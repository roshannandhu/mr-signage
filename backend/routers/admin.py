"""Platform-operator routes: tenants, packages, and the universal demo reel.

This replaces routers/approvals.py, which was mounted at /api/approvals and gated by a
local `_require_admin` that accepted `role in ("manager", "owner")` in addition to a
super admin. Every Google signup is created with role="owner", so that check made each
customer a platform administrator: able to enumerate every tenant and its owner's email
address, approve their own pending workspace, rewrite anyone's quota, and suspend a
competitor. Everything here now depends on `require_super_admin`.

Nothing in this module edits a tenant's own content. The drill-in routes are deliberately
read-only -- an operator needs to see what a workspace contains to support it, not to
change it.
"""
import json
import logging
import os
import pathlib
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from .. import database, models, schemas
from ..billing import TERMINAL_SUBSCRIPTION_STATUSES, apply_plan, plan_features, subscription_state
from ..limiter import limiter
from ..media_urls import resolve_media_url
from ..services import audit, legacy_device_auth_allowed, screens_without_secret
from ..services.admin_metrics import TenantUsage, fleet_screen_filter, screen_is_online, tenant_usage
from ..services.storage_service import forget_cached_usage
from ..services.tenant_purge import TENANT_PURGE_AFTER_DAYS, purge_due_at, purge_organization
from ..tenancy import TenantScope, require_super_admin

logger = logging.getLogger(__name__)

router = APIRouter()

DEFAULT_DEMO_VIDEO = "/uploads/f9863204-f997-4122-ac1b-a50157e3d905.mp4"
DEMO_VIDEO_KEY = "universal_demo_video_url"


# --------------------------------------------------------------------------- schemas


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    slug: str
    monthly_price_paise: int
    yearly_price_paise: int
    # The one-time price the storefront charges for `duration_days` of access.
    price_paise: int
    duration_days: int
    # None = no screen limit. 0 stays a real limit of zero.
    max_screens: Optional[int] = None
    max_clients: int
    max_storage_bytes: int
    max_ad_slots: int
    # Populated from feature_flags_json by _plan_out; from_attributes cannot decode the
    # stored JSON on its own.
    feature_flags: Dict[str, bool] = Field(default_factory=dict)
    is_active: bool


class PlanWrite(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9-]*$")
    monthly_price_paise: int = Field(default=0, ge=0)
    yearly_price_paise: int = Field(default=0, ge=0)
    price_paise: int = Field(default=0, ge=0)
    duration_days: int = Field(default=30, ge=1, le=3650)
    # -1 = unlimited, on every one of these four. Screens store it as NULL and the other
    # three as 0, because 0 already means unlimited there and means zero for screens -- the
    # mapping lives in `_limit` so no caller has to remember which is which.
    max_screens: int = Field(default=0, ge=-1)
    max_clients: int = Field(default=0, ge=-1)
    max_storage_bytes: int = Field(default=10 * 1024 * 1024 * 1024, ge=-1)
    max_ad_slots: int = Field(default=0, ge=-1)
    feature_flags: Dict[str, bool] = Field(default_factory=dict)
    is_active: bool = True


class PlanPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    monthly_price_paise: Optional[int] = Field(default=None, ge=0)
    yearly_price_paise: Optional[int] = Field(default=None, ge=0)
    price_paise: Optional[int] = Field(default=None, ge=0)
    duration_days: Optional[int] = Field(default=None, ge=1, le=3650)
    max_screens: Optional[int] = Field(default=None, ge=-1)
    max_clients: Optional[int] = Field(default=None, ge=-1)
    max_storage_bytes: Optional[int] = Field(default=None, ge=-1)
    max_ad_slots: Optional[int] = Field(default=None, ge=-1)
    feature_flags: Optional[Dict[str, bool]] = None
    is_active: Optional[bool] = None


class TenantSummaryOut(BaseModel):
    id: int
    name: str
    slug: str
    status: str
    created_at: str
    owner_email: Optional[str] = None
    owner_name: Optional[str] = None
    plan_id: Optional[int] = None
    plan_name: Optional[str] = None
    screens_count: int = 0
    online_screens_count: int = 0
    # The limit ACTUALLY ENFORCED: the override when one is set, else the package's.
    # None = no limit configured. Distinct from 0, which is a package granting none --
    # the console must not draw those two the same way.
    max_screens: Optional[int] = None
    max_ad_slots: Optional[int] = None
    # The raw override column, for the quota dialog to edit. Reported separately because
    # the dialog writes this field: seeded from the effective value instead, saving would
    # silently pin the tenant to whatever their package happened to say that day, and they
    # would then stop tracking the package.
    max_screens_override: int = 0
    max_ad_slots_override: int = 0
    ad_slots_used: int = 0
    # None = unlimited on each of these, matching max_screens / max_ad_slots above.
    max_clients: Optional[int] = None
    clients_used: int = 0
    # What this workspace's package actually grants it. Absent until now, so the console
    # could show an operator a tenant's screen and ad limits but never which features they
    # had -- the one part of a package that cannot be inferred from the numbers.
    feature_flags: Dict[str, bool] = Field(default_factory=dict)
    storage_used_bytes: int = 0
    storage_quota_bytes: int = 0
    rejection_reason: Optional[str] = None
    # The paid window. `subscription_state` is the answer the product actually acts on --
    # "active", "grace" or "expired" -- rather than the raw status column, which says
    # "active" right up until something compares the period to the clock.
    subscription_state: Optional[str] = None
    subscription_status: Optional[str] = None
    billing_period: Optional[str] = None
    current_period_end: Optional[str] = None
    # Removal, and the deadline it created. Both are reported because the console has to
    # draw the difference between a workspace that is blocked (recoverable by a click, and
    # nothing is going to happen to it) and one that is counting down to being destroyed.
    deleted_at: Optional[str] = None
    purge_at: Optional[str] = None


class TenantScreenOut(BaseModel):
    id: int
    name: Optional[str] = None
    status: str
    last_seen: Optional[str] = None
    location: Optional[str] = None
    model: Optional[str] = None
    app_version: Optional[str] = None
    playback_state: str = "idle"


class TenantContentOut(BaseModel):
    id: int
    name: Optional[str] = None
    type: Optional[str] = None
    status: str
    file_size_bytes: int = 0
    uploaded_at: Optional[str] = None
    thumbnail: Optional[str] = None


class TenantUserOut(BaseModel):
    id: int
    username: str
    email: Optional[str] = None
    full_name: Optional[str] = None
    role: str
    is_active: bool


class ApprovalRequest(BaseModel):
    """Approve a workspace, optionally onto a package.

    `plan_id` is the normal path: the package carries the limits. The two overrides exist
    for the tenant that negotiated something different, and win over the package when set.
    """

    plan_id: Optional[int] = None
    max_screens: Optional[int] = Field(default=None, ge=0)
    max_ad_slots: Optional[int] = Field(default=None, ge=0)


class QuotaUpdateRequest(BaseModel):
    plan_id: Optional[int] = None
    max_screens: Optional[int] = Field(default=None, ge=0)
    max_ad_slots: Optional[int] = Field(default=None, ge=0)


class SubscriptionUpdateRequest(BaseModel):
    """Move a workspace's paid window by hand.

    `extend_days` is the everyday case -- "give them another month" -- and is measured from
    whichever is later, now or the end they already have, so extending twice in a week adds
    two months rather than throwing the first one away.
    """
    extend_days: Optional[int] = Field(default=None, ge=1, le=3650)
    period_end: Optional[datetime] = None
    billing_period: Optional[Literal["monthly", "yearly", "one_time"]] = None
    # "active" reinstates a workspace whose window ran out; "expired" cuts one off now.
    status: Optional[Literal["active", "expired"]] = None


UNLIMITED = -1


def _screen_limit(value: int) -> Optional[int]:
    """A screen cap as stored: -1 becomes NULL, which is what "no limit" is for screens.

    Screens cannot use 0 for unlimited the way ad slots and clients do -- 0 there is a real
    package granting no screens, and test_tenant_isolation enrols against exactly that. So
    the API takes one marker for all four limits and this decides where it lands.
    """
    return None if value == UNLIMITED else value


def _zero_is_unlimited(value: int) -> int:
    """Ad slots, clients and storage: -1 and 0 both already mean no limit."""
    return 0 if value == UNLIMITED else value


def _store_limits(fields: dict) -> dict:
    """Translate the API's -1 = "unlimited" into the way each column stores it.

    create_plan and update_plan wrote the request straight onto the row, so a package saved
    with -1 screens held -1, and `screen_count >= -1` refused every TV on it; -1 storage was
    copied onto each tenant as a quota of -1 bytes and refused every upload. Only the grant
    route applied this mapping. One function now, used by all three.
    """
    if fields.get("max_screens") is not None:
        fields["max_screens"] = _screen_limit(fields["max_screens"])
    for name in ("max_clients", "max_storage_bytes", "max_ad_slots"):
        if fields.get(name) is not None:
            fields[name] = _zero_is_unlimited(fields[name])
    return fields


