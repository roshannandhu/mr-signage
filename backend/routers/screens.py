import html
import json
import logging
import os
import random
import string
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Literal

import boto3
from fastapi import APIRouter, Depends, HTTPException, Response, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import database, models, schemas
from ..limiter import limiter
from .. import google_device
from ..media_selection import select_rendition
from . import playlists as playlists_router
from .. import rollout
from ..rotation import normalise as normalise_rotation, resolve_rotation
from ..maps_link import MapsLinkError, parse as parse_maps_link
from ..media_urls import is_s3_enabled, media_base_url, resolve_media_url
from ..tenancy import TenantScope, get_tenant_scope, require_tenant_roles

logger = logging.getLogger(__name__)

router = APIRouter()
security = HTTPBearer(auto_error=False)

s3_client = boto3.client(
    "s3",
    endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION", "auto"),
)


def public_base_url(request: Request | None = None) -> str:
    """The address a phone or a TV can actually reach this API on."""
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured and not ("localhost" in configured or "127.0.0.1" in configured):
        return configured

    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if render_url:
        return render_url

    if request is not None:
        forwarded_proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
        scheme = forwarded_proto or request.url.scheme or "http"
        host = request.headers.get("host") or request.url.netloc
        if host and not ("127.0.0.1" in host or "localhost" in host):
            return f"{scheme}://{host}"

    return "https://olrac-signage-32lh.onrender.com"


from ..services import (
    generate_pair_code,
    as_aware_utc,
    current_app_version,
    player_sync_interval_seconds,
    screen_offline_after_seconds,
    queue_device_command,
    pop_device_command,
    park_device_secret,
    collect_device_secret,
    legacy_device_auth_allowed,
    token_is_for_device,
    verify_device_auth,
    issue_device_secret,
)
from ..services.device_auth_service import DEVICE_TOKEN_TYPE
from ..billing import serving_block_reason



@router.get("/auth-methods")
def auth_methods():
    """Which sign-in routes this deployment actually offers.

    google_device.is_configured() existed from the start and was read in exactly one
    place -- to answer /google/start with a 503. Nothing ever told the TV, so the player
    always drew a "Sign in with Google" button, and on a deployment with no Google
    credentials pressing it could only ever produce an error. The intent to gate it was
    written down in google_device's docstring and never implemented; this is that missing
    half.

    Unauthenticated on purpose: it is read by a TV that has no credential yet, and it
    discloses nothing but which buttons to draw.
    """
    return {
        # The real answer, not a hardcoded True. With no Google credentials this route can
        # only ever answer /google/start with a 503, so the player drew a button whose
        # single outcome was an error -- and, worse, on the dev path it bound the screen to
        # an arbitrary organisation (see google_device_poll).
        "google": google_device.is_configured() or google_device.is_web_configured(),
        # Always available. Listed rather than assumed so the player renders from one
        # answer instead of hard-coding two of the three.
        "password": True,
        "pair_code": True,
    }


def presents_valid_device_token(device_id: str, credentials: HTTPAuthorizationCredentials | None) -> bool:
    """Whether this caller already holds a working credential for this screen."""
    if not credentials:
        return False
    try:
        from jose import JWTError, jwt

        from .auth import ALGORITHM, get_secret_key

        payload = jwt.decode(credentials.credentials, get_secret_key(), algorithms=[ALGORITHM])
        return token_is_for_device(payload, device_id)
    except Exception:  # noqa: BLE001 - expired or malformed is simply "no"
        return False


# How long a pairing code stays valid. Rotation is deliberate; the player shows a countdown.
#
# The player's PAIR_CODE_REFRESH_MS must be strictly SHORTER than this. Both were sixty
# seconds, so the code expired at the same instant the TV asked for a new one -- and because
# the player polls every few seconds, the six digits on screen were an expired code for up to
# five seconds of every minute. Typing them then earns "Invalid pairing code", which is
# exactly what was reported.
#
# Raised from one minute to five: one minute was still not long enough to read six digits off
# a television, walk to a laptop, open the Screens page and type them -- especially when the
# screen being paired is on a wall in another room.
#
# tests/test_pairing_code_window.py reads the player's constant and fails if the margin is
# lost again, because nothing else connects a Python timedelta to a Kotlin Long.
PAIR_CODE_TTL = timedelta(minutes=5)

UNIVERSAL_PIN_KEY = "universal_maintenance_pin"


def effective_maintenance_pin(db: Session, screen) -> str | None:
    """The screen's own maintenance PIN.

    Every screen has one: the column is NOT NULL with a random default, deliberately per
    screen so that one leaked code does not open the whole fleet. This is the code a TENANT
    uses on their own wall.
    """
    return (getattr(screen, "maintenance_pin", None) or "").strip() or None


def master_maintenance_pin(db: Session) -> str | None:
    """The platform operator's master code, which opens ANY screen.

    A second code rather than a replacement for the per-screen one. Those answer different
    needs and collapsing them loses both: a tenant wants a code for their own wall, and the
    operator supporting a panel they have never seen cannot be reading a different four digits
    off the dashboard for every screen -- least of all when the reason they are there is that
    the screen has no network and the dashboard shows nothing useful.

    Held by the operator alone, so it is not shown to tenants. Per-screen containment is
    unchanged: a leaked tenant PIN still opens exactly one screen.
    """
    setting = db.query(models.SystemSetting).filter(
        models.SystemSetting.key == UNIVERSAL_PIN_KEY
    ).first()
    return ((setting.value if setting else "") or "").strip() or None


