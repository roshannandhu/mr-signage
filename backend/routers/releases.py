import json
import logging
from datetime import timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from .. import models, rollout, schemas
from ..tenancy import TenantScope, get_tenant_scope, require_tenant_roles

logger = logging.getLogger(__name__)

router = APIRouter()

# An AppRelease is platform-wide: `current_app_version` falls back to the highest
# version_code in this table for *every* screen in *every* tenant. Publishing one is
# therefore not a tenant-level action -- a release created by one organisation's owner
# would install itself across the whole fleet, and on a device-owner TV it installs
# silently. Creation is restricted to `super_admin` for that reason.
#
# Reading stays open to any authenticated member: the dashboard's staged-rollout table
# needs the version list to label which build each of *its own* screens is pinned to.


@router.get("/", response_model=List[schemas.AppReleaseResponse])
def list_releases(scope: TenantScope = Depends(get_tenant_scope)):
    return (
        scope.db.query(models.AppRelease)
        .order_by(models.AppRelease.version_code.desc())
        .all()
    )


@router.post("/", response_model=schemas.AppReleaseResponse, status_code=201)
def create_release(
    data: schemas.AppReleaseCreate,
    scope: TenantScope = Depends(require_tenant_roles("super_admin")),
):
    release = models.AppRelease(
        version_code=data.version_code,
        version_name=data.version_name,
        apk_url=data.apk_url,
        sha256=data.sha256.lower(),
        mandatory=data.mandatory,
        rollout_state=data.rollout_state,
    )
    scope.db.add(release)
    try:
        scope.db.commit()
    except IntegrityError:
        # version_code is unique; re-publishing one would silently repoint every screen
        # already pinned to it at a different APK.
        scope.db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Version code {data.version_code} already exists",
        )
    scope.db.refresh(release)
    logger.info(
        "Release %s (%s) published by %s",
        release.version_code,
        release.version_name,
        scope.user.username,
    )
    return release


@router.patch("/{version_code}", response_model=schemas.AppReleaseResponse)
def promote_release(
    version_code: int,
    data: schemas.AppReleasePatch,
    scope: TenantScope = Depends(require_tenant_roles("super_admin")),
):
    """Move a build along the rollout ring: draft -> canary -> released.

    Promoting to "released" is the moment a build becomes live for every screen that has
    no explicit pin, so it carries the same authority as publishing one.
    """
    release = (
        scope.db.query(models.AppRelease)
        .filter(models.AppRelease.version_code == version_code)
        .first()
    )
    if not release:
        raise HTTPException(status_code=404, detail="Release not found")
    if data.rollout_state == rollout.RELEASED and not release.sha256:
        # Legacy rows predate the mandatory digest. The player refuses to install an
        # unpinned APK, so promoting one fleet-wide would only produce 500 failed
        # installs; refusing here says why instead.
        raise HTTPException(
            status_code=422,
            detail="Release has no sha256 digest and cannot be promoted; re-publish it with one",
        )
    previous = release.rollout_state
    release.rollout_state = data.rollout_state
    scope.db.commit()
    scope.db.refresh(release)
    logger.info(
        "Release %s moved %s -> %s by %s",
        release.version_code,
        previous,
        release.rollout_state,
        scope.user.username,
    )
    return release


class ScreenUpdateRequest(BaseModel):
    # A build to pin this screen to. Omitted means "the latest released build", which also
    # clears any earlier pin so the screen goes back to following the fleet.
    version_code: Optional[int] = None


class ScreenUpdateResult(BaseModel):
    screen_id: int
    name: Optional[str] = None
    app_version: Optional[str] = None
    target_version_code: Optional[int] = None
    offered_version_code: Optional[int] = None
    offered_version_name: Optional[str] = None
    already_current: bool
    online: bool


@router.post("/screens/{screen_id}/update", response_model=ScreenUpdateResult)
async def update_screen_now(
    screen_id: int,
    data: ScreenUpdateRequest | None = None,
    scope: TenantScope = Depends(require_tenant_roles("super_admin")),
):
    """Make one TV look for its update now instead of on its own schedule.

    Screens update themselves when a release is published, but only when they next hear
    about it -- and one that failed, was offline, or was left with a dismissed prompt had no
    way to be told again short of publishing another build. This is the operator's lever for
    a single screen.

    Three deliveries, so it lands whichever build and connection the screen has:
      * the screen's sync marker moves, so its next sync returns the full body carrying the
        offer instead of a 204 -- this alone works on every player ever shipped;
      * a queued `check_update` clears the player's retry backoff and, from 1.0.13, is
        picked up by the 15-second heartbeat and syncs immediately;
      * a push over the screen's socket, when it holds one, syncs it at once.
    """
    db = scope.db
    # Platform-wide on purpose: the operator is not acting inside any one tenant.
    screen = (
        db.query(models.Screen)
        .filter(models.Screen.id == screen_id, models.Screen.deleted_at.is_(None))
        .first()
    )
    if not screen or not screen.device_id:
        raise HTTPException(status_code=404, detail="Screen not found")

    version_code = data.version_code if data else None
    if version_code is not None:
        release = (
            db.query(models.AppRelease)
            .filter(models.AppRelease.version_code == version_code)
            .first()
        )
        if not release:
            raise HTTPException(status_code=422, detail=f"No release with version_code {version_code}")
        if not release.sha256:
            raise HTTPException(status_code=422, detail="That release has no sha256 and cannot be installed")
    # Also resets the failure count, so a screen that had given up on a build (rolled back
    # after repeated failures) is allowed to try again when a person asks it to.
    rollout.repin(screen, version_code)
    screen.assignment_updated_at = models.utcnow()
    db.commit()
    db.refresh(screen)

    from ..services import current_app_version, queue_device_command

    offered = current_app_version(
        db, screen.target_version_code or (screen.group.target_version_code if screen.group else None)
    )
    await queue_device_command(screen.device_id, "check_update")
    try:
        from .websockets import broadcast_in_memory

        await broadcast_in_memory(
            f"screen:{screen.device_id}",
            json.dumps({"type": "sync_now", "device_id": screen.device_id}),
        )
    except Exception as exc:  # noqa: BLE001 - the queued command still delivers it
        logger.warning("Could not push sync_now to screen %s: %s", screen.id, exc)

    online = screen.last_seen is not None and (
        models.utcnow() - screen.last_seen
    ) <= timedelta(seconds=150)
    logger.info(
        "Update requested for screen %s (pin=%s, offering %s) by %s",
        screen.id, screen.target_version_code, offered.version_code, scope.user.username,
    )
    return ScreenUpdateResult(
        screen_id=screen.id,
        name=screen.name,
        app_version=screen.app_version,
        target_version_code=screen.target_version_code,
        offered_version_code=offered.version_code,
        offered_version_name=offered.version_name,
        already_current=bool(screen.app_version and screen.app_version == offered.version_name),
        online=online,
    )