class GrantRequest(BaseModel):
    """Put one workspace on limits of your own, rather than on a published package.

    Everything is optional; anything left out keeps the value the workspace already has.
    **-1 means unlimited** on each of the four numbers.
    """
    max_screens: Optional[int] = Field(default=None, ge=UNLIMITED)
    max_ad_slots: Optional[int] = Field(default=None, ge=UNLIMITED)
    max_clients: Optional[int] = Field(default=None, ge=UNLIMITED)
    max_storage_bytes: Optional[int] = Field(default=None, ge=UNLIMITED)
    features: Optional[dict[str, bool]] = None
    days: Optional[int] = Field(default=None, ge=1, le=3650)
    name: Optional[str] = None


class RejectionRequest(BaseModel):
    reason: Optional[str] = "Application could not be approved at this time."


class DemoVideoPayload(BaseModel):
    url: str
    description: Optional[str] = None


# --------------------------------------------------------------------------- helpers


def _iso(value) -> Optional[str]:
    return value.isoformat() if value else None


def _summaries(db: Session, orgs: List[models.Organization]) -> List[TenantSummaryOut]:
    """Summaries for many tenants in a fixed number of queries (see admin_metrics).

    This used to run ~9 queries per tenant plus 2 per ad booking, which at production's
    ~114 ms per query made the tenants list take most of a minute at 30 tenants.
    """
    usage = tenant_usage(db, [org.id for org in orgs])
    return [_summary_from(org, usage[org.id]) for org in orgs]


def _summarise(db: Session, org: models.Organization) -> TenantSummaryOut:
    return _summaries(db, [org])[0]


def _summary_from(org: models.Organization, used: TenantUsage) -> TenantSummaryOut:
    owner = used.owner
    return TenantSummaryOut(
        id=org.id,
        name=org.name,
        slug=org.slug,
        status=org.status,
        created_at=_iso(org.created_at) or "",
        owner_email=owner.email if owner else None,
        owner_name=(owner.full_name or owner.username) if owner else None,
        plan_id=org.plan_id,
        plan_name=org.plan.name if org.plan else None,
        screens_count=used.screens,
        online_screens_count=used.online_screens,
        # The limit actually enforced, not the raw override column. Reporting the column
        # meant the console showed 0 -- rendered as an infinity sign by the quota bar --
        # for every tenant whose limit came from their package, which is all of them. An
        # operator setting a 5-screen package saw "unlimited" and could not tell what a
        # tenant was allowed.
        max_screens=org.effective_max_screens,
        max_ad_slots=org.effective_max_ad_slots,
        max_screens_override=org.max_screens or 0,
        max_ad_slots_override=org.max_ad_slots or 0,
        ad_slots_used=used.ad_slots_used,
        max_clients=org.effective_max_clients,
        clients_used=used.clients,
        feature_flags=plan_features(org.plan) if org.plan else {},
        storage_used_bytes=used.storage_bytes,
        storage_quota_bytes=org.storage_quota_bytes,
        rejection_reason=org.rejection_reason,
        subscription_state=subscription_state(org.subscription),
        subscription_status=org.subscription.status if org.subscription else None,
        billing_period=org.subscription.billing_period if org.subscription else None,
        current_period_end=(
            _iso(org.subscription.current_period_end) if org.subscription else None
        ),
        deleted_at=_iso(org.deleted_at),
        purge_at=_iso(purge_due_at(org)),
    )


def _tenant_state(org: models.Organization) -> dict:
    """The fields an audit entry compares before and after a tenant lifecycle change."""
    sub = org.subscription
    return {
        "status": org.status,
        "plan_id": org.plan_id,
        "max_screens": org.max_screens,
        "max_ad_slots": org.max_ad_slots,
        "max_clients": org.max_clients,
        "storage_quota_bytes": org.storage_quota_bytes,
        "deleted_at": _iso(org.deleted_at),
        "subscription_status": sub.status if sub else None,
        "current_period_end": _iso(sub.current_period_end) if sub else None,
    }


def _audit(
    db: Session,
    scope: TenantScope,
    request: Request,
    action: str,
    *,
    org: Optional[models.Organization] = None,
    org_id: Optional[int] = None,
    target_type: Optional[str] = None,
    target_id=None,
    before=None,
    after=None,
) -> None:
    audit.record(
        db,
        actor=scope.user,
        action=action,
        request=request,
        target_org_id=org.id if org is not None else org_id,
        target_type=target_type or ("organization" if org is not None else None),
        target_id=target_id if target_id is not None else (org.id if org is not None else None),
        before=before,
        after=after,
        acting_org_id=scope.acting_organization_id,
    )


def _get_org(db: Session, org_id: int) -> models.Organization:
    org = db.query(models.Organization).filter(models.Organization.id == org_id).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org


def _apply_plan(org: models.Organization, plan: models.Plan) -> None:
    """Put a tenant on a package.

    Sets the package and clears the per-tenant overrides, rather than copying the package's
    numbers into them. `Organization.max_screens` / `max_ad_slots` mean "this tenant differs
    from their package", and `effective_max_*` already resolves override -> package ->
    unlimited, so copying made every tenant permanently different from a package whose
    numbers merely happened to match at the time.

    The consequence was invisible and wrong in both directions: editing a package no longer
    moved anyone put on it from this console, while `max_clients` -- which this never copied
    -- did keep tracking it, so one tenant followed their package for clients and ignored it
    for screens. The custom-request revise path had to clear these columns by hand to work
    around exactly this.

    storage_quota_bytes is still copied: it is a real column with no derived equivalent, and
    `test_plan_purchase` pins that it follows the package.
    """
    apply_plan(org, plan)


# --------------------------------------------------------------------------- tenants


TOTAL_COUNT_HEADER = "X-Total-Count"

_TENANT_SORTS = {
    "created_desc": models.Organization.created_at.desc(),
    "created_asc": models.Organization.created_at.asc(),
    "name_asc": func.lower(models.Organization.name).asc(),
    "name_desc": func.lower(models.Organization.name).desc(),
}


def like_pattern(term: str) -> str:
    """A substring ILIKE pattern with the user's own % and _ taken literally."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/tenants", response_model=List[TenantSummaryOut])
def list_tenants(
    response: Response,
    status: Optional[str] = None,
    q: Optional[str] = Query(default=None, max_length=100),
    plan_id: Optional[int] = None,
    sort: Literal["created_desc", "created_asc", "name_asc", "name_desc"] = "created_desc",
    limit: Optional[int] = Query(default=None, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Workspaces on the platform, newest first by default.

    Every parameter is optional and the body is the same plain list it always was; with
    `limit` the total matching count comes back in X-Total-Count for paging.
    """
    query = db.query(models.Organization)
    if status:
        query = query.filter(models.Organization.status == status)
    if plan_id is not None:
        query = query.filter(models.Organization.plan_id == plan_id)
    if q and q.strip():
        pattern = like_pattern(q.strip())
        owner_match = (
            db.query(models.User.id)
            .filter(
                models.User.organization_id == models.Organization.id,
                models.User.role == "owner",
                models.User.email.ilike(pattern, escape="\\"),
            )
            .exists()
        )
        query = query.filter(
            models.Organization.name.ilike(pattern, escape="\\")
            | models.Organization.slug.ilike(pattern, escape="\\")
            | owner_match
        )
    if limit is not None:
        response.headers[TOTAL_COUNT_HEADER] = str(query.count())
    query = query.options(
        selectinload(models.Organization.plan), selectinload(models.Organization.subscription),
    ).order_by(_TENANT_SORTS[sort], models.Organization.id.desc()).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return _summaries(db, query.all())