@router.post("/register", response_model=schemas.RegisterResponse)
# Unauthenticated, and it both creates rows and can hand back a credential, so it needs a
# ceiling. Sized like /auth for the same reason: a site coming back after a power cut
# re-registers every panel at once through one public IP.
@limiter.limit("120/minute")
async def register_tv(
    request: Request,
    req: schemas.RegisterRequest,
    db: Session = Depends(database.get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
):
    # Both lookups exclude archived rows, so a panel whose screen was removed comes back
    # as a genuinely new one and shows a fresh pairing code. Without this the operator
    # deletes a TV, the TV re-registers, and the row they just removed reappears.
    # 1. Match by device_id
    db_screen = (
        db.query(models.Screen)
        .filter(models.Screen.device_id == req.device_id, models.Screen.deleted_at.is_(None))
        .first()
    )

    # 2. Match by installation_id if device_id didn't match (e.g. after reinstall/reset)
    if not db_screen and req.installation_id:
        db_screen = (
            db.query(models.Screen)
            .filter(
                models.Screen.installation_id == req.installation_id,
                models.Screen.deleted_at.is_(None),
            )
            .order_by(models.Screen.last_seen.desc().nullslast(), models.Screen.id.desc())
            .first()
        )
        if db_screen:
            logger.info(
                "Restoring screen %s (Name: %s, Org: %s) for installation %s (reclaimed device_id %s -> %s)",
                db_screen.id, db_screen.name, db_screen.organization_id, req.installation_id, db_screen.device_id, req.device_id
            )
            db_screen.device_id = req.device_id

    # 3. If screen is already paired / configured, keep it active and update telemetry
    if db_screen:
        # Captured BEFORE the overwrite below, because it is the proof the re-issue path
        # checks against and assigning first would make every caller match itself.
        known_installation_id = db_screen.installation_id
        if req.installation_id:
            db_screen.installation_id = req.installation_id
        if req.identity_source:
            db_screen.identity_source = req.identity_source
        if req.device_model:
            db_screen.model = req.device_model
        db_screen.last_seen = models.utcnow()
        if db_screen.status != "waiting_pairing":
            # The screen is claimed and is coming back. Two ways it may need a credential:
            # one was parked for it when an operator redeemed its pairing code, or it lost
            # the one it had.
            issued = await collect_device_secret(db_screen.device_id)

            if issued is None and not presents_valid_device_token(req.device_id, credentials):
                # Reinstalling the app, or clearing its data, wipes SharedPreferences --
                # including the device secret -- while device_id survives, because it is
                # derived from ANDROID_ID rather than stored. So a returning screen is
                # recognised but can no longer authenticate, and without re-issuing here it
                # would be stuck on the legacy path for good: the moment
                # ALLOW_LEGACY_DEVICE_AUTH is turned off, every reinstalled TV goes dark
                # and needs a site visit.
                #
                # This used to re-issue on possession of the device id alone, reasoning that
                # every other unauthenticated device route already accepts that much. It does
                # not hold: those routes only ever let a caller ACT as the screen while the
                # legacy path is open, whereas this one mints a durable credential and
                # overwrites the stored hash -- so it both handed the fleet to anyone who
                # read a device id off the dashboard or a log line, and locked the real panel
                # out at the same time. It also survived ALLOW_LEGACY_DEVICE_AUTH=false, which
                # left that flag with no end state: the migration it stages could never
                # actually close.
                #
                # The installation id is the hardware identity (serial, else ANDROID_ID), so
                # a genuinely reinstalled panel always presents the one already on file while
                # a remote caller holding only a device id cannot. That is the proof now
                # required.
                recognised = (
                    req.installation_id is not None
                    and known_installation_id is not None
                    and req.installation_id == known_installation_id
                )
                if recognised:
                    issued = issue_device_secret(db_screen)
                    logger.info(
                        "Re-issued device credential to screen %s (device %s) after reinstall",
                        db_screen.id, db_screen.device_id,
                    )
                elif known_installation_id is None and legacy_device_auth_allowed():
                    # A screen enrolled before installation_id was recorded has no hardware
                    # identity to match, so during the rollout it keeps the old behaviour and
                    # backfills one. Tied to the legacy flag on purpose: turning that off is
                    # what finally closes this branch, which is what makes the flag mean
                    # something.
                    issued = issue_device_secret(db_screen)
                    logger.warning(
                        "Re-issued device credential to screen %s (device %s) with no stored "
                        "installation id (legacy path)",
                        db_screen.id, db_screen.device_id,
                    )
                else:
                    # Not fatal: the screen still registers and still plays on the legacy
                    # path if it is open. It simply is not handed a new credential.
                    logger.warning(
                        "Refused to re-issue a device credential for screen %s (device %s): "
                        "installation id did not match",
                        db_screen.id, db_screen.device_id,
                    )

            db.commit()
            db.refresh(db_screen)
            response = schemas.RegisterResponse.model_validate(db_screen)
            response.device_secret = issued
            return response
    else:
        # Model and manufacturer recorded from the first contact, not left until the screen
        # is claimed and starts sending heartbeats. An operator redeeming a pairing code is
        # looking at a list of TVs that have not been named yet, and "Lenovo TB-8505F" is the
        # only thing that distinguishes one waiting panel from another.
        db_screen = models.Screen(
            device_id=req.device_id,
            installation_id=req.installation_id,
            identity_source=req.identity_source,
            model=req.device_model,
            manufacturer=req.manufacturer,
            status="waiting_pairing"
        )
        db.add(db_screen)

    now = models.utcnow()
    # If the screen already has an unexpired pairing code, reuse it so we don't
    # invalidate the code shown on TV during polling or network retries.
    if not (db_screen.pair_code and db_screen.pair_code_expires_at and db_screen.pair_code_expires_at > now):
        code = generate_pair_code()
        while db.query(models.Screen).filter(models.Screen.pair_code == code, models.Screen.deleted_at.is_(None)).first():
            code = generate_pair_code()
        db_screen.pair_code = code
        db_screen.pair_code_expires_at = now + PAIR_CODE_TTL
    db.commit()
    db.refresh(db_screen)
    return db_screen


def ensure_screen_quota(db: Session, organization_id: int, action: str) -> None:
    """Reject the caller when the organisation is already at its screen limit.

    Two limits are checked in order:
    1. Per-org admin quota (org.max_screens > 0 → hard limit set by Super Admin).
    2. Plan-level limit (plan.max_screens) as a secondary fallback.

    Every path that binds a new screen to an organisation has to call this, or that path
    becomes a quota bypass: unlimited screens on a limited plan. An organisation with no
    plan and no admin quota is deliberately unbounded (0 = unlimited).
    """
    organization = db.query(models.Organization).filter(
        models.Organization.id == organization_id
    ).one()

    # Archived screens do not occupy a slot -- removing a TV has to actually free the
    # quota it was using, or the operator deletes one and still cannot add another.
    screen_count = db.query(models.Screen).filter(
        models.Screen.deleted_at.is_(None),
        models.Screen.organization_id == organization_id,
        models.Screen.status != "waiting_pairing",
    ).count()

    # One number, from Organization.effective_max_screens: the admin override when one is
    # set, otherwise the package's limit. This used to be two branches reading two columns,
    # and the first branch `return`ed on a 0 override -- which is every organisation in
    # practice, so the package limit was reached only by falling through, and a tenant with
    # no package at all was silently unlimited.
    limit = organization.effective_max_screens
    if limit is None:
        return  # no package and no override → deliberately unbounded

    if screen_count >= limit:
        plan_name = organization.plan.name if organization.plan else None
        where = f" on {plan_name}" if plan_name and not organization.max_screens else ""
        raise HTTPException(
            status_code=409,
            detail=(
                f"Screen limit reached ({screen_count}/{limit}{where}). "
                f"Upgrade the package or raise this workspace's limit to {action}."
            ),
        )


@router.post("/pair", response_model=schemas.ScreenResponse)
async def pair_screen(
    req: schemas.PairRequest,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    db_screen = scope.db.query(models.Screen).filter(models.Screen.pair_code == req.pair_code).first()
    if not db_screen:
        raise HTTPException(status_code=404, detail="Invalid pairing code")
    if db_screen.organization_id not in (None, scope.organization_id):
        raise HTTPException(status_code=404, detail="Invalid pairing code")
    if db_screen.status != "waiting_pairing":
        raise HTTPException(status_code=400, detail="Screen already paired")
    if db_screen.pair_code_expires_at and db_screen.pair_code_expires_at < models.utcnow():
        raise HTTPException(status_code=400, detail="Pairing code expired")

    # Anti-duplicate hardware check: If the same physical TV was previously paired in this organization,
    # reclaim the existing screen record instead of creating a duplicate ghost screen.
    existing_screen = None
    if db_screen.installation_id:
        existing_screen = (
            scope.db.query(models.Screen)
            .filter(
                models.Screen.installation_id == db_screen.installation_id,
                models.Screen.organization_id == scope.organization_id,
                models.Screen.deleted_at.is_(None),
                models.Screen.id != db_screen.id,
            )
            .first()
        )

    # A factory reset defeats installation_id: on a TV that hides its serial it is derived
    # from ANDROID_ID, and a reset changes ANDROID_ID, so the same panel returns as a stranger.
    # Fall back to the hardware model -- if this workspace has exactly one non-live screen of
    # the same model, this is almost certainly that TV coming back, so reconnect it rather than
    # leave a duplicate. Exactly one, and never a screen that is still online, so two identical
    # TVs are never merged into each other; with two offline look-alikes we cannot tell which
    # this is and leave it as a new screen. Full, unambiguous recovery needs the serial, which
    # only a device-owner install can read.
    if not existing_screen and db_screen.model:
        same_model = (
            scope.db.query(models.Screen)
            .filter(
                models.Screen.organization_id == scope.organization_id,
                models.Screen.model == db_screen.model,
                models.Screen.deleted_at.is_(None),
                models.Screen.status.notin_(("online", "waiting_pairing")),
                models.Screen.id != db_screen.id,
            )
            .all()
        )
        if len(same_model) == 1:
            existing_screen = same_model[0]
            logger.info(
                "Reclaiming screen %s (%s) by model after a factory reset changed its identity "
                "(installation_id %s -> %s)",
                existing_screen.id, existing_screen.name, existing_screen.installation_id, db_screen.installation_id,
            )

    if existing_screen:
        logger.info(
            "Reclaiming existing screen %s (%s) on pairing reinstalled hardware (installation_id: %s, new device_id: %s)",
            existing_screen.id, existing_screen.name, db_screen.installation_id, db_screen.device_id
        )
        # Avoid Postgres unique constraint collision between db_screen and existing_screen
        dev_id = db_screen.device_id
        db_screen.device_id = None
        scope.db.delete(db_screen)
        scope.db.flush()

        existing_screen.device_id = dev_id
        if db_screen.model:
            existing_screen.model = db_screen.model
        if db_screen.manufacturer:
            existing_screen.manufacturer = db_screen.manufacturer
        existing_screen.status = "online"
        existing_screen.last_seen = models.utcnow()
        existing_screen.pair_code = None
        existing_screen.pair_code_expires_at = None
        existing_screen.assignment_updated_at = models.utcnow()

        device_secret = issue_device_secret(existing_screen)

        scope.db.commit()
        scope.db.refresh(existing_screen)

        # A screen without its own playlist shows an empty detail page. Provision one
        # now so the operator sees the content library and timeline immediately.
        if not existing_screen.playlist_id:
            _playlist = models.Playlist(
                organization_id=existing_screen.organization_id,
                name=f"{existing_screen.name or f'Screen {existing_screen.id}'} loop",
            )
            scope.db.add(_playlist)
            scope.db.flush()
            existing_screen.playlist_id = _playlist.id
            scope.db.commit()
            scope.db.refresh(existing_screen)

        # Parked for the TV to collect on its next /register poll; this response goes to
        # the operator's dashboard, not to the screen.
        await park_device_secret(existing_screen.device_id, device_secret)
        response = schemas.ScreenResponse.model_validate(existing_screen)
        response.device_secret = device_secret
        return response

    ensure_screen_quota(scope.db, scope.organization_id, "pair another screen")

    db_screen.status = "online"
    db_screen.last_seen = models.utcnow()
    db_screen.organization_id = scope.organization_id
    db_screen.approved_at = models.utcnow()
    if not db_screen.name or db_screen.name.startswith("Screen "):
        if db_screen.model:
            m = (db_screen.manufacturer or "").strip()
            mod = db_screen.model.strip()
            db_screen.name = f"{m} {mod}".strip() if m and not mod.lower().startswith(m.lower()) else mod
        else:
            db_screen.name = f"Screen {db_screen.pair_code}"
    db_screen.pair_code = None
    db_screen.pair_code_expires_at = None
    db_screen.assignment_updated_at = models.utcnow()
    device_secret = issue_device_secret(db_screen)
    scope.db.commit()
    scope.db.refresh(db_screen)

    # Provision the screen's own playlist so the detail page shows the content
    # library and playback timeline from the first visit, not "Nothing scheduled".
    if not db_screen.playlist_id:
        _playlist = models.Playlist(
            organization_id=db_screen.organization_id,
            name=f"{db_screen.name or f'Screen {db_screen.id}'} loop",
        )
        scope.db.add(_playlist)
        scope.db.flush()
        db_screen.playlist_id = _playlist.id
        scope.db.commit()
        scope.db.refresh(db_screen)

    await park_device_secret(db_screen.device_id, device_secret)
    response = schemas.ScreenResponse.model_validate(db_screen)
    response.device_secret = device_secret
    return response


def bind_screen_to_org(
    db: Session,
    *,
    device_id: str,
    user: models.User,
    name: str | None,
    conflict_error: HTTPException,
    how: str,
    installation_id: str | None = None,
    model: str | None = None,
    manufacturer: str | None = None,
) -> models.Screen:
    """Claim `device_id` or `installation_id` for `user`'s organisation, auto-reclaiming existing hardware."""
    if user.role not in ("owner", "editor", "super_admin"):
        raise HTTPException(status_code=403, detail="This account cannot add screens")
    if not user.organization_id:
        raise HTTPException(status_code=403, detail="This account has no workspace")

    # Anti-duplicate hardware search:
    # 1. Match by persistent installation_id (survives app reinstalls)
    # 2. Or match by device_id
    screen = None
    if installation_id:
        screen = db.query(models.Screen).filter(
            (models.Screen.installation_id == installation_id) &
            (models.Screen.organization_id == user.organization_id)
        ).first()

    if not screen:
        screen = db.query(models.Screen).filter(models.Screen.device_id == device_id, models.Screen.deleted_at.is_(None)).first()

    # If an existing screen with installation_id was found, clean up any transient placeholder holding device_id
    if screen and screen.device_id != device_id:
        temp_screen = db.query(models.Screen).filter(
            models.Screen.device_id == device_id,
            models.Screen.id != screen.id
        ).first()
        if temp_screen:
            db.delete(temp_screen)
            db.flush()

    if screen and screen.organization_id not in (None, user.organization_id):
        logger.warning(
            "Rejected cross-tenant claim for device %s (installation %s): owned by org %s, user belongs to org %s",
            device_id, installation_id, screen.organization_id, user.organization_id,
        )
        raise conflict_error

    # A screen that is new or still waiting to be paired is not yet counted against the
    # plan. Re-claiming a device this organisation already owns is not a new screen.
    if not screen or screen.status == "waiting_pairing":
        ensure_screen_quota(db, user.organization_id, "add another screen")

    if not screen:
        screen = models.Screen(device_id=device_id)
        db.add(screen)

    screen.device_id = device_id
    if installation_id:
        screen.installation_id = installation_id
    if model:
        screen.model = model
    if manufacturer:
        screen.manufacturer = manufacturer

    screen.organization_id = user.organization_id
    screen.approved_at = models.utcnow()
    screen.status = "online"
    screen.last_seen = models.utcnow()

    # Hardware Model based auto-naming
    detected_name = None
    if model:
        m = (manufacturer or "").strip()
        mod = model.strip()
        if m and not mod.lower().startswith(m.lower()):
            detected_name = f"{m} {mod}"
        else:
            detected_name = mod

    if name and name.strip():
        screen.name = name.strip()
    elif not screen.name or screen.name.startswith("Screen "):
        screen.name = detected_name or screen.name or f"Screen {device_id[:6]}"

    # Drop any code minted by an earlier /register: the screen is claimed now
    screen.pair_code = None
    screen.pair_code_expires_at = None
    screen.assignment_updated_at = models.utcnow()

    # Rotated on every bind, exactly as /enroll does. The plaintext is stashed on the
    # instance (not a column) so the calling route can hand it to the device once; it is
    # never persisted and never reloaded.
    device_secret = issue_device_secret(screen)

    db.commit()
    db.refresh(screen)

    # Every screen should have its own playlist from the moment it is claimed, so the
    # operator never lands on "Nothing scheduled yet". This covers sign-in, Google JWT
    # and Google OAuth flows.
    if not screen.playlist_id:
        _playlist = models.Playlist(
            organization_id=screen.organization_id,
            name=f"{screen.name or f'Screen {screen.id}'} loop",
        )
        db.add(_playlist)
        db.flush()
        screen.playlist_id = _playlist.id
        db.commit()
        db.refresh(screen)

    screen.issued_device_secret = device_secret
    logger.info("Device %s (model: %s) %s to org %s by %s", device_id, screen.model, how, user.organization_id, user.username)
    return screen

@router.post("/sign-in", response_model=schemas.ScreenResponse)
# Matches the reasoning on google/start below: an installer bringing up a room of twenty
# screens is one IP making twenty legitimate attempts in a few minutes, and a five-per-
# minute cap fails exactly the install it is meant to protect. Still tight enough to make
# password guessing through this route pointless.
@limiter.limit("30/minute")
def sign_in_screen(
    request: Request,
    req: schemas.ScreenSignInRequest, 
    db: Session = Depends(database.get_db)
):
    """Bind this device to the organisation of whoever signs in on the TV.

    The pairing-code flow needs a second person at a dashboard within five minutes. If the
    installer already has an account, signing in on the TV proves the same thing. The
    device holds no credential yet, so every guard that /pair gets for free from
    require_tenant_roles has to be applied by hand here.

    Deliberately does not issue a device secret: this binds a screen exactly as /pair does,
    so device auth stays optional (see verify_device_auth) and the player needs no token
    handling. /enroll remains the path for devices that should carry a real secret.
    """
    from .auth import verify_password

    # One error for every credential failure — never reveal whether the username exists,
    # the account is disabled, or only the password was wrong.
    CREDENTIALS_ERROR = HTTPException(status_code=401, detail="The username or password is incorrect")

    # Matched case-insensitively, exactly as /api/auth/token does. Case-sensitive matching
    # here meant a TV rejected the very credentials the dashboard had just accepted.
    login_id = (req.username or "").strip().lower()
    user = db.query(models.User).filter(
        (func.lower(models.User.username) == login_id)
        | (func.lower(models.User.email) == login_id)
    ).first()
    if not user or not user.is_active or not verify_password(req.password, user.hashed_password):
        raise CREDENTIALS_ERROR

    screen = bind_screen_to_org(
        db,
        device_id=req.device_id,
        user=user,
        name=req.name,
        installation_id=req.installation_id,
        model=req.model,
        manufacturer=req.manufacturer,
        conflict_error=CREDENTIALS_ERROR,
        how="signed in",
    )
    response = schemas.ScreenResponse.model_validate(screen)
    response.device_secret = getattr(screen, "issued_device_secret", None)
    return response


# The poll token is minted and read only here, so the marker is local rather than shared.
GOOGLE_POLL_TYPE = "google_poll"


@router.post("/google/start", response_model=schemas.GoogleDeviceStartResponse)
# Loose on purpose. Each call spends a Google device-code quota unit, so it cannot be
# unlimited -- but a whole site sits behind one NAT address, and an installer bringing up
# a room of twenty screens is one IP making twenty legitimate calls in a few minutes. A
# tight per-IP cap here would fail exactly the install it is meant to protect.
@limiter.limit("30/minute")
def google_device_start(
    request: Request,
    req: schemas.GoogleDeviceStartRequest,
):
    """Begin a Google sign-in for this TV and hand back the code to put on screen."""
    from .auth import create_access_token

    if not google_device.is_configured():
        # No credentials means no Google sign-in. This used to mint a fake "TV-1234" code
        # and a poll token flagged is_dev, which google_device_poll then honoured by
        # binding the screen to `email == <a hardcoded gmail address> OR role == "owner"`
        # -- in practice whichever owner the database returned first, in someone else's
        # organisation. /auth-methods now reports google=false so the player hides the
        # button; this is the backstop for a client that asks anyway.
        raise HTTPException(status_code=503, detail="Google sign-in is not enabled on this server")

    try:
        started = google_device.start()
    except google_device.GoogleError as error:
        logger.warning("Google device-code request failed for %s: %s", req.device_id, error)
        raise HTTPException(status_code=502, detail="Could not reach Google. Try again.")

    poll_token = create_access_token(
        {
            "sub": f"device:{req.device_id}",
            "typ": GOOGLE_POLL_TYPE,
            "dc": started["device_code"],
            "nm": req.name,
            "iid": req.installation_id,
            "mod": req.model,
            "man": req.manufacturer,
        },
        expires_delta=timedelta(seconds=started["expires_in"]),
    )

    logger.info("Started Google sign-in for device %s", req.device_id)
    return schemas.GoogleDeviceStartResponse(
        user_code=started["user_code"],
        verification_url=started["verification_url"],
        interval=started["interval"],
        expires_in=started["expires_in"],
        poll_token=poll_token,
    )


@router.post("/google/poll", response_model=schemas.GoogleDevicePollResponse)
def google_device_poll(
    req: schemas.GoogleDevicePollRequest,
    db: Session = Depends(database.get_db),
):
    """Has the installer approved on their phone yet? If so, bind the screen."""
    from jose import JWTError, jwt

    from .auth import ALGORITHM, get_secret_key

    INVALID = HTTPException(status_code=401, detail="This sign-in has expired. Start again on the TV.")

    try:
        claims = jwt.decode(req.poll_token, get_secret_key(), algorithms=[ALGORITHM])
    except JWTError:
        raise INVALID
    if claims.get("typ") != GOOGLE_POLL_TYPE:
        raise INVALID

    device_id = str(claims.get("sub") or "").removeprefix("device:")
    device_code = claims.get("dc")
    if not device_id or not device_code:
        raise INVALID

    try:
        result = google_device.poll(device_code)
    except google_device.GoogleError as error:
        logger.warning("Google poll failed for device %s: %s", device_id, error)
        raise HTTPException(status_code=502, detail="Could not reach Google. Try again.")

    if result["status"] != "ok":
        # Google has nothing for us. Before reporting that back, check whether this panel
        # got bound by some OTHER route in the meantime.
        #
        # google_device.poll() asks GOOGLE about this device code. The browser flow on the
        # TV (google/oauth-callback) is a separate OAuth grant that never approves this
        # code, so a panel signing in through its own browser sat on "authorization_pending"
        # until the code expired -- while the screen was already bound and the TV's browser
        # was showing "Display Connected".
        #
        # Closing that gap was left entirely to the olrac:// deep link back into the app,
        # and a TV browser is free to refuse it ("App deeplink blocked" on TCL), which
        # stranded the panel on a sign-in screen it had already passed. MainActivity starts
        # this poll alongside opening the browser, so the binding is reported on the next
        # tick instead and the deep link becomes an accelerator, not the only way through.
        # An operator redeeming a pairing code mid-flow lands here for the same reason.
        #
        # Deliberately only on a NON-ok result. Every authorisation check below -- the
        # tenant boundary above all -- runs whenever Google actually returns an identity,
        # and must keep running: a rival approving on their own phone has to be refused,
        # not handed the screen this device is already bound to.
        #
        # It reports a binding, it never creates one, and it reaches no further than
        # /register already does for the same device_id. No device secret: there is no new
        # grant to hand over, which is also why the deep-link path carries none.
        already_bound = (
            db.query(models.Screen)
            .filter(
                models.Screen.device_id == device_id,
                models.Screen.deleted_at.is_(None),
                models.Screen.organization_id.isnot(None),
                models.Screen.status != "waiting_pairing",
            )
            .first()
        )
        if already_bound:
            return schemas.GoogleDevicePollResponse(
                status="bound",
                screen=schemas.ScreenResponse.model_validate(already_bound),
            )
        return schemas.GoogleDevicePollResponse(status=result["status"])

    email = result.get("email") or ""
    if not email or not result.get("email_verified"):
        raise HTTPException(
            status_code=403,
            detail="That Google account has no verified email address.",
        )

    user = db.query(models.User).filter(func.lower(models.User.email) == email).first()
    if not user or not user.is_active:
        logger.warning("Google sign-in for device %s: no active account for that address", device_id)
        raise HTTPException(
            status_code=403,
            detail=(
                "No MR Signage account uses that Google address. Add it to your profile in the "
                "dashboard under Account, then try again."
            ),
        )

    screen = bind_screen_to_org(
        db,
        device_id=device_id,
        user=user,
        name=claims.get("nm"),
        installation_id=claims.get("iid"),
        model=claims.get("mod"),
        manufacturer=claims.get("man"),
        conflict_error=HTTPException(
            status_code=403,
            detail="This screen belongs to another workspace.",
        ),
        how="signed in with Google",
    )
    bound = schemas.ScreenResponse.model_validate(screen)
    bound.device_secret = getattr(screen, "issued_device_secret", None)
    return schemas.GoogleDevicePollResponse(status="bound", screen=bound)


@router.get("/google/oauth-url")
def get_tv_google_oauth_url(
    device_id: str,
    name: Optional[str] = None,
    installation_id: Optional[str] = None,
    model: Optional[str] = None,
    manufacturer: Optional[str] = None,
    request: Request = None,
):
    """Generate a direct Google OAuth URL for Android TV / Custom Tab login."""
    from .auth import create_access_token

    if not google_device.is_web_configured():
        raise HTTPException(status_code=503, detail="Google sign-in is not enabled on this server")

    redirect_uri = f"{public_base_url(request)}/api/screens/google/oauth-callback"
    state_token = create_access_token(
        {
            "sub": f"device:{device_id}",
            "typ": "tv_oauth",
            "nm": name,
            "iid": installation_id,
            "mod": model,
            "man": manufacturer,
        },
        expires_delta=timedelta(minutes=30),
    )
    return {
        "oauth_url": google_device.build_oauth_url(redirect_uri, state=state_token),
        "redirect_uri": redirect_uri,
    }


# The player's package, so an intent: URL can name it and Chrome launches it directly
# instead of bouncing to the Play Store. Matches applicationId in
# android-tv/app/build.gradle.kts; there is no build-type suffix, so one value covers both.
TV_APP_PACKAGE = "com.olrac.signage"


def _android_intent_url(deep_link: str) -> str:
    """Rewrite an "olrac://" deep link into the intent: form a browser will actually open.

    The player registers the custom scheme (AndroidManifest, scheme="olrac" host="auth",
    BROWSABLE) and MainActivity.onNewIntent handles it -- but none of that is ever reached,
    because Chrome refuses to resolve a scheme it does not know and fails the navigation
    with ERR_UNKNOWN_URL_SCHEME. Chrome is what a Custom Tab is, and what every Android TV
    browser is, so this failed on the panel while working anywhere it was tested by hand.

    That is the "Web page not available" a TV shows at the very end of Google sign-in: the
    account is authenticated and the screen is bound server side, the sign-in genuinely
    succeeded, and the panel simply never hears about it and sits on the sign-in screen.

    The intent: syntax is the documented way to hand a browser navigation to an app. The
    intent it builds carries the same "olrac://auth/..." data, so the manifest filter that
    is already shipped matches it and no APK rebuild is needed -- this is a backend deploy.
    """
    parts = urllib.parse.urlsplit(deep_link)
    # http(s) is already something a browser can open; only a custom scheme needs this.
    if not parts.scheme or parts.scheme in ("http", "https"):
        return deep_link
    target = f"{parts.netloc}{parts.path}"
    if parts.query:
        target += f"?{parts.query}"
    # Nothing caller-supplied can break out of the fragment: every value reaching a query
    # here goes through urllib.parse.quote, which percent-encodes both "#" and ";".
    return f"intent://{target}#Intent;scheme={parts.scheme};package={TV_APP_PACKAGE};end"


def _tv_result_page(title: str, heading: str, body_html: str, deep_link: str, accent: str) -> HTMLResponse:
    """The TV's Custom Tab landing page, which then hands back to the player app.

    Every caller-supplied value reaching this function is escaped by the caller with
    html.escape. The three pages this replaces interpolated `screen.name`, `target_email`
    and Google's raw `error` string straight into markup -- and screen names are
    operator-controlled, so that was stored XSS in a page that runs on the installer's
    phone.
    """
    launch_url = _android_intent_url(deep_link)
    safe_link = html.escape(launch_url, quote=True)
    return HTMLResponse(
        content=f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{html.escape(title)} &mdash; MR Signage</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body {{ background: #070A0F; color: #FFFFFF; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; text-align: center; }}
        .card {{ background: #0D131F; border: 1px solid #1E293B; border-radius: 20px; padding: 40px; max-width: 420px; width: 90%; }}
        .btn {{ display: inline-block; margin-top: 24px; padding: 14px 28px; background: {accent}; color: #070A0F; border-radius: 12px; text-decoration: none; font-weight: 700; }}
    </style>
</head>
<body>
    <div class="card">
        <h1 style="color:{accent};margin-bottom:12px;font-size:22px;">{heading}</h1>
        <p style="color:#94A3B8;line-height:1.5;">{body_html}</p>
        <a id="link" href="{safe_link}" class="btn" autofocus>Return to Player</a>
    </div>
    <script>
        window.location.href = {json.dumps(launch_url)};
        setTimeout(function() {{ window.close(); }}, 2500);
    </script>
</body>
</html>""",
        media_type="text/html; charset=utf-8",
    )


@router.get("/google/oauth-callback", response_class=HTMLResponse)
def tv_google_oauth_callback(
    code: str = None,
    state: str = None,
    error: str = None,
    request: Request = None,
    db: Session = Depends(database.get_db),
):
    """Handle Google OAuth return on Android TV Custom Tab and auto-bind screen.

    `email` and `name` used to be accepted here as query parameters and trusted as the
    signed-in identity. The `state` token they travelled with is minted by
    /google/oauth-url, which is unauthenticated -- so two requests, neither of them
    carrying a credential, bound an attacker's TV into any workspace whose owner's address
    they could guess. Identity now comes only from exchanging `code` with Google.
    """
    from jose import JWTError, jwt
    from .auth import ALGORITHM, get_secret_key

    FAILED_LINK = "olrac://auth/failed"

    def failure(heading: str, message: str, link: str = FAILED_LINK) -> HTMLResponse:
        return _tv_result_page("Sign-in failed", heading, message, link, "#FF8A80")

    if error or not state:
        return failure("Google Authentication Cancelled", html.escape(error or "Missing state token"))

    try:
        claims = jwt.decode(state, get_secret_key(), algorithms=[ALGORITHM])
    except JWTError:
        return failure("Sign-in Expired", "This sign-in link is no longer valid. Start again on the TV.")
    if claims.get("typ") != "tv_oauth":
        return failure("Sign-in Expired", "This sign-in link is no longer valid. Start again on the TV.")

    device_id = str(claims.get("sub") or "").removeprefix("device:")
    if not device_id:
        return failure("Sign-in Failed", "This sign-in link is missing its device. Start again on the TV.")

    if not code or not google_device.is_web_configured():
        return failure("Sign-in Failed", "Google did not return an authorization code. Start again on the TV.")

    # Must be byte-identical to the redirect_uri /google/oauth-url sent to Google, or the
    # exchange is rejected. Both are built from public_base_url, so a deployment behind TLS
    # produces an https:// pair -- the hardcoded http://127.0.0.1:8000 this replaces could
    # never work anywhere but a developer's laptop.
    redirect_uri = f"{public_base_url(request)}/api/screens/google/oauth-callback"
    try:
        google_claims = google_device.exchange_code(code, redirect_uri)
    except google_device.GoogleError as exc:
        logger.warning("Google code exchange failed on TV callback: %s", exc)
        return failure("Sign-in Failed", "Could not complete Google sign-in. Start again on the TV.")

    target_email = (google_claims.get("email") or "").strip().lower()
    if not target_email or not google_claims.get("email_verified"):
        return failure("Sign-in Failed", "That Google account has no verified email address.")

    google_sub = google_claims.get("sub")
    user = None
    if google_sub:
        user = db.query(models.User).filter(models.User.google_sub == google_sub).first()
    if not user:
        user = db.query(models.User).filter(
            (func.lower(models.User.email) == target_email)
            | (func.lower(models.User.username) == target_email)
        ).first()

    if not user:
        import secrets
        org_name = google_claims.get("name") or target_email.split("@")[0]
        # Same posture as signing up in the browser (`routers/auth.py`): a brand new
        # workspace waits until it has paid or been approved. This used to create the
        # organisation "active" with approved_at already stamped, which meant signing in on
        # a television instead of the dashboard walked straight past the storefront, the
        # approval queue and billing entirely -- the same signup, two different answers,
        # depending only on which screen it was done from.
        #
        # models.utcnow() rather than datetime.utcnow(): the column is timezone-aware and
        # everything that compares against it expects an aware value.
        organization = models.Organization(
            name=f"{org_name}'s Workspace",
            slug=f"org-{secrets.token_hex(4)}",
            status="pending_approval",
        )
        db.add(organization)
        db.flush()

        user = models.User(
            organization_id=organization.id,
            email=target_email,
            username=target_email,
            google_sub=google_sub,
            role="owner",
            is_active=True,
            auth_provider="google",
            picture=google_claims.get("picture"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.is_active:
        user.is_active = True
        if google_sub and not user.google_sub:
            user.google_sub = google_sub
        db.commit()

    screen = bind_screen_to_org(
        db,
        device_id=device_id,
        user=user,
        name=claims.get("nm"),
        installation_id=claims.get("iid"),
        model=claims.get("mod"),
        manufacturer=claims.get("man"),
        conflict_error=HTTPException(status_code=403, detail="Screen claimed elsewhere"),
        how="signed in with Google OAuth",
    )

    deep_link = (
        f"olrac://auth/success?screen_id={screen.id}"
        f"&screen_name={urllib.parse.quote(screen.name or '')}"
    )
    return _tv_result_page(
        "Display connected",
        "&#10003; Display Connected",
        f"Screen <strong>{html.escape(screen.name or 'Display')}</strong> is now linked to "
        f"<strong>{html.escape(user.email or user.username)}</strong>.",
        deep_link,
        "#68E0A0",
    )


@router.post("/enroll", response_model=schemas.EnrollResponse)
def enroll_device(req: schemas.EnrollRequest, db: Session = Depends(database.get_db)):
    token = db.query(models.EnrollmentToken).filter(
        models.EnrollmentToken.token == req.enrollment_token,
        models.EnrollmentToken.is_active == True
    ).first()
    # Use a single generic error for all token rejection cases so callers
    # cannot probe which tokens exist or distinguish between "no such token",
    # "expired", "revoked", and "quota exhausted".
    TOKEN_ERROR = HTTPException(status_code=400, detail="Invalid or inactive enrollment token")

    if not token:
        raise TOKEN_ERROR

    # Lifecycle check 1: expiry
    if token.expires_at and token.expires_at < models.utcnow():
        raise TOKEN_ERROR

    # Lifecycle check 2: max-use cap
    if token.max_uses is not None and token.use_count >= token.max_uses:
        raise TOKEN_ERROR

    screen = db.query(models.Screen).filter(models.Screen.device_id == req.device_id, models.Screen.deleted_at.is_(None)).first()

    if screen and screen.organization_id not in (None, token.organization_id):
        # A device already belonging to another organisation must never be re-homed by
        # presenting a different organisation's token. Without this guard anyone holding
        # any valid enrollment token can hijack a competitor's screen by guessing its
        # device_id: the screen moves to their tenant and the rightful owner's secret is
        # overwritten, taking the TV offline. Enrollment is unauthenticated, so this is
        # the only place the tenant boundary can be enforced.
        # Deliberately the same generic error as a bad token, so this cannot be used to
        # probe which device ids exist.
        logger.warning(
            "Rejected cross-tenant enrollment of device %s: owned by org %s, token belongs to org %s",
            req.device_id, screen.organization_id, token.organization_id,
        )
        raise TOKEN_ERROR

    if not screen and req.installation_id:
        # A factory reset mints a fresh device_id, so the same panel would enrol again as
        # a second screen and the fleet count would drift up with every wipe. The install
        # id survives the reset, so the existing row is reclaimed and its history, group
        # and playlist assignment stay with the physical screen.
        screen = (
            db.query(models.Screen)
            .filter(
                models.Screen.installation_id == req.installation_id,
                models.Screen.organization_id == token.organization_id,
            )
            .first()
        )
        if screen:
            logger.info(
                "Reclaiming screen %s for installation %s (device id changed %s -> %s)",
                screen.id, req.installation_id, screen.device_id, req.device_id,
            )
            temp_screen = db.query(models.Screen).filter(
                models.Screen.device_id == req.device_id,
                models.Screen.id != screen.id
            ).first()
            if temp_screen:
                db.delete(temp_screen)
                db.flush()
            screen.device_id = req.device_id

    # "or waiting_pairing" is the half this was missing, and it made the quota optional.
    #
    # The player calls POST /register on boot, which mints a row with status
    # "waiting_pairing" and no organisation. /enroll then FOUND that row, so `not screen`
    # was false and the check was skipped -- while the lines below bind it to the org and
    # flip it to "offline". Since ensure_screen_quota counts everything except
    # waiting_pairing, the screen was uncounted before and counted after: one screen over
    # the cap, every time, repeatable. bind_screen_to_org has always had both halves;
    # this is the same condition.
    if not screen or screen.status == "waiting_pairing":
        # New device counts against the plan, exactly as pairing does.
        ensure_screen_quota(db, token.organization_id, "enrol another device")
    if not screen:
        screen = models.Screen(device_id=req.device_id)
        db.add(screen)

    import secrets
    from .auth import get_password_hash
    device_secret = secrets.token_hex(32)

    # Re-enrolling a device already in this organisation is legitimate (factory reset,
    # reinstall) and rotates the secret.
    screen.device_secret_hash = get_password_hash(device_secret)
    screen.organization_id = token.organization_id
    screen.status = "offline"
    # The enrollment token was minted by an owner for exactly this, so the screen is
    # already authorised; making it queue for approval would gate zero-touch on a human.
    screen.approved_at = models.utcnow()
    screen.installation_id = req.installation_id
    if not screen.name:
        screen.name = f"Screen {req.device_id[:6]}"
    screen.assignment_updated_at = models.utcnow()

    # Provision the screen's own playlist so the detail page shows the content
    # library and playback timeline immediately upon enrollment.
    if not screen.playlist_id:
        _playlist = models.Playlist(
            organization_id=token.organization_id,
            name=f"{screen.name or f'Screen {screen.id}'} loop",
        )
        db.add(_playlist)
        db.flush()
        screen.playlist_id = _playlist.id

    # Atomically increment use_count after all validation passes.
    token.use_count = (token.use_count or 0) + 1

    db.commit()

    return {
        "device_id": req.device_id,
        "device_secret": device_secret,
        "organization_id": token.organization_id,
        "screen_id": screen.id
    }


@router.post("/auth", response_model=schemas.DeviceTokenResponse)
# Sized for a site reconnecting, not for a single device. Fifty screens in one mall leave
# through one public IP and all re-authenticate at once after a power cut -- the moment a
# tight cap does the most damage. The credential here is a 32-byte secret, so brute force
# is infeasible at any rate this would permit; the cap exists to bound abuse, not to be the
# thing standing between an attacker and the fleet.
@limiter.limit("120/minute")
def auth_device(
    request: Request,
    req: schemas.DeviceAuthRequest, 
    db: Session = Depends(database.get_db)
):
    screen = db.query(models.Screen).filter(models.Screen.device_id == req.device_id, models.Screen.deleted_at.is_(None)).first()
    if not screen or not screen.device_secret_hash:
        raise HTTPException(status_code=401, detail="Invalid credentials")
        
    from .auth import verify_password, create_access_token
    if not verify_password(req.device_secret, screen.device_secret_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
        
    token = create_access_token(
        data={"sub": f"device:{req.device_id}", "typ": DEVICE_TOKEN_TYPE},
        expires_delta=timedelta(hours=1)
    )
    return {"access_token": token}


@router.get("/", response_model=list[schemas.ScreenResponse])
async def get_screens(
    scope: TenantScope = Depends(get_tenant_scope),
):
    # A screen still waiting to be paired is not part of the fleet: it has no name, no
    # playlist and nothing to report, and listing it put placeholder cards in the grid.
    screens = (
        scope.query(models.Screen)
        .filter(models.Screen.status != "waiting_pairing")
        .order_by(models.Screen.name)
        .all()
    )
    candidates = screens

    # One pipelined round-trip for the whole fleet. Awaiting EXISTS per screen inside
    # the loop is 500 sequential round-trips at 500 screens, which is the latency
    # problem Redis was introduced to remove.
    presence: dict[int, bool] | None = None
    if candidates:
        try:
            redis = database.get_redis()
            pipe = redis.pipeline()
            for screen in candidates:
                pipe.exists(f"screen_presence:{screen.id}")
            results = await pipe.execute()
            presence = {s.id: bool(r) for s, r in zip(candidates, results)}
        except Exception as exc:  # noqa: BLE001 - Redis is a cache, not a dependency
            logger.warning("Redis presence read failed, falling back to last_seen: %s", exc)

    changed = False
    for screen in candidates:
        is_online = (presence.get(screen.id, False) if presence else False) or _is_recent(screen.last_seen)
        new_status = "online" if is_online else "offline"
        if screen.status != new_status:
            screen.status = new_status
            changed = True

    if changed:
        scope.db.commit()

    shots = _latest_screenshots(scope, screens)
    # Resolved against the whole group set so a card reports what the player will
    # actually do -- ancestor and dynamic groups included. The bare effective_playlist_id
    # property cannot reach those without spending a query per screen.
    groups = groups_by_id(scope.db, {screen.organization_id for screen in screens})
    # Read once for the whole fleet rather than per screen: it is one row and every screen
    # without its own PIN resolves to the same answer.
    universal_pin = _universal_maintenance_pin(scope.db)
    return [
        schemas.ScreenResponse.model_validate(screen).model_copy(
            update={
                "latest_screenshot": shots.get(screen.id),
                "effective_playlist_id": screen.resolve_playlist_id(groups),
                # What actually opens the maintenance door on THIS screen. Reported beside
                # maintenance_pin rather than replacing it: that field is the screen's own and
                # is what the settings dialog edits, while this is the one to put in front of
                # somebody standing at a kiosked television.
                "effective_maintenance_pin": (screen.maintenance_pin or "").strip() or universal_pin,
            }
        )
        for screen in screens
    ]


def _universal_maintenance_pin(db: Session) -> str | None:
    setting = db.query(models.SystemSetting).filter(
        models.SystemSetting.key == UNIVERSAL_PIN_KEY
    ).first()
    return ((setting.value if setting else "") or "").strip() or None


def _is_recent(last_seen) -> bool:
    """True when last_seen is inside the offline threshold.

    Tolerates naive and aware timestamps: SQLite returns naive values while Postgres
    with timezone=True returns aware ones, and comparing the two raises TypeError.
    """
    if last_seen is None:
        return False
    now = models.utcnow()
    if last_seen.tzinfo is None:
        now = now.replace(tzinfo=None)
    return (now - last_seen).total_seconds() < screen_offline_after_seconds()


def _latest_screenshots(scope: TenantScope, screens) -> dict[int, str]:
    """Most recent capture per screen, in one query.

    The fleet grid shows a live thumbnail per screen. Fetching that per card is 500 round
    trips at 500 screens, so the newest row per screen is picked with a window function.
    """
    if not screens:
        return {}

    ranked = (
        select(
            models.ScreenshotLog.screen_id.label("screen_id"),
            models.ScreenshotLog.file_url.label("file_url"),
            func.row_number()
            .over(
                partition_by=models.ScreenshotLog.screen_id,
                order_by=models.ScreenshotLog.created_at.desc(),
            )
            .label("rank"),
        )
        .where(models.ScreenshotLog.screen_id.in_([s.id for s in screens]))
    )
    # Super admins read across tenants; everyone else must stay inside their own.
    if scope.user.role != "super_admin":
        ranked = ranked.where(models.ScreenshotLog.organization_id == scope.organization_id)

    newest = ranked.subquery()
    rows = scope.db.execute(
        select(newest.c.screen_id, newest.c.file_url).where(newest.c.rank == 1)
    ).all()

    return {screen_id: resolve_media_url(file_url) for screen_id, file_url in rows}


@router.put("/{screen_id}", response_model=schemas.ScreenResponse)
def update_screen(
    screen_id: int,
    screen: schemas.ScreenCreate,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    db_screen = scope.get(models.Screen, screen_id)
    if not db_screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    db_screen.name = screen.name
    db_screen.orientation = screen.orientation
    # An operator setting orientation here means the heartbeat must stop overwriting it.
    db_screen.orientation_source = "manual"
    if "group_id" in screen.model_fields_set:
        db_screen.group_id = screen.group_id
    db_screen.assignment_updated_at = models.utcnow()
    scope.db.commit()
    scope.db.refresh(db_screen)
    return db_screen


@router.patch("/{screen_id}", response_model=schemas.ScreenResponse)
def patch_screen(
    screen_id: int,
    patch: schemas.ScreenPatch,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    db_screen = scope.get(models.Screen, screen_id)
    if not db_screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    fields = patch.model_fields_set
    if "target_version_code" in fields and patch.target_version_code is not None:
        release = scope.db.query(models.AppRelease).filter(
            models.AppRelease.version_code == patch.target_version_code
        ).first()
        if not release:
            raise HTTPException(status_code=422, detail="Unknown release version code")

    if "leader_screen_id" in fields and patch.leader_screen_id is not None:
        if patch.leader_screen_id == screen_id:
            raise HTTPException(status_code=422, detail="A screen cannot follow itself")
        # scope.get keeps this inside the tenant: without it, a follower could be pointed
        # at another organisation's screen and take its playback clock.
        if not scope.get(models.Screen, patch.leader_screen_id):
            raise HTTPException(status_code=422, detail="Unknown leader screen")

    for field in (
        "name",
        "orientation",
        "group_id",
        "target_version_code",
        "description",
        "tags",
        "location",
        "latitude",
        "longitude",
        "place_id",
        "timezone",
        "fit_mode",
        "maintenance_pin",
        "sync_playback",
        "sync_role",
        "leader_screen_id",
        "operating_mode",
        "operating_hours",
    ):
        if field in fields:
            setattr(db_screen, field, getattr(patch, field))
    if "target_version_code" in fields:
        # Re-pinning is a fresh attempt: carry no failure count over from the build this
        # screen was previously chasing, or it would roll back after one failure instead
        # of three.
        rollout.repin(db_screen, patch.target_version_code)
    if "orientation" in fields:
        # Explicit operator choice; auto-detection must not undo it on the next heartbeat.
        db_screen.orientation_source = "manual"
    # A leader has nothing to follow; keeping a stale pointer would make the player chase
    # a clock it is supposed to be publishing.
    if db_screen.sync_role == "leader":
        db_screen.leader_screen_id = None

    # The player asks "anything new since X?" and the answer is built from this marker.
    # Orientation and fit_mode are answered in that same response, so without a bump the
    # sync stays quiet and an operator's rotation change never reaches the panel.
    db_screen.assignment_updated_at = models.utcnow()
    scope.db.commit()
    scope.db.refresh(db_screen)
    from .websockets import trigger_screen_sync
    trigger_screen_sync(organization_id=scope.organization_id, screen_device_id=db_screen.device_id, group_id=db_screen.group_id)
    return db_screen


@router.delete("/{screen_id}", status_code=204)
async def remove_screen(
    screen_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner")),
):
    """Remove a TV from the fleet and sign the panel out of this workspace.

    Archives rather than deletes. play_logs and play_log_hourly_rollups both hold a NOT
    NULL foreign key to this row, and the booking report attributes plays to a screen by
    name -- so a DELETE would either fail on the constraint or, with it relaxed, quietly
    erase the proof of play an advertiser was billed on. The screen leaves the fleet, the
    history keeps it.

    Owner-only, unlike the editor-level screen edits: this ends a device's access to the
    workspace, which is closer to revoking a credential than to renaming a screen.
    """
    screen = scope.get(models.Screen, screen_id)
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    device_id = screen.device_id

    screen.deleted_at = models.utcnow()
    # The credential goes with the row. Without this, a token minted moments before the
    # removal stays valid for its full hour -- and verify_device_auth would refuse it
    # anyway, but leaving a live hash on an archived screen is a loaded gun for whatever
    # reads that column next.
    screen.device_secret_hash = None
    # Frees the identifiers so the same panel can be paired again as a new screen. Held
    # onto, the unique constraint on device_id would reject the fresh row and the TV could
    # never rejoin -- a removal that quietly bricks the hardware. The old values stay
    # readable on the archived row for anyone tracing what happened.
    screen.device_id = f"removed:{screen.id}:{device_id}" if device_id else None
    screen.installation_id = None
    # Nothing should still be scheduled to it.
    screen.playlist_id = None
    screen.group_id = None
    screen.pair_code = None
    scope.db.commit()

    # Tell it now rather than at its next sync. A screen removed mid-playback would
    # otherwise keep showing the old playlist for up to PLAYER_SYNC_INTERVAL_SECONDS,
    # which for an operator who just cut a customer off is exactly the wrong behaviour.
    # Best-effort: the 404 on the next sync is the guarantee, this is the speed.
    if device_id:
        await queue_device_command(device_id, "deregister", ttl_seconds=3600)
        try:
            from .websockets import broadcast_ws_event
            payload = {"type": "deregister", "command": "deregister"}
            await broadcast_ws_event(f"screen:{device_id}", payload)
            await broadcast_ws_event(f"device:{device_id}", payload)
        except Exception as exc:  # noqa: BLE001 - the 404 still resets it
            logger.warning("Could not push deregister to %s: %s", device_id, exc)

    logger.info("Screen %s removed from organisation %s", screen_id, screen.organization_id)
    return Response(status_code=204)


@router.delete("/{screen_id}/device-secret")
def revoke_device_secret(
    screen_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner")),
):
    db_screen = scope.get(models.Screen, screen_id)
    if not db_screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    db_screen.device_secret_hash = None
    scope.db.commit()
    return {"status": "ok", "message": "Device secret revoked"}


@router.post("/heartbeat")
async def heartbeat(
    req: schemas.HeartbeatRequest, 
    db: Session = Depends(database.get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(security)
):
    db_screen = verify_device_auth(req.device_id, credentials, db)
    
    # Update DB fields
    db_screen.last_seen = models.utcnow()
    if db_screen.status != "waiting_pairing":
        db_screen.status = "online"
    if req.device_version:
        db_screen.device_version = req.device_version
        if not req.app_version:
            db_screen.app_version = req.device_version
    if req.device_owner is not None:
        # Whether this panel can take a release without a human. Written here rather than at
        # pairing because provisioning often happens later, and a fleet that only learned it
        # once would keep reporting a screen as unattended long after it stopped being.
        db_screen.device_owner = req.device_owner
    if req.app_version is not None:
        db_screen.app_version = req.app_version
    if getattr(req, "update_status", None) is not None:
        rolled_back = rollout.apply_update_status(
            db_screen,
            req.update_status,
            getattr(req, "version_code", None),
        )
        if rolled_back:
            logging.getLogger(__name__).warning(
                "Screen %s %s", db_screen.id, rolled_back
            )
        # Only when the device actually told us its version. This used to assign
        # unconditionally, so a heartbeat reporting an update result without app_version
        # wiped the device_version that had been recorded earlier.
        if req.app_version is not None:
            db_screen.device_version = req.app_version
    if req.storage_used:
        db_screen.storage_used = req.storage_used

    # Auto-detected orientation, but never over an operator's explicit choice. This is the
    # only place auto-detection is applied; without the source check a manual override
    # silently reverts on the next heartbeat.
    if req.orientation is not None and db_screen.orientation_source != "manual":
        db_screen.orientation = normalise_rotation(req.orientation)

    if req.screen_width is not None:
        db_screen.screen_width = req.screen_width
    if req.screen_height is not None:
        db_screen.screen_height = req.screen_height
    if req.refresh_rate is not None:
        db_screen.refresh_rate = req.refresh_rate
    if req.total_ram_mb is not None:
        db_screen.total_ram_mb = req.total_ram_mb
    if req.available_ram_mb is not None:
        db_screen.available_ram_mb = req.available_ram_mb
    if req.total_storage_mb is not None:
        db_screen.total_storage_mb = req.total_storage_mb
    if req.free_storage_mb is not None:
        db_screen.free_storage_mb = req.free_storage_mb
    if req.supported_video_codecs is not None:
        db_screen.supported_video_codecs = req.supported_video_codecs
    if req.max_decode_width is not None:
        db_screen.max_decode_width = req.max_decode_width
    if req.max_decode_height is not None:
        db_screen.max_decode_height = req.max_decode_height
    if req.manufacturer is not None:
        db_screen.manufacturer = req.manufacturer
    if req.model is not None:
        db_screen.model = req.model
    if req.android_version is not None:
        db_screen.android_version = req.android_version
    if req.sdk_int is not None:
        db_screen.sdk_int = req.sdk_int
    if req.network_type is not None:
        db_screen.network_type = req.network_type
    if req.timezone is not None:
        db_screen.timezone = req.timezone
        
    fields = req.model_fields_set
    if "playback_state" in fields and req.playback_state is not None:
        db_screen.playback_state = req.playback_state
    if "current_item_id" in fields:
        db_screen.current_item_id = req.current_item_id
    if "last_error" in fields:
        db_screen.last_error = req.last_error
        if req.last_error:
            db_screen.last_error_at = models.utcnow()
    db.commit()

    # Presence cache. Best-effort on purpose: a Redis outage must never fail a TV's
    # heartbeat. last_seen is already committed above, so get_screens can fall back to
    # it. Reliability of the fleet outranks accuracy of the presence cache.
    pending_command = await pop_device_command(db_screen.device_id)
    try:
        ttl = screen_offline_after_seconds()
        await database.get_redis().setex(f"screen_presence:{db_screen.id}", ttl, "online")
    except Exception as exc:  # noqa: BLE001 - any Redis failure must stay non-fatal
        logger.warning("Redis presence write failed for screen %s: %s", db_screen.id, exc)

    return {
        "status": "ok", 
        "screen_status": db_screen.status,
        "server_time_ms": int(models.utcnow().timestamp() * 1000),
        "screen_id": db_screen.id,
        "organization_id": db_screen.organization_id,
        "pending_command": pending_command,
    }


# Screen approval was removed here, deliberately.
#
# `Screen.approved_at` was written by /pair, /sign-in and /enroll and read by absolutely
# nothing -- sync_tv never consulted it. So POST /{id}/approve and /{id}/revoke-approval
# had no effect on playback: a "revoked" screen kept syncing and playing, and no dashboard
# ever called either route. Pairing a screen is now what it always actually was, instant.
#
# The gate that DOES matter is company approval (Organization.status), which a platform
# administrator controls from the admin console and which get_tenant_scope and sync_tv both
# enforce. approved_at survives as the informational "when did this screen join" timestamp
# the fleet list shows.


@router.post("/{screen_id}/assign/{playlist_id}")
async def assign_playlist(
    screen_id: int,
    playlist_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    screen = scope.get(models.Screen, screen_id)
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    playlist = scope.get(models.Playlist, playlist_id)
    if not playlist:
        raise HTTPException(status_code=404, detail="Playlist not found")

    now = models.utcnow()
    screen.playlist_id = playlist_id
    screen.assignment_updated_at = now
    playlist.updated_at = now
    scope.db.commit()
    
    from .websockets import trigger_screen_sync
    trigger_screen_sync(organization_id=scope.organization_id, screen_device_id=screen.device_id)
    
    return {"status": "ok", "message": "Playlist assigned"}


@router.delete("/{screen_id}/assign")
async def clear_direct_assignment(
    screen_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    screen = scope.get(models.Screen, screen_id)
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    screen.playlist_id = None
    screen.assignment_updated_at = models.utcnow()
    scope.db.commit()
    
    from .websockets import trigger_screen_sync
    trigger_screen_sync(organization_id=scope.organization_id, screen_device_id=screen.device_id)
    
    return {"status": "ok"}


@router.post("/{screen_id}/ensure-playlist", response_model=schemas.ScreenResponse)
async def ensure_playlist(
    screen_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    """Provision a playlist for a screen that does not have one.

    Idempotent: if the screen already has a playlist_id the call is a no-op.
    The frontend calls this as a safety net when it navigates to a screen detail
    page whose effective_playlist_id is still null -- which should no longer happen
    after the pairing-time fix, but may exist for screens paired before that change.
    """
    screen = scope.get(models.Screen, screen_id)
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    if not screen.playlist_id:
        playlist = models.Playlist(
            organization_id=screen.organization_id,
            name=f"{screen.name or f'Screen {screen.id}'} loop",
        )
        scope.db.add(playlist)
        scope.db.flush()
        screen.playlist_id = playlist.id
        scope.db.commit()
        scope.db.refresh(screen)

    return schemas.ScreenResponse.model_validate(screen)

@router.get("/player-version", response_model=schemas.AppVersionResponse)
def player_version(db: Session = Depends(database.get_db)):
    # `db` was missing, so every call raised TypeError and the endpoint answered 500.
    # The sync path passed it correctly, which is why updates still reached devices and
    # hid the breakage here.
    return current_app_version(db)


from ..services import (
    MAX_GROUP_DEPTH,
    groups_by_id,
    resolve_screen_playlist,
)



@router.get(
    "/{device_id}/sync",
    response_model=schemas.SyncResponse,
    responses={204: {"description": "Playlist has not changed"}},
)
async def sync_tv(
    device_id: str,
    since: datetime | None = None,
    db: Session = Depends(database.get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(security)
):
    screen = verify_device_auth(device_id, credentials, db)

    pending_command = await pop_device_command(device_id)

    # Not cleared to advertise: answer with the demo reel and the reason, and nothing of the
    # tenant's own. Deliberately ahead of the 204 short-circuit below -- a screen that was
    # playing before its workspace was cut off must be told now, not left running a cached
    # playlist until something else happens to change its marker.
    #
    # This covers a suspension, a rejection, a workspace not yet activated, and a plan that
    # has run out. Refusing with 401/403 instead would not work: the player treats those as
    # transient, clears its token and retries forever on the playlist it already has, which
    # is deliberate -- rotating SECRET_KEY once made the whole fleet factory-reset itself.
    # Serving a valid response containing different content is what actually stops adverts.
    #
    # Nothing here is destructive. The screen stays paired, online, counted and credentialed,
    # so paying again restores service on the next sync with no re-pairing.
    block_reason = serving_block_reason(
        screen.organization, screen.organization.subscription if screen.organization else None
    )
    if block_reason:
        setting = db.query(models.SystemSetting).filter(models.SystemSetting.key == "universal_demo_video_url").first()
        demo_url = setting.value if setting else "/uploads/f9863204-f997-4122-ac1b-a50157e3d905.mp4"
        demo_url = resolve_media_url(demo_url) or demo_url

        demo_content = schemas.ContentResponse(
            id=999999,
            name="MR Signage Universal Demo Reel",
            type="video/mp4",
            file_url=demo_url,
            thumbnail=None,
            file_size_bytes=1575315,
            duration_ms=44000,
            status="ready",
            uploaded_at=models.utcnow(),
            renditions=[],
        )
        demo_item = schemas.PlaylistItemResponse(
            id=999999,
            content_id=999999,
            order=0,
            duration=44,
            rotation=screen.orientation or 0,
            content=demo_content,
        )
        demo_playlist = schemas.PlaylistResponse(
            id=999999,
            name="MR Signage Universal Demo Loop",
            default_transition="fade",
            default_transition_ms=600,
            created_at=models.utcnow(),
            updated_at=models.utcnow(),
            items=[demo_item],
        )
        return schemas.SyncResponse(
            status="online",
            playlist=demo_playlist,
            playlist_updated_at=models.utcnow(),
            fit_mode=screen.fit_mode or "contain",
            maintenance_pin=effective_maintenance_pin(db, screen) if getattr(screen, "authenticated", False) else None,
            master_pin=master_maintenance_pin(db) if getattr(screen, "authenticated", False) else None,
            sync_interval_seconds=15,
            operating_mode="always",
            pending_command=pending_command,
            screen_id=screen.id,
            organization_id=screen.organization_id,
            service_state=block_reason,
        )

    playlist_id = resolve_screen_playlist(screen, db)
    playlist = (
        db.query(models.Playlist)
        # The same load chain the dashboard's playlist routes use, and it matters more
        # here than anywhere else in the API. This response embeds a ContentResponse per
        # item, whose expires_at walks Content.playlist_items -- so lazily this cost one
        # extra query per item: 109 queries for a 100-item playlist. Every screen repeats
        # that every PLAYER_SYNC_INTERVAL_SECONDS, so an 80-screen estate on a one-minute
        # interval was issuing roughly 8,700 queries a minute to render a playlist that
        # had not changed. Named eagerly it is a fixed handful regardless of length.
        .options(*playlists_router.PLAYLIST_LOAD)
        .filter(
            models.Playlist.id == playlist_id,
            models.Playlist.organization_id == screen.organization_id,
        )
        .first()
        if playlist_id and screen.organization_id is not None
        else None
    )
    marker = screen.assignment_updated_at or screen.last_seen
    if playlist and playlist.updated_at > marker:
        marker = playlist.updated_at
    if screen.group and screen.group.updated_at > marker:
        marker = screen.group.updated_at

    # A new player build makes this sync fresh too.
    #
    # The marker was built only from playlist, group and assignment times, so a screen whose
    # content had settled asked "anything since X?" and was told 204 with an empty body --
    # for ever. The player reads the update offer out of that body, so it never learned a new
    # APK existed: publishing a release moved nothing, and every TV in the fleet stayed on
    # whatever was last installed by hand. That is why app_releases sat empty and nobody
    # noticed the over-the-air path had never once worked end to end.
    #
    # Folding the newest release's timestamp in costs one indexed query and self-settles: the
    # screen picks up the new marker with the body, and is back on 204 from the next sync.
    newest_release = (
        db.query(models.AppRelease.created_at)
        .order_by(models.AppRelease.created_at.desc())
        .limit(1)
        .scalar()
    )
    if newest_release and as_aware_utc(newest_release) > marker:
        marker = newest_release

    marker = as_aware_utc(marker)
    if since and marker <= as_aware_utc(since) and not pending_command:
        return Response(
            status_code=204,
            headers={"X-Sync-Interval-Seconds": str(player_sync_interval_seconds())},
        )

    playlist_payload = None
    if playlist:
        playlist_payload = schemas.PlaylistResponse.model_validate(playlist)
        valid_items = []
        for item in playlist_payload.items:
            # An item whose paid window has closed is not sent at all.
            #
            # The player already refuses to PLAY it (ScheduleEvaluator.isActive checks
            # endAt), so nothing was visibly wrong -- but it stayed in every sync payload
            # and every panel went on downloading and storing the video for a campaign that
            # had ended. After a year of bookings a screen is caching dead media for adverts
            # nobody sold. Dropping it here is what makes the file actually go away.
            #
            # start_at is deliberately NOT filtered: a screen has to fetch tomorrow's advert
            # today, or it has nothing cached when the window opens.
            if item.end_at and as_aware_utc(item.end_at) <= as_aware_utc(models.utcnow()):
                continue
            if item.content.status in {"ready", "processing"}:
                rendition = select_rendition(item.content, screen)
                if rendition:
                    item.content.file_url = rendition.file_url
                    item.content.sha256 = rendition.sha256
                    item.content.file_size_bytes = rendition.file_size_bytes

                item.content.file_url = resolve_media_url(item.content.file_url) or item.content.file_url
                item.content.thumbnail = resolve_media_url(item.content.thumbnail)
                # Resolve renditions urls as well if needed
                for rend in item.content.renditions:
                    rend.file_url = resolve_media_url(rend.file_url) or rend.file_url
                # Hand the player one number so it never has to work out precedence.
                item.rotation = resolve_rotation(item, screen)
                valid_items.append(item)
        playlist_payload.items = valid_items

    return schemas.SyncResponse(
        fit_mode=screen.fit_mode or "contain",
        # Withheld from a screen that authenticated with nothing but its device id: this
        # pin unlocks the on-TV maintenance screen, and device ids are guessable.
        maintenance_pin=effective_maintenance_pin(db, screen) if getattr(screen, "authenticated", False) else None,
            master_pin=master_maintenance_pin(db) if getattr(screen, "authenticated", False) else None,
        operating_mode=screen.operating_mode or "always",
        operating_hours=screen.operating_hours,
        playlist=playlist_payload,
        playlist_updated_at=marker,
        status=screen.status,
        app_version=current_app_version(db, screen.target_version_code or (screen.group.target_version_code if screen.group else None)),
        sync_interval_seconds=player_sync_interval_seconds(),
        pending_command=pending_command,
        screen_id=screen.id,
        organization_id=screen.organization_id,
    )


@router.post("/play-logs/batch")
def batch_upload_play_logs(
    req: schemas.PlayLogBatchRequest,
    db: Session = Depends(database.get_db),
    credentials: HTTPAuthorizationCredentials | None = Depends(security)
):
    if len(req.events) > 500:
        raise HTTPException(status_code=400, detail="Batch size exceeds limit of 500")

    # Same identity check as sync and heartbeat: a screen holding a device secret must
    # present its token, one provisioned by pair code is known by its device id. This
    # endpoint used to demand a JWT of its own, which no pair-code screen can produce, so
    # every one of them had its proof of play rejected with 401 and reported zero plays.
    device_id = req.device_id
    screen = verify_device_auth(device_id, credentials, db)

    # The identity that counts is the authenticated screen, never the body. screen_id and
    # organization_id are still ACCEPTED for compatibility with players that send them, but
    # a value naming a different screen or tenant is rejected rather than quietly
    # rewritten: silently re-attributing it would let one TV file plays that look like they
    # came from a competitor's screen, and the sender would be told it worked.
    if req.screen_id is not None and req.screen_id != screen.id:
        raise HTTPException(status_code=403, detail="screen_id does not match the authenticated screen")
    if req.organization_id is not None and req.organization_id != screen.organization_id:
        raise HTTPException(status_code=403, detail="organization_id does not match the authenticated screen")

    target_screen_id = screen.id
    target_org_id = screen.organization_id

    if not req.events:
        return {"status": "ok", "inserted": 0}

    from sqlalchemy.dialects.postgresql import insert

    # The player cannot attribute a play to a campaign: PlaylistItemEntity has no campaign
    # column, so every device sends campaign_id = null. Left as-is that makes campaign_id
    # NULL on every rollup row, and since all campaign analytics filter on it, every
    # campaign reports zero plays forever. Derive it here from the playlist the event
    # names -- server side, so it also repairs events already queued on devices.
    # Scoped to the screen's verified org so a forged playlist_id cannot attribute across tenants.
    playlist_ids = {ev.playlist_id for ev in req.events if ev.playlist_id is not None}
    campaign_by_playlist: dict[int, int | None] = {}
    media_by_playlist: dict[int, int | None] = {}
    if playlist_ids:
        campaign_by_playlist = {
            pid: cid
            for pid, cid in db.query(models.Playlist.id, models.Playlist.campaign_id)
            .filter(
                models.Playlist.id.in_(playlist_ids),
                models.Playlist.organization_id == target_org_id,
            )
            .all()
        }
        # Fallback media_id resolution for single-item playlists
        playlist_items = (
            db.query(models.PlaylistItem.playlist_id, models.PlaylistItem.content_id)
            .filter(models.PlaylistItem.playlist_id.in_(playlist_ids))
            .all()
        )
        from collections import defaultdict
        p_items_map = defaultdict(list)
        for pid, cid in playlist_items:
            p_items_map[pid].append(cid)
        for pid, cids in p_items_map.items():
            if len(cids) == 1:
                media_by_playlist[pid] = cids[0]

    values = []
    now = models.utcnow()
    for ev in req.events:
        values.append({
            "event_id": ev.event_id,
            "screen_id": target_screen_id,
            "organization_id": target_org_id,
            "media_id": ev.media_id or media_by_playlist.get(ev.playlist_id),
            "playlist_id": ev.playlist_id,
            "campaign_id": ev.campaign_id or campaign_by_playlist.get(ev.playlist_id),
            "device_started_at": ev.device_started_at,
            "device_finished_at": ev.device_finished_at,
            "corrected_started_at": ev.corrected_started_at,
            "corrected_finished_at": ev.corrected_finished_at,
            "duration_ms": ev.duration_ms,
            "status": ev.status,
            "error_message": ev.error_message,
            "received_at": now
        })
        
    stmt = insert(models.PlayLog).values(values)
    stmt = stmt.on_conflict_do_nothing(index_elements=['event_id'])
    
    result = db.execute(stmt)
    db.commit()

    # Immediately aggregate so stats and proof-of-play cards update live in real-time
    from ..worker import aggregate_play_logs_sync
    aggregate_play_logs_sync(db)
    
    return {"status": "ok", "inserted": result.rowcount}



@router.post("/resolve-location-link", response_model=schemas.ResolveLinkResponse)
def resolve_location_link(
    payload: schemas.ResolveLinkRequest,
    tenant: TenantScope = Depends(get_tenant_scope),
):
    """Coordinates for a pasted Google Maps link.

    Deliberately not a Google API call: every form of Maps URL already carries the
    coordinate, so this needs no key and no billing account. Short links are resolved by
    following the redirect, which is a plain HTTP request.
    """
    try:
        latitude, longitude, name = parse_maps_link(payload.link)
    except MapsLinkError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return schemas.ResolveLinkResponse(latitude=latitude, longitude=longitude, name=name)


@router.post("/{screen_id}/bring-to-front")
async def bring_to_front(
    screen_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    screen = scope.get(models.Screen, screen_id)
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    # Two delivery paths, on purpose: the publish reaches a screen holding a websocket
    # right now, and the queued command is picked up at the next sync or heartbeat by one
    # that is not. queue_device_command owns the SETEX -- this used to write the same key
    # itself a few lines below, so the two could disagree about the TTL.
    queued = await queue_device_command(screen.device_id, "bring_to_front", 300) if screen.device_id else False

    published = False
    try:
        redis = database.get_redis()
        payload = json.dumps({"type": "bring_to_front", "command": "launch_app"})
        if screen.device_id:
            await redis.publish(f"screen:{screen.device_id}", payload)
            await redis.publish(f"device:{screen.device_id}", payload)
        await redis.publish(f"screen:{screen.id}", payload)
        if screen.organization_id:
            await redis.publish(
                f"org:{screen.organization_id}",
                json.dumps({"type": "bring_to_front", "device_id": screen.device_id, "screen_id": screen.id}),
            )
        published = True
    except Exception as e:
        logger.warning(f"Failed to publish bring_to_front to redis: {e}")

    try:
        from .websockets import broadcast_in_memory
        payload = json.dumps({"type": "bring_to_front", "command": "launch_app"})
        if screen.device_id:
            await broadcast_in_memory(f"screen:{screen.device_id}", payload)
            await broadcast_in_memory(f"device:{screen.device_id}", payload)
        await broadcast_in_memory(f"screen:{screen.id}", payload)
        if screen.organization_id:
            await broadcast_in_memory(
                f"org:{screen.organization_id}",
                json.dumps({"type": "bring_to_front", "device_id": screen.device_id, "screen_id": screen.id}),
            )
        published = True
    except Exception as e:
        logger.warning(f"Failed to broadcast bring_to_front in-memory: {e}")

    if not queued and not published:
        # Neither path is available, so the command is going nowhere. Saying "ok" here is
        # what made this button look like flaky hardware.
        raise HTTPException(
            status_code=503,
            detail="Could not reach the message broker; the screen was not notified.",
        )

    return {"status": "ok", "message": "Bring to front command sent"}