@router.get("/tenants/{org_id}", response_model=TenantSummaryOut)
def get_tenant(
    org_id: int,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    return _summarise(db, _get_org(db, org_id))


class TenantPlaysOut(BaseModel):
    total_plays: int
    online_plays: int
    offline_plays: int


@router.get("/tenants/{org_id}/plays", response_model=TenantPlaysOut)
def get_tenant_plays(
    org_id: int,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """The last 30 days, fleet-wide -- not the per-campaign or per-advert breakdown the
    tenant's own reports show, just enough for an operator to tell a quiet tenant from a
    working one at a glance. A separate route, not a field on TenantSummaryOut: that schema
    also serves the tenants LIST, and summing rollups for every row on one page is exactly
    the kind of query this app's database (Supabase, ~114ms/round trip) cannot afford N times.
    """
    _get_org(db, org_id)
    since = models.utcnow() - timedelta(days=30)
    total, offline = db.query(
        func.coalesce(func.sum(models.PlayLogHourlyRollup.total_plays), 0),
        func.coalesce(func.sum(models.PlayLogHourlyRollup.offline_plays), 0),
    ).filter(
        models.PlayLogHourlyRollup.organization_id == org_id,
        models.PlayLogHourlyRollup.date_hour >= since,
    ).one()
    return TenantPlaysOut(total_plays=total, online_plays=max(0, total - offline), offline_plays=offline)


@router.get("/tenants/{org_id}/kiosk-events", response_model=List[schemas.KioskUnlockEventResponse])
def get_kiosk_unlock_events(
    org_id: int,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Every local PIN attempt and remote unlock for this tenant's fleet, newest first.

    Visibility only -- nothing here lets an operator skip the PIN itself. That boundary
    stays where Phase 1.7 put it when it removed the old master-PIN-opens-everything
    fallback; a genuinely stuck tenant goes through remote_unlock below instead, which is
    its own deliberate, logged act, not a standing bypass.
    """
    _get_org(db, org_id)
    rows = (
        db.query(models.KioskUnlockEvent, models.Screen.name, models.User.username)
        .outerjoin(models.Screen, models.Screen.id == models.KioskUnlockEvent.screen_id)
        .outerjoin(models.User, models.User.id == models.KioskUnlockEvent.actor_user_id)
        .filter(models.KioskUnlockEvent.organization_id == org_id)
        .order_by(models.KioskUnlockEvent.occurred_at.desc())
        .limit(200)
        .all()
    )
    return [
        schemas.KioskUnlockEventResponse(
            id=event.id,
            screen_id=event.screen_id,
            screen_name=screen_name,
            occurred_at=event.occurred_at,
            method=event.method,
            success=event.success,
            actor_username=actor_username,
        )
        for event, screen_name, actor_username in rows
    ]


@router.post(
    "/tenants/{org_id}/screens/{screen_id}/remote-unlock",
    response_model=schemas.RemoteUnlockResponse,
)
async def remote_unlock_screen(
    org_id: int,
    screen_id: int,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Queue a remote unlock for one screen -- the recovery lever for a tenant genuinely
    locked out (lost PIN, nobody left who knows it).

    Takes effect at that screen's next sync or heartbeat, same as every other queued
    command; a screen that is offline right now gets it once it reconnects, not instantly.
    Logged here rather than left implicit in the command queue, so "who unlocked this and
    when" survives long after the 24-hour hold on the command itself expires.
    """
    organization = _get_org(db, org_id)
    screen = (
        db.query(models.Screen)
        .filter(models.Screen.id == screen_id, models.Screen.organization_id == organization.id)
        .first()
    )
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")
    if not screen.device_id:
        raise HTTPException(status_code=422, detail="This screen has not paired yet")

    from ..services import queue_device_command

    queued = await queue_device_command(screen.device_id, "remote_unlock")
    db.add(models.KioskUnlockEvent(
        organization_id=organization.id,
        screen_id=screen.id,
        method="remote",
        success=True,
        actor_user_id=scope.user.id,
    ))
    _audit(db, scope, request, "screen.remote_unlock", org=organization,
           target_type="screen", target_id=screen.id, after={"queued": queued})
    db.commit()
    logger.info(
        "Remote unlock queued for screen %s (org %s) by %s",
        screen.id, organization.id, scope.user.username,
    )
    return schemas.RemoteUnlockResponse(queued=queued)


class AdminProvisioningRequest(BaseModel):
    wifi_ssid: str = Field(min_length=1, max_length=64)
    wifi_password: str = Field(default="", max_length=128)
    wifi_security_type: str = "WPA"
    max_uses: int = Field(default=1, ge=1, le=1000)


@router.post("/tenants/{org_id}/provisioning/qr")
def generate_tenant_provisioning_qr(
    org_id: int,
    req: AdminProvisioningRequest,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Zero-touch provisioning for a TENANT the operator chooses explicitly.

    The tenant-facing POST /api/provisioning/qr (routers/provisioning.py) binds the
    enrollment token to scope.organization_id -- correct for an owner setting up their
    own fleet, wrong for an operator doing it on a tenant's behalf from the admin console,
    where there is no "Enter workspace" step and so no acting org to fall back to; it
    would otherwise mint a token bound to the OPERATOR's own house organisation, and the
    TV would enrol into the wrong tenant's workspace. This route takes the target
    explicitly instead of inferring it from the caller.
    """
    import secrets

    organization = _get_org(db, org_id)
    if organization.deleted_at is not None:
        raise HTTPException(status_code=409, detail="This workspace has been removed.")

    cert_hash = os.getenv("PROVISIONING_CERT_SHA256_BASE64")
    apk_url = os.getenv("PROVISIONING_APK_URL")
    base_url = os.getenv("PUBLIC_BASE_URL")
    if not cert_hash or not apk_url or not base_url:
        missing = [
            name for name, value in (
                ("PROVISIONING_CERT_SHA256_BASE64", cert_hash),
                ("PROVISIONING_APK_URL", apk_url),
                ("PUBLIC_BASE_URL", base_url),
            ) if not value
        ]
        raise HTTPException(
            status_code=503,
            detail=f"Provisioning is not configured yet. Set {', '.join(missing)} in .env and restart the API.",
        )

    token = models.EnrollmentToken(
        organization_id=organization.id,
        token=secrets.token_urlsafe(32),
        description=f"Admin-generated provisioning QR for {organization.name} (site {req.wifi_ssid})",
        is_active=True,
        max_uses=req.max_uses,
    )
    db.add(token)
    # The token itself is a bearer credential; never in the audit trail, only that one
    # was minted and for whom -- the same rule the secret-shaped fields in audit.py apply.
    _audit(db, scope, request, "tenant.provisioning_qr", org=organization, target_type="enrollment_token",
           after={"wifi_ssid": req.wifi_ssid, "max_uses": req.max_uses})
    db.commit()
    db.refresh(token)

    return {
        "android.app.extra.PROVISIONING_DEVICE_ADMIN_COMPONENT_NAME": "com.olrac.signage/.receivers.SignageDeviceAdminReceiver",
        "android.app.extra.PROVISIONING_DEVICE_ADMIN_PACKAGE_DOWNLOAD_LOCATION": apk_url,
        "android.app.extra.PROVISIONING_DEVICE_ADMIN_SIGNATURE_CHECKSUM": cert_hash,
        "android.app.extra.PROVISIONING_WIFI_SSID": req.wifi_ssid,
        "android.app.extra.PROVISIONING_WIFI_PASSWORD": req.wifi_password,
        "android.app.extra.PROVISIONING_WIFI_SECURITY_TYPE": req.wifi_security_type,
        "android.app.extra.PROVISIONING_SKIP_ENCRYPTION": True,
        "android.app.extra.PROVISIONING_LEAVE_ALL_SYSTEM_APPS_ENABLED": True,
        "android.app.extra.PROVISIONING_ADMIN_EXTRAS_BUNDLE": {
            "enrollment_token": token.token,
            "api_base_url": base_url,
        },
    }


@router.get("/tenants/{org_id}/screens", response_model=List[TenantScreenOut])
def get_tenant_screens(
    org_id: int,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    _get_org(db, org_id)
    screens = (
        db.query(models.Screen)
        .filter(models.Screen.organization_id == org_id)
        .order_by(models.Screen.name)
        .all()
    )
    return [
        TenantScreenOut(
            id=s.id,
            name=s.name,
            status=s.status or "offline",
            last_seen=_iso(s.last_seen),
            location=s.location,
            model=s.model,
            app_version=s.app_version,
            playback_state=s.playback_state or "idle",
        )
        for s in screens
    ]


@router.get("/tenants/{org_id}/content", response_model=List[TenantContentOut])
def get_tenant_content(
    org_id: int,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    _get_org(db, org_id)
    items = (
        db.query(models.Content)
        .filter(models.Content.organization_id == org_id)
        .order_by(models.Content.uploaded_at.desc())
        .limit(500)
        .all()
    )
    return [
        TenantContentOut(
            id=c.id,
            name=c.name,
            type=c.type,
            status=c.status,
            file_size_bytes=c.file_size_bytes or 0,
            uploaded_at=_iso(c.uploaded_at),
            thumbnail=resolve_media_url(c.thumbnail),
        )
        for c in items
    ]


@router.get("/tenants/{org_id}/users", response_model=List[TenantUserOut])
def get_tenant_users(
    org_id: int,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    _get_org(db, org_id)
    users = (
        db.query(models.User)
        .filter(models.User.organization_id == org_id)
        .order_by(models.User.created_at)
        .all()
    )
    return [
        TenantUserOut(
            id=u.id,
            username=u.username,
            email=u.email,
            full_name=u.full_name,
            role=u.role,
            is_active=u.is_active,
        )
        for u in users
    ]


@router.post("/tenants/{org_id}/approve", response_model=TenantSummaryOut)
def approve_tenant(
    org_id: int,
    request: Request,
    req: ApprovalRequest = ApprovalRequest(),
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Let a workspace in, on a package or on hand-set limits."""
    org = _get_org(db, org_id)
    before = _tenant_state(org)

    if req.plan_id is not None:
        plan = db.query(models.Plan).filter(models.Plan.id == req.plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Package not found")
        _apply_plan(org, plan)

    if req.max_screens is not None:
        org.max_screens = req.max_screens
    if req.max_ad_slots is not None:
        org.max_ad_slots = req.max_ad_slots

    org.status = "active"
    org.approved_at = models.utcnow()
    org.approved_by_user_id = scope.user.id
    org.rejection_reason = None
    _audit(db, scope, request, "tenant.approve", org=org, before=before, after=_tenant_state(org))
    db.commit()
    db.refresh(org)
    logger.info(
        "Org %s (ID %s) approved by %s: plan=%s screens=%s ads=%s",
        org.name, org.id, scope.user.username, org.plan_id, org.max_screens, org.max_ad_slots,
    )
    return _summarise(db, org)


class StorageTenantOut(BaseModel):
    organization_id: Optional[int] = None
    name: str
    prefix: str
    bucket_bytes: int = 0
    bucket_objects: int = 0
    # What the database says this workspace holds. Differs from bucket_bytes when objects
    # were orphaned by a deletion that did not reach the bucket, or uploaded under an older
    # prefix -- which is the whole reason both numbers are here.
    database_bytes: int = 0
    quota_bytes: int = 0


class StorageOut(BaseModel):
    configured: bool
    error: Optional[str] = None
    bucket: Optional[str] = None
    endpoint: Optional[str] = None
    total_bytes: int = 0
    object_count: int = 0
    # What the operator has said they are willing to pay for, if anything. Object storage
    # has no fixed size -- R2 and S3 both bill per GB stored with no ceiling -- so "how much
    # is left" only means something against a budget somebody chose.
    budget_bytes: Optional[int] = None
    # Every workspace's quota added up. Not a prediction of the bill: quotas are promises
    # about what a tenant MAY use, and overselling them is normal.
    committed_quota_bytes: int = 0
    cached: bool = False
    tenants: List[StorageTenantOut] = Field(default_factory=list)








class MaintenancePinOut(BaseModel):
    """The platform-wide PIN that opens a screen's maintenance door.

    Shown in full, deliberately. Secrecy is not what this protects: it is the thing an
    installer standing in front of a kiosked television needs, and hiding it from the
    operator who set it only means it gets written on a sticky note instead.
    """
    pin: Optional[str] = None


class MaintenancePinWrite(BaseModel):
    # Four digits, because it is typed on a TV remote's d-pad where anything longer or
    # non-numeric is genuinely painful. Blank clears it.
    pin: str = Field(default="", pattern=r"^(\d{4})?$")


@router.get("/maintenance-pin", response_model=MaintenancePinOut)
def get_maintenance_pin(
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """The fallback PIN used by any screen that has not been given its own."""
    from .screens import UNIVERSAL_PIN_KEY

    setting = db.query(models.SystemSetting).filter(
        models.SystemSetting.key == UNIVERSAL_PIN_KEY
    ).first()
    return MaintenancePinOut(pin=setting.value if setting else None)


@router.put("/maintenance-pin", response_model=MaintenancePinOut)
def set_maintenance_pin(
    req: MaintenancePinWrite,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Set one PIN that opens every screen which has no PIN of its own.

    A screen with kiosk enabled and no PIN has NO exit: home and back are swallowed by
    lock-task, so the only remaining move is a factory reset, which unpairs the panel and
    loses its playlist. The per-screen field is optional and usually skipped, so without a
    platform-wide fallback that state is the default rather than the exception.
    """
    from .screens import UNIVERSAL_PIN_KEY

    setting = db.query(models.SystemSetting).filter(
        models.SystemSetting.key == UNIVERSAL_PIN_KEY
    ).first()
    pin = req.pin.strip()
    # The PIN itself never reaches the trail; only whether one is set.
    _audit(db, scope, request, "platform.maintenance_pin.update", target_type="setting",
           target_id=UNIVERSAL_PIN_KEY, before={"set": setting is not None}, after={"set": bool(pin)})
    if not pin:
        if setting:
            db.delete(setting)
        db.commit()
        logger.info("Universal maintenance PIN cleared by %s", scope.user.username)
        return MaintenancePinOut(pin=None)
    if setting:
        setting.value = pin
        setting.updated_at = models.utcnow()
    else:
        db.add(models.SystemSetting(
            key=UNIVERSAL_PIN_KEY, value=pin,
            description="Fallback maintenance PIN for screens with none of their own",
        ))
    db.commit()
    logger.info("Universal maintenance PIN set by %s", scope.user.username)
    return MaintenancePinOut(pin=pin)


@router.get("/storage", response_model=StorageOut)
def platform_storage(
    refresh: bool = False,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """What the object store actually holds, and which workspace put it there.

    Per-tenant quotas answer "what may this workspace use". This answers "what am I being
    billed for", which nothing did -- the bucket is the thing with a cost attached, and it
    drifts from the database through orphans and renditions of deleted content.

    Measured by walking the bucket, cached for a few minutes because LIST is charged per
    request; `?refresh=true` forces it.
    """
    from ..media_urls import get_s3_config, tenant_storage_root
    from ..services.storage_service import (
        bucket_usage,
        organization_id_from_prefix,
        organization_storage_used,
    )

    usage = bucket_usage(force=refresh)
    organizations = {org.id: org for org in db.query(models.Organization).all()}

    # Accumulated per workspace, not per prefix. A workspace mid-migration holds objects
    # under both the legacy `org-<id>` and the current `tenants/<Slug>-<id>`, and a renamed
    # one holds them under every slug it has ever had -- all of which resolve to the same
    # id. Listed one row per prefix, those appeared as several workspaces, each repeating
    # the FULL database bytes and quota while showing only its share of the bucket, so a
    # tenant comfortably inside its quota could be read off this page as several times over.
    merged: dict[int, dict] = {}
    unattributed: List[StorageTenantOut] = []

    for prefix, totals in usage["by_prefix"].items():
        org_id = organization_id_from_prefix(prefix)
        org = organizations.get(org_id) if org_id else None
        if not org:
            # A prefix naming no workspace is still real bytes on the bill, so it is listed
            # rather than dropped -- that is how an orphan becomes visible at all.
            unattributed.append(StorageTenantOut(
                name=f"Unattributed ({prefix})",
                prefix=prefix,
                bucket_bytes=totals["bytes"],
                bucket_objects=totals["objects"],
            ))
            continue
        entry = merged.setdefault(org.id, {"prefixes": [], "bytes": 0, "objects": 0})
        entry["prefixes"].append(prefix)
        entry["bytes"] += totals["bytes"]
        entry["objects"] += totals["objects"]

    # Workspaces holding nothing in the bucket still belong in the list, or a tenant whose
    # uploads all failed looks identical to one that has none.
    rows: List[StorageTenantOut] = []
    for org in organizations.values():
        entry = merged.get(org.id)
        database_bytes = organization_storage_used(db, org.id)
        if entry is None and not database_bytes and not org.storage_quota_bytes:
            continue
        rows.append(StorageTenantOut(
            organization_id=org.id,
            name=org.name,
            # For a workspace with nothing stored, where its files WOULD land. This was
            # synthesized as `org-<id>`, a scheme nothing has minted since
            # tenant_storage_root landed, so every empty workspace advertised a prefix its
            # uploads would never use.
            prefix=", ".join(sorted(entry["prefixes"])) if entry else tenant_storage_root(org),
            bucket_bytes=entry["bytes"] if entry else 0,
            bucket_objects=entry["objects"] if entry else 0,
            database_bytes=database_bytes,
            quota_bytes=org.storage_quota_bytes,
        ))

    rows.extend(unattributed)
    rows.sort(key=lambda row: -row.bucket_bytes)

    budget = os.getenv("PLATFORM_STORAGE_BUDGET_BYTES", "").strip()
    config = get_s3_config()
    return StorageOut(
        configured=usage["configured"],
        error=usage.get("error"),
        bucket=usage.get("bucket") or config["bucket"],
        endpoint=config["endpoint_url"],
        total_bytes=usage["total_bytes"],
        object_count=usage["object_count"],
        budget_bytes=int(budget) if budget.isdigit() else None,
        committed_quota_bytes=sum(o.storage_quota_bytes or 0 for o in organizations.values()),
        cached=bool(usage.get("cached")),
        tenants=rows,
    )


@router.patch("/tenants/{org_id}/subscription", response_model=TenantSummaryOut)
def update_tenant_subscription(
    org_id: int,
    req: SubscriptionUpdateRequest,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Set, extend or end a workspace's paid window.

    The only lever over the lifecycle an operator had was suspend/reinstate, which is a
    different thing: suspension is a judgement about the customer, expiry is a fact about
    the calendar. Without this there was no way to say "they paid me offline, give them
    another month" except editing the database.
    """
    org = _get_org(db, org_id)
    before = _tenant_state(org)
    subscription = org.subscription
    if subscription is None:
        if org.plan_id is None:
            raise HTTPException(
                status_code=409,
                detail="This workspace has no package; assign one before setting a period.",
            )
        subscription = models.Subscription(
            organization_id=org.id,
            plan_id=org.plan_id,
            status="active",
            billing_period=req.billing_period or "one_time",
        )
        db.add(subscription)
        db.flush()

    now = models.utcnow()
    if req.extend_days is not None:
        # From the later of now and the end they already hold, so extending a live
        # subscription adds to it rather than truncating it back to today.
        current_end = subscription.current_period_end
        if current_end is not None and current_end.tzinfo is None:
            current_end = current_end.replace(tzinfo=timezone.utc)
        base = max(now, current_end) if current_end else now
        subscription.current_period_end = base + timedelta(days=req.extend_days)
        if subscription.current_period_start is None:
            subscription.current_period_start = now
    if req.period_end is not None:
        subscription.current_period_end = req.period_end
    if req.billing_period is not None:
        subscription.billing_period = req.billing_period

    if req.status == "active":
        subscription.status = "active"
        # Clearing the grace end matters: left set and in the past, it would expire them
        # again on the next sweep.
        subscription.grace_period_end = None
    elif req.status == "expired":
        subscription.status = "expired"
    elif subscription.status in (TERMINAL_SUBSCRIPTION_STATUSES | {"grace"}) and (
        req.extend_days is not None or req.period_end is not None
    ):
        # Giving a lapsed workspace a future window without saying "active" plainly means
        # reinstating it; leaving the status as it was would ignore the window just granted.
        # Every terminal status counts, not just expired/grace: read_only, cancelled and
        # completed also make subscription_state() answer "expired" whatever the window says.
        subscription.status = "active"
        subscription.grace_period_end = None

    subscription.updated_at = now
    db.flush()
    db.refresh(org)
    _audit(db, scope, request, "tenant.subscription.update", org=org, before=before,
           after={**_tenant_state(org), "request": req.model_dump(exclude_none=True)})
    db.commit()
    db.refresh(org)
    logger.info(
        "Subscription for org %s (ID %s) set by %s: status=%s period_end=%s",
        org.name, org.id, scope.user.username, subscription.status,
        subscription.current_period_end,
    )
    return _summarise(db, org)


@router.post("/tenants/{org_id}/grant", response_model=TenantSummaryOut)
def grant_custom_limits(
    org_id: int,
    req: GrantRequest,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Put one workspace on limits and features of your own choosing.

    Screens and ad slots already had per-tenant overrides, but clients, storage and the
    feature flags did not -- and features live on a Plan, so there was no way to give one
    workspace emergency alerts without editing the package every other workspace is on.

    This mints a hidden package for them, the same shape a paid custom request produces
    (`is_active=False`, a `custom-` slug the catalogue filters out), so effective_max_* and
    plan_features keep working unchanged and nothing new has to understand a third kind of
    limit. Re-granting edits that same package rather than accumulating one per change.
    """
    org = _get_org(db, org_id)
    before = {**_tenant_state(org), "features": plan_features(org.plan) if org.plan else {}}

    slug = f"custom-org-{org.id}"
    plan = db.query(models.Plan).filter(models.Plan.slug == slug).first()
    source = org.plan
    if plan is None:
        plan = models.Plan(
            name=req.name or f"Custom limits for {org.name}",
            slug=slug,
            monthly_price_paise=0,
            yearly_price_paise=0,
            price_paise=0,
            duration_days=req.days or (source.duration_days if source else 30),
            # Seeded from whatever they are on now, so a grant that sets only one field
            # does not silently zero the rest.
            # None, not 0, for a workspace on no package: that is "no limit" for screens, and 0
            # is a real limit of zero. A grant that touched only clients used to zero them.
            max_screens=source.max_screens if source else None,
            max_clients=source.max_clients if source else 0,
            max_ad_slots=source.max_ad_slots if source else 0,
            max_storage_bytes=source.max_storage_bytes if source else 0,
            feature_flags_json=json.dumps(plan_features(source) if source else {}, sort_keys=True),
            is_active=False,
        )
        db.add(plan)
        db.flush()
    elif req.name:
        plan.name = req.name

    if req.max_screens is not None:
        plan.max_screens = _screen_limit(req.max_screens)
    if req.max_ad_slots is not None:
        plan.max_ad_slots = _zero_is_unlimited(req.max_ad_slots)
    if req.max_clients is not None:
        plan.max_clients = _zero_is_unlimited(req.max_clients)
    if req.max_storage_bytes is not None:
        plan.max_storage_bytes = _zero_is_unlimited(req.max_storage_bytes)
    if req.days is not None:
        plan.duration_days = req.days
    if req.features is not None:
        plan.feature_flags_json = json.dumps(req.features, sort_keys=True)

    _apply_plan(org, plan)
    # A granted workspace is one you have decided about, so let it in.
    if org.status == "pending_approval":
        org.status = "active"
        org.approved_at = models.utcnow()
        org.approved_by_user_id = scope.user.id
        org.rejection_reason = None

    _audit(db, scope, request, "tenant.grant", org=org, before=before, after={
        **_tenant_state(org),
        "features": plan_features(plan),
        "plan_limits": {
            "max_screens": plan.max_screens, "max_ad_slots": plan.max_ad_slots,
            "max_clients": plan.max_clients, "max_storage_bytes": plan.max_storage_bytes,
            "duration_days": plan.duration_days,
        },
    })
    db.commit()
    db.refresh(org)
    logger.info(
        "Custom limits granted to org %s (ID %s) by %s: screens=%s ads=%s clients=%s features=%s",
        org.name, org.id, scope.user.username, plan.max_screens, plan.max_ad_slots,
        plan.max_clients, plan.feature_flags_json,
    )
    return _summarise(db, org)


@router.patch("/tenants/{org_id}/quota", response_model=TenantSummaryOut)
def update_tenant_quota(
    org_id: int,
    req: QuotaUpdateRequest,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Change a tenant's package or its individual limits, at any time."""
    org = _get_org(db, org_id)
    before = _tenant_state(org)

    if req.plan_id is not None:
        plan = db.query(models.Plan).filter(models.Plan.id == req.plan_id).first()
        if not plan:
            raise HTTPException(status_code=404, detail="Package not found")
        _apply_plan(org, plan)

    if req.max_screens is not None:
        org.max_screens = req.max_screens
    if req.max_ad_slots is not None:
        org.max_ad_slots = req.max_ad_slots

    _audit(db, scope, request, "tenant.quota.update", org=org, before=before, after=_tenant_state(org))
    db.commit()
    db.refresh(org)
    logger.info("Quota updated for org %s by %s: %s", org.name, scope.user.username, req.model_dump(exclude_none=True))
    return _summarise(db, org)


@router.post("/tenants/{org_id}/reject", response_model=TenantSummaryOut)
def reject_tenant(
    org_id: int,
    request: Request,
    req: RejectionRequest = RejectionRequest(),
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    org = _get_org(db, org_id)
    if org.id == scope.user.organization_id:
        raise HTTPException(status_code=400, detail="Cannot reject your own organization.")
    before = _tenant_state(org)
    org.status = "rejected"
    org.rejection_reason = req.reason
    _audit(db, scope, request, "tenant.reject", org=org, before=before,
           after={**_tenant_state(org), "rejection_reason": req.reason})
    db.commit()
    db.refresh(org)
    logger.info("Org %s (ID %s) rejected by %s", org.name, org.id, scope.user.username)
    return _summarise(db, org)


@router.post("/tenants/{org_id}/suspend", response_model=TenantSummaryOut)
def suspend_tenant(
    org_id: int,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Block a tenant. get_tenant_scope refuses every API call from them until reinstated."""
    org = _get_org(db, org_id)
    if org.id == scope.user.organization_id:
        raise HTTPException(status_code=400, detail="Cannot suspend your own organization.")
    before = _tenant_state(org)
    org.status = "suspended"
    _audit(db, scope, request, "tenant.suspend", org=org, before=before, after=_tenant_state(org))
    db.commit()
    db.refresh(org)
    logger.info("Org %s (ID %s) suspended by %s", org.name, org.id, scope.user.username)
    return _summarise(db, org)


@router.post("/tenants/{org_id}/reinstate", response_model=TenantSummaryOut)
def reinstate_tenant(
    org_id: int,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    org = _get_org(db, org_id)
    before = _tenant_state(org)
    org.status = "active"
    org.rejection_reason = None
    if org.approved_at is None:
        org.approved_at = models.utcnow()
    _audit(db, scope, request, "tenant.reinstate", org=org, before=before, after=_tenant_state(org))
    db.commit()
    db.refresh(org)
    logger.info("Org %s (ID %s) reinstated by %s", org.name, org.id, scope.user.username)
    return _summarise(db, org)


@router.delete("/tenants/{org_id}", response_model=TenantSummaryOut)
def remove_tenant(
    org_id: int,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Take a workspace off the platform, and start its countdown to being destroyed.

    Marks rather than deletes. The rows go 30 days later, in `purge_removed_tenants` --
    partly because a straight DELETE fails on the first of twelve unguarded foreign keys,
    and mostly because removing the wrong workspace should be survivable. Until the window
    closes, `restore_tenant` puts everything back exactly as it was.

    Access stops immediately: `User.organization_status` reports "removed", which every
    gate already refuses.
    """
    org = _get_org(db, org_id)
    if org.id == scope.user.organization_id:
        raise HTTPException(status_code=400, detail="Cannot remove your own organization.")
    if org.deleted_at is not None:
        raise HTTPException(status_code=409, detail="This workspace has already been removed.")

    before = _tenant_state(org)
    org.deleted_at = models.utcnow()
    _audit(db, scope, request, "tenant.remove", org=org, before=before,
           after={**_tenant_state(org), "purge_at": _iso(purge_due_at(org))})
    db.commit()
    db.refresh(org)
    logger.warning(
        "Org %s (ID %s) removed by %s; purges after %s days on %s",
        org.name, org.id, scope.user.username, TENANT_PURGE_AFTER_DAYS,
        _iso(purge_due_at(org)),
    )
    return _summarise(db, org)


@router.post("/tenants/{org_id}/restore", response_model=TenantSummaryOut)
def restore_tenant(
    org_id: int,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Undo a removal while the 30 days are still running.

    Nothing was destroyed, so there is nothing to rebuild: clearing the mark restores the
    workspace whole. Once the purge has run there is no row left for this to find, which is
    why it answers 404 rather than pretending.
    """
    org = _get_org(db, org_id)
    if org.deleted_at is None:
        raise HTTPException(status_code=409, detail="This workspace has not been removed.")

    before = _tenant_state(org)
    org.deleted_at = None
    _audit(db, scope, request, "tenant.restore", org=org, before=before, after=_tenant_state(org))
    db.commit()
    db.refresh(org)
    logger.info("Org %s (ID %s) restored by %s", org.name, org.id, scope.user.username)
    return _summarise(db, org)


@router.delete("/tenants/{org_id}/permanent")
def delete_tenant_permanently(
    org_id: int,
    confirm_name: str,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Destroy a removed workspace now instead of waiting out its 30 days.

    Only a workspace already removed: the removal is the first confirmation, and it is what
    locked everyone out. The typed name is checked here too, not just in the browser.
    """
    org = _get_org(db, org_id)
    if org.id == scope.user.organization_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own organization.")
    if org.deleted_at is None:
        raise HTTPException(status_code=409, detail="Remove this workspace before deleting it permanently.")
    if confirm_name.strip() != org.name:
        raise HTTPException(status_code=400, detail="The name typed does not match this workspace.")

    name = org.name
    before = _tenant_state(org)
    try:
        report = purge_organization(db, org, immediately=True)
        audit.record(
            db, actor=scope.user, action="tenant.purge", request=request,
            target_org_id=org_id, target_type="organization", target_id=org_id,
            before={**before, "name": name}, after={"rows": report["rows"],
            "objects_deleted": report["objects_deleted"], "objects_missed": report["objects_missed"]},
            acting_org_id=scope.acting_organization_id,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Permanent delete of org %s (ID %s) failed", name, org_id)
        # Files are deleted before rows (so a failure leaves rows to retry against), which
        # means some media may already be gone: retrying is safe, restoring is not.
        raise HTTPException(
            status_code=500,
            detail="Could not finish deleting this workspace. Its records were kept so you can "
                   "retry the delete, but some of its files may already be gone -- do not restore it.",
        )
    forget_cached_usage()
    logger.warning(
        "Org %s (ID %s) permanently deleted by %s: %s",
        name, org_id, scope.user.username, report["rows"],
    )
    return report


# --------------------------------------------------------------------------- packages


def _plan_out(plan: models.Plan) -> PlanOut:
    """PlanOut with feature_flags decoded from the stored JSON.

    Built by hand rather than from_attributes because feature_flags lives as a JSON string
    on the row (feature_flags_json) and Pydantic cannot decode it into a dict on its own.
    """
    return PlanOut(
        id=plan.id,
        name=plan.name,
        slug=plan.slug,
        monthly_price_paise=plan.monthly_price_paise,
        yearly_price_paise=plan.yearly_price_paise,
        price_paise=plan.price_paise,
        duration_days=plan.duration_days,
        max_screens=plan.max_screens,
        max_clients=plan.max_clients,
        max_storage_bytes=plan.max_storage_bytes,
        max_ad_slots=plan.max_ad_slots,
        feature_flags=plan_features(plan),
        is_active=plan.is_active,
    )


@router.get("/plans", response_model=List[PlanOut])
def list_plans(
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Every package, active or not.

    /api/billing/plans is the tenant-facing view and shows only active ones; this is the
    operator's, which has to show a retired package so it can be re-activated.

    Bespoke plans minted for a paid custom request (slug 'custom-<id>') are hidden: they are
    one tenant's negotiated shape, not a package on the shelf, and would only clutter the
    catalogue.
    """
    plans = (
        db.query(models.Plan)
        .filter(~models.Plan.slug.like("custom-%"))
        .order_by(models.Plan.price_paise)
        .all()
    )
    return [_plan_out(plan) for plan in plans]


@router.post("/plans", response_model=PlanOut, status_code=201)
def create_plan(
    payload: PlanWrite,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    if db.query(models.Plan).filter(models.Plan.slug == payload.slug).first():
        raise HTTPException(status_code=409, detail="A package with that slug already exists")
    fields = payload.model_dump()
    # feature_flags is a dict on the wire but a JSON string on the row. This is the fix for
    # the old create_plan, which hardcoded "{}" and made features un-settable at all.
    features = fields.pop("feature_flags")
    plan = models.Plan(**_store_limits(fields), feature_flags_json=json.dumps(features, sort_keys=True))
    db.add(plan)
    db.flush()
    _audit(db, scope, request, "plan.create", target_type="plan", target_id=plan.id,
           after=_plan_out(plan).model_dump())
    db.commit()
    db.refresh(plan)
    logger.info("Package %s created by %s", plan.slug, scope.user.username)
    return _plan_out(plan)


@router.patch("/plans/{plan_id}", response_model=PlanOut)
def update_plan(
    plan_id: int,
    payload: PlanPatch,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Edit a package in place.

    The slug is deliberately not editable: /api/billing/checkout resolves the payment
    provider's plan id from an env var named after it (RAZORPAY_{SLUG}_{PERIOD}_PLAN_ID),
    so renaming a slug would silently break checkout for everyone on that package.
    """
    plan = db.query(models.Plan).filter(models.Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Package not found")
    before = _plan_out(plan).model_dump()
    fields = payload.model_dump(exclude_unset=True, exclude_none=True)
    features = fields.pop("feature_flags", None)
    if features is not None:
        plan.feature_flags_json = json.dumps(features, sort_keys=True)
    for field, value in _store_limits(fields).items():
        setattr(plan, field, value)
    _audit(db, scope, request, "plan.update", target_type="plan", target_id=plan.id,
           before=before, after=_plan_out(plan).model_dump())
    db.commit()
    db.refresh(plan)
    logger.info("Package %s updated by %s", plan.slug, scope.user.username)
    return _plan_out(plan)


@router.delete("/plans/{plan_id}")
def delete_plan(
    plan_id: int,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Retire a package.

    Deactivated, never deleted, when tenants are on it: organizations.plan_id and
    subscriptions.plan_id both point here, so a hard delete would either fail on the
    foreign key or orphan live workspaces.
    """
    plan = db.query(models.Plan).filter(models.Plan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Package not found")

    before = _plan_out(plan).model_dump()
    # Both columns, not just the organisation's: a subscription can outlive a tenant
    # being moved off the package, and its FK made the hard delete fail with a 500.
    in_use = db.query(models.Organization).filter(models.Organization.plan_id == plan_id).count()
    subscribed = db.query(models.Subscription).filter(models.Subscription.plan_id == plan_id).count()
    if in_use or subscribed:
        plan.is_active = False
        _audit(db, scope, request, "plan.deactivate", target_type="plan", target_id=plan.id,
               before=before, after={**before, "is_active": False})
        db.commit()
        return {
            "status": "deactivated",
            "detail": (
                f"{max(in_use, subscribed)} workspace(s) are on or subscribed to this package, "
                "so it was retired rather than deleted."
            ),
        }

    _audit(db, scope, request, "plan.delete", target_type="plan", target_id=plan.id, before=before)
    db.delete(plan)
    db.commit()
    logger.info("Package %s deleted by %s", plan.slug, scope.user.username)
    return {"status": "deleted"}


# ------------------------------------------------------------------ custom-plan requests


@router.get("/custom-requests", response_model=List[schemas.CustomPlanRequestResponse])
def list_custom_requests(
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Bespoke-package requests that are still live: awaiting a price, awaiting payment, or
    already paid.

    Paid ones stay on the list rather than dropping off it. They are the ones an operator most
    often needs to revise -- a tenant on a custom package who buys more screens is an edit to
    the request they already paid for, and a row that is not returned here cannot be edited at
    all. Only rejected ones disappear.
    """
    from .billing import serialize_custom_request

    requests = (
        db.query(models.CustomPlanRequest)
        .filter(models.CustomPlanRequest.status.in_(("requested", "priced", "paid")))
        .order_by(models.CustomPlanRequest.created_at.asc())
        .all()
    )
    return [serialize_custom_request(request, include_org=True) for request in requests]


@router.post("/custom-requests/{request_id}/price", response_model=schemas.CustomPlanRequestResponse)
def price_custom_request(
    request_id: int,
    payload: schemas.CustomPlanRequestPrice,
    http_request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Set what a bespoke request costs. The tenant pays it from their storefront."""
    from .billing import serialize_custom_request

    request = db.query(models.CustomPlanRequest).filter(
        models.CustomPlanRequest.id == request_id
    ).first()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    if request.status == "paid":
        raise HTTPException(status_code=409, detail="This request has already been paid")
    before = {"status": request.status, "price_paise": request.price_paise}
    request.price_paise = payload.price_paise
    request.status = "priced"
    request.updated_at = models.utcnow()
    _audit(db, scope, http_request, "custom_request.price", org_id=request.organization_id,
           target_type="custom_request", target_id=request.id, before=before,
           after={"status": "priced", "price_paise": payload.price_paise})
    db.commit()
    db.refresh(request)
    logger.info("Custom request %s priced at %s paise by %s", request.id, payload.price_paise, scope.user.username)
    return serialize_custom_request(request, include_org=True)


@router.patch("/custom-requests/{request_id}", response_model=schemas.CustomPlanRequestResponse)
def update_custom_request(
    request_id: int,
    payload: schemas.CustomPlanRequestUpdate,
    http_request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Revise a bespoke package: its caps, its features, its price -- at any status.

    Before payment this just edits the row. After payment it must also move the plan that
    payment minted, because that plan is what the workspace is actually running on:
    `Organization.effective_max_*` reads screens/clients/ad-slots straight through
    `org.plan`, so writing them onto `custom-<id>` moves the tenant immediately and without
    a second charge. Storage is the exception -- `storage_quota_bytes` is a copied column,
    not a derived one -- so it is re-synced explicitly here or the new allowance silently
    would not apply.

    Repricing a paid request does NOT re-bill and does not reopen it for payment; it
    corrects the record of what was charged. `status` is deliberately untouched, since
    dropping a paid request back to 'priced' would invite the tenant to pay for it twice.
    """
    from .billing import serialize_custom_request

    request = db.query(models.CustomPlanRequest).filter(
        models.CustomPlanRequest.id == request_id
    ).first()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    if request.status == "rejected":
        raise HTTPException(status_code=409, detail="A rejected request cannot be edited")

    fields = payload.model_dump(exclude_unset=True)
    _audit(db, scope, http_request, "custom_request.update", org_id=request.organization_id,
           target_type="custom_request", target_id=request.id,
           before={"status": request.status, "price_paise": request.price_paise},
           after={**fields, "status": request.status})
    if "feature_flags" in fields:
        request.feature_flags_json = json.dumps(fields.pop("feature_flags") or {})
    for name, value in fields.items():
        setattr(request, name, value)
    request.updated_at = models.utcnow()

    if request.status == "paid":
        plan = db.query(models.Plan).filter(models.Plan.slug == f"custom-{request.id}").first()
        if plan is not None:
            plan.max_screens = request.max_screens
            plan.max_clients = request.max_clients
            plan.max_ad_slots = request.max_ad_slots
            plan.max_storage_bytes = request.max_storage_bytes
            plan.duration_days = request.duration_days
            plan.feature_flags_json = request.feature_flags_json
            plan.price_paise = request.price_paise
            org = db.query(models.Organization).filter(
                models.Organization.id == request.organization_id
            ).first()
            if org is not None and org.plan_id == plan.id:
                # The one cap that does not derive from the plan. Left alone, a tenant
                # granted more space would keep hitting the old ceiling.
                org.storage_quota_bytes = plan.max_storage_bytes
                # A per-org override beats the plan (see Organization.effective_max_*), and
                # several paths leave one behind. Revising the package the tenant is on is an
                # explicit statement of what they now get, so clear the override for exactly
                # the caps being revised -- otherwise the edit saves, reports success, and
                # changes nothing the tenant can see, which is indistinguishable from broken.
                for field in ("max_screens", "max_clients", "max_ad_slots"):
                    if field in fields:
                        setattr(org, field, 0)

    db.commit()
    db.refresh(request)
    logger.info(
        "Custom request %s revised by %s (status %s)", request.id, scope.user.username, request.status
    )
    return serialize_custom_request(request, include_org=True)


@router.post("/custom-requests/{request_id}/reject", response_model=schemas.CustomPlanRequestResponse)
def reject_custom_request(
    request_id: int,
    http_request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    from .billing import serialize_custom_request

    request = db.query(models.CustomPlanRequest).filter(
        models.CustomPlanRequest.id == request_id
    ).first()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    if request.status == "paid":
        raise HTTPException(status_code=409, detail="A paid request cannot be rejected")
    _audit(db, scope, http_request, "custom_request.reject", org_id=request.organization_id,
           target_type="custom_request", target_id=request.id,
           before={"status": request.status}, after={"status": "rejected"})
    request.status = "rejected"
    request.updated_at = models.utcnow()
    db.commit()
    db.refresh(request)
    logger.info("Custom request %s rejected by %s", request.id, scope.user.username)
    return serialize_custom_request(request, include_org=True)


# ------------------------------------------------------------------ fleet version monitor

class FleetScreenOut(BaseModel):
    id: int
    name: Optional[str] = None
    organization_id: int
    organization_name: str
    online: bool
    app_version: Optional[str] = None
    # A pinned build (canary ring); null means the screen follows the global released build.
    target_version_code: Optional[int] = None
    update_status: Optional[str] = None
    update_failure_count: int = 0
    last_seen: Optional[str] = None


class FleetOverviewOut(BaseModel):
    total: int
    online: int
    # The highest RELEASED build — what an unpinned screen should converge to.
    latest_version_name: Optional[str] = None
    latest_version_code: Optional[int] = None
    # Online screens already reporting the latest released version_name.
    on_latest: int
    # Screens mid-update or rolled back, so an operator can watch a rollout land.
    updating: int
    failed: int
    # app_version -> count, so the console can show the version spread at a glance.
    versions: Dict[str, int]
    screens: List[FleetScreenOut]
    # Whether screens may still call device endpoints with no credential, and how many hold no
    # device secret and would be stranded if that were switched off.
    legacy_device_auth: bool = False
    screens_without_secret: int = 0


@router.get("/fleet", response_model=FleetOverviewOut)
def fleet_overview(
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Every TV across every tenant, with the version it is on and its update state.

    The per-tenant screen lists already show a screen's version; this is the one place the
    platform operator can see the whole fleet's version spread and watch a release roll out.
    """
    latest = (
        db.query(models.AppRelease)
        .filter(models.AppRelease.rollout_state == "released")
        .order_by(models.AppRelease.version_code.desc())
        .first()
    )
    org_names = {org.id: org.name for org in db.query(models.Organization).all()}

    # Real fleet screens only: archived rows and never-paired registrations are not TVs an
    # operator is monitoring.
    screens = db.query(models.Screen).filter(*fleet_screen_filter()).all()

    now = models.utcnow()
    rows: List[FleetScreenOut] = []
    versions: Dict[str, int] = {}
    online = on_latest = updating = failed = 0

    for screen in screens:
        # From last_seen, not Screen.status, which lags until the health pass flips it.
        is_online = screen_is_online(screen.last_seen, now)
        if is_online:
            online += 1
        label = screen.app_version or "unknown"
        versions[label] = versions.get(label, 0) + 1
        if latest and screen.app_version and screen.app_version == latest.version_name and is_online:
            on_latest += 1
        if screen.update_status in ("pending", "downloading", "installing"):
            updating += 1
        if screen.update_status in ("failed", "rolled_back") or screen.update_failure_count > 0:
            failed += 1
        rows.append(
            FleetScreenOut(
                id=screen.id,
                name=screen.name,
                organization_id=screen.organization_id,
                organization_name=org_names.get(screen.organization_id, "—"),
                online=is_online,
                app_version=screen.app_version,
                target_version_code=screen.target_version_code,
                update_status=screen.update_status,
                update_failure_count=screen.update_failure_count or 0,
                last_seen=_iso(screen.last_seen),
            )
        )

    # Offline first, then furthest behind — the screens an operator needs to look at sit on
    # top rather than being buried under the healthy majority.
    rows.sort(key=lambda r: (r.online, r.app_version or ""))

    return FleetOverviewOut(
        total=len(rows),
        online=online,
        latest_version_name=latest.version_name if latest else None,
        latest_version_code=latest.version_code if latest else None,
        on_latest=on_latest,
        updating=updating,
        failed=failed,
        versions=versions,
        screens=rows,
        legacy_device_auth=legacy_device_auth_allowed(),
        screens_without_secret=screens_without_secret(db),
    )


# --------------------------------------------------------------------------- demo reel


@router.get("/demo-video")
def get_universal_demo_video(db: Session = Depends(database.get_db)):
    """The reel a pending tenant's TVs play. Unauthenticated: sync_tv serves it to devices."""
    setting = db.query(models.SystemSetting).filter(models.SystemSetting.key == DEMO_VIDEO_KEY).first()
    return {
        "url": setting.value if setting else DEFAULT_DEMO_VIDEO,
        "description": setting.description if setting else "Default Universal Demo Video",
    }


def _store_demo_video(db: Session, url: str, description: Optional[str]) -> models.SystemSetting:
    setting = db.query(models.SystemSetting).filter(models.SystemSetting.key == DEMO_VIDEO_KEY).first()
    if not setting:
        setting = models.SystemSetting(key=DEMO_VIDEO_KEY, value=url, description=description or "Universal Demo Video")
        db.add(setting)
    else:
        setting.value = url
        if description:
            setting.description = description
    db.commit()
    return setting


def _current_demo_video(db: Session) -> Optional[str]:
    setting = db.query(models.SystemSetting).filter(models.SystemSetting.key == DEMO_VIDEO_KEY).first()
    return setting.value if setting else None


@router.post("/demo-video")
def set_universal_demo_video(
    payload: DemoVideoPayload,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    _audit(db, scope, request, "platform.demo_video.update", target_type="setting",
           target_id=DEMO_VIDEO_KEY, before={"url": _current_demo_video(db)}, after={"url": payload.url})
    setting = _store_demo_video(db, payload.url, payload.description)
    logger.info("Universal demo reel set to %s by %s", setting.value, scope.user.username)
    return {"status": "ok", "url": setting.value}


DEMO_VIDEO_MAX_BYTES = 500 * 1024 * 1024


@router.post("/demo-video/upload")
def upload_universal_demo_video(
    request: Request,
    file: UploadFile = File(...),
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    from .. import media_storage

    # Extension taken from a fixed allow-list rather than from the upload's own filename,
    # which is attacker-controlled and lands under a directory served by StaticFiles.
    extension = os.path.splitext(file.filename or "")[1].lower()
    if extension not in {".mp4", ".webm", ".mov", ".m4v"}:
        raise HTTPException(status_code=400, detail="Demo reel must be an .mp4, .webm, .mov or .m4v file")
    file.file.seek(0, os.SEEK_END)
    size = file.file.tell()
    file.file.seek(0)
    if size > DEMO_VIDEO_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Demo reel must be 500 MB or smaller")

    # Through media_storage like every tenant upload, so it lands in object storage when
    # that is configured instead of on the instance disk, which a redeploy wipes.
    key = f"platform/demo/demo_reel_{uuid.uuid4().hex[:8]}{extension}"
    with tempfile.NamedTemporaryFile(delete=False, suffix=extension) as tmp:
        shutil.copyfileobj(file.file, tmp)
        tmp_path = pathlib.Path(tmp.name)
    try:
        stored = media_storage.store(tmp_path, key)
    finally:
        tmp_path.unlink(missing_ok=True)

    resolved_url = resolve_media_url(stored) or stored
    _audit(db, scope, request, "platform.demo_video.update", target_type="setting",
           target_id=DEMO_VIDEO_KEY, before={"url": _current_demo_video(db)},
           after={"url": resolved_url, "filename": file.filename, "size_bytes": size})
    _store_demo_video(db, resolved_url, f"Universal Demo Video ({file.filename})")

    logger.info("Universal demo reel updated by %s (%s)", scope.user.username, resolved_url)
    return {
        "status": "ok",
        "url": resolved_url,
        "filename": file.filename,
        "message": f"Universal demo reel '{file.filename}' uploaded and applied to all pending TV displays.",
    }


class UserRoleUpdate(BaseModel):
    role: str = Field(..., description="Role to assign: super_admin, owner, editor, viewer")
    # Granting platform-wide access is the one role change that cannot be undone by the
    # person it is done to, so it is confirmed by typing their username, here and not just
    # in the browser.
    confirm_username: Optional[str] = None


@router.patch("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    payload: UserRoleUpdate,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Promote or demote any user account to/from super_admin or tenant roles."""
    if payload.role not in ("super_admin", "owner", "editor", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role. Must be super_admin, owner, editor, or viewer.")

    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Refusing self-demotion is also what guarantees an administrator always remains: the
    # caller is one, and is the only account this route cannot demote.
    if user.id == scope.user.id and payload.role != "super_admin":
        raise HTTPException(status_code=400, detail="You cannot remove your own platform administrator role.")
    if payload.role == "super_admin" and user.role != "super_admin":
        if (payload.confirm_username or "").strip() != user.username:
            raise HTTPException(
                status_code=400,
                detail="Type the user's username to confirm granting platform administrator access.",
            )

    _audit(db, scope, request, "user.role.update", org_id=user.organization_id, target_type="user",
           target_id=user.id, before={"role": user.role, "username": user.username},
           after={"role": payload.role, "username": user.username})
    user.role = payload.role
    db.commit()
    db.refresh(user)
    logger.info("User %s (ID %s) role updated to %s by Super Admin %s", user.username, user.id, user.role, scope.user.username)
    return {
        "status": "ok",
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "message": f"User '{user.username}' is now assigned role '{user.role}'.",
    }



# ------------------------------------------------------------------------------------------
# Admin Billing: what tenants have paid, the contact address shown on a custom-plan email
# draft, and whether SMTP is actually working. Phase 3.2.

CONTACT_EMAIL_KEY = "platform_contact_email"


class PlatformPaymentOut(BaseModel):
    id: int
    organization_id: int
    organization_name: Optional[str] = None
    kind: str
    plan_id: Optional[int] = None
    custom_request_id: Optional[int] = None
    amount_paise: int
    currency: str
    provider: str
    provider_order_id: Optional[str] = None
    provider_payment_id: Optional[str] = None
    status: str
    paid_at: datetime


@router.get("/payments", response_model=List[PlatformPaymentOut])
def list_platform_payments(
    response: Response,
    organization_id: Optional[int] = None,
    kind: Optional[str] = None,
    provider: Optional[str] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = 200,
    offset: int = Query(default=0, ge=0),
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """What tenants have paid the platform -- view only. Amounts come from the provider,
    recorded once by the shared activation path at the moment each purchase was captured.

    Same paging contract as /tenants: the body stays a plain list (so an older caller
    using just `limit` keeps working unchanged), and X-Total-Count carries the count of
    everything the filters matched, not just this page."""
    query = db.query(models.PlatformPayment, models.Organization.name).join(
        models.Organization, models.Organization.id == models.PlatformPayment.organization_id
    )
    if organization_id is not None:
        query = query.filter(models.PlatformPayment.organization_id == organization_id)
    if kind:
        query = query.filter(models.PlatformPayment.kind == kind)
    if provider:
        query = query.filter(models.PlatformPayment.provider == provider)
    if since is not None:
        query = query.filter(models.PlatformPayment.paid_at >= since)
    if until is not None:
        query = query.filter(models.PlatformPayment.paid_at <= until)
    response.headers[TOTAL_COUNT_HEADER] = str(query.count())
    rows = (
        query.order_by(models.PlatformPayment.paid_at.desc())
        .offset(offset).limit(min(max(limit, 1), 1000)).all()
    )
    return [
        PlatformPaymentOut(
            id=payment.id, organization_id=payment.organization_id, organization_name=org_name,
            kind=payment.kind, plan_id=payment.plan_id, custom_request_id=payment.custom_request_id,
            amount_paise=payment.amount_paise, currency=payment.currency, provider=payment.provider,
            provider_order_id=payment.provider_order_id, provider_payment_id=payment.provider_payment_id,
            status=payment.status, paid_at=payment.paid_at,
        )
        for payment, org_name in rows
    ]


class ContactEmailOut(BaseModel):
    email: Optional[str] = None


class ContactEmailWrite(BaseModel):
    email: str = Field(default="", max_length=254)


@router.get("/settings/contact-email", response_model=ContactEmailOut)
def get_contact_email(
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    """Where a custom-plan request's mailto draft is addressed. Unset until an operator sets
    one here; the tenant-facing redirect is simply skipped until then (see billing.py's
    /billing/contact)."""
    setting = db.query(models.SystemSetting).filter(models.SystemSetting.key == CONTACT_EMAIL_KEY).first()
    return ContactEmailOut(email=setting.value if setting else None)


@router.put("/settings/contact-email", response_model=ContactEmailOut)
def set_contact_email(
    payload: ContactEmailWrite,
    request: Request,
    scope: TenantScope = Depends(require_super_admin),
    db: Session = Depends(database.get_db),
):
    email = payload.email.strip()
    if email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
        raise HTTPException(status_code=422, detail="That does not look like an email address")
    setting = db.query(models.SystemSetting).filter(models.SystemSetting.key == CONTACT_EMAIL_KEY).first()
    _audit(db, scope, request, "platform.contact_email.update", target_type="setting",
           target_id=CONTACT_EMAIL_KEY, before={"email": setting.value if setting else None},
           after={"email": email or None})
    if not email:
        if setting:
            db.delete(setting)
        db.commit()
        logger.info("Platform contact email cleared by %s", scope.user.username)
        return ContactEmailOut(email=None)
    if setting:
        setting.value = email
        setting.updated_at = models.utcnow()
    else:
        db.add(models.SystemSetting(
            key=CONTACT_EMAIL_KEY, value=email,
            description="Address a tenant's custom-plan request email draft is addressed to",
        ))
    db.commit()
    logger.info("Platform contact email set by %s", scope.user.username)
    return ContactEmailOut(email=email)


class EmailStatusOut(BaseModel):
    is_configured: bool
    sender: Optional[str] = None
    missing_variables: List[str] = Field(default_factory=list)


@router.get("/settings/email-status", response_model=EmailStatusOut)
def platform_email_status(scope: TenantScope = Depends(require_super_admin)):
    """Whether SMTP is set up on this deployment, for the Billing page's status card.
    Mirrors placements.py's tenant-facing /email-status."""
    from .. import mailer

    configured = mailer.is_configured()
    return EmailStatusOut(
        is_configured=configured,
        sender=mailer.sender() if configured else None,
        missing_variables=[] if configured else mailer.missing_variables(),
    )


class TestEmailRequest(BaseModel):
    to: str = Field(max_length=254)


@router.post("/settings/test-email")
@limiter.limit("5/minute")
def send_test_email(
    request: Request,
    payload: TestEmailRequest,
    scope: TenantScope = Depends(require_super_admin),
):
    """Prove SMTP actually works, with the real error shown rather than hidden behind a
    generic failure -- the one question "I set the variables in Render" cannot answer by
    itself is whether the server can actually reach and authenticate to that SMTP host."""
    from .. import mailer

    to = payload.to.strip()
    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", to):
        raise HTTPException(status_code=422, detail="That does not look like an email address")
    try:
        mailer.send(
            to=to,
            subject="MR Signage - test email",
            body=(
                f"This confirms SMTP is working on this deployment.\n\n"
                f"Sent by {scope.user.username} from the admin Billing page."
            ),
        )
    except mailer.MailNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - surfacing the real SMTP error is the whole point
        raise HTTPException(status_code=502, detail=f"Could not send the email: {exc}")
    _audit(scope.db, scope, request, "platform.test_email.send", target_type="email", after={"to": to})
    scope.db.commit()
    return {"status": "sent", "to": to}
