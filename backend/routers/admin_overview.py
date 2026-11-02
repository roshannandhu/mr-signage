"""Mission Control: GET /api/admin/overview and /api/admin/inbox-counts.

Separate from admin.py (already ~2000 lines) because this is one cohesive thing -- the
platform's own dashboard -- not another CRUD surface. Every number here is a bulk
aggregate query, never a per-row Python loop: at 30+ tenants the old per-tenant pattern
this file replaces (see admin_metrics.py's docstring) cost real seconds per request.

The whole response is cached in process memory for CACHE_SECONDS, keyed by range -- the
same shape storage_service.py's bucket-listing cache uses, not Redis: get_redis() returns
an ASYNC client and this is a sync route (matching the rest of admin.py), so reusing it
here would need an event loop this route doesn't have. It is read far more often than the
numbers underneath it change -- the dashboard someone leaves open re-polls it -- so an
in-process cache is enough; it just means a multi-process deployment recomputes it once
per worker rather than sharing one answer, which is a fine trade at this read volume.
"""
import logging
import time
from datetime import datetime, timedelta
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel
from sqlalchemy import func

from .. import models
from ..tenancy import TenantScope, require_super_admin

logger = logging.getLogger(__name__)

router = APIRouter()

CACHE_SECONDS = 30
_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}
_cache: dict[str, tuple[float, dict]] = {}


class DayPoint(BaseModel):
    date: str
    value: int


class OverviewOut(BaseModel):
    range: Literal["7d", "30d", "90d"]
    generated_at: str
    cached: bool

    tenants_active: int
    tenants_pending: int
    tenants_suspended: int
    signups_by_day: List[DayPoint]

    screens_total: int
    screens_online: int
    screens_offline_24h: int
    on_latest_version_pct: Optional[float] = None

    alerts_critical_open: int
    alerts_warning_open: int

    plays_online_by_day: List[DayPoint]
    plays_offline_by_day: List[DayPoint]

    revenue_paise_by_day: List[DayPoint]
    revenue_paise_total: int

    renewals_due_14d_count: int
    renewals_due_14d_value_paise: int

    storage_used_bytes: int

    pending_approvals: int
    pending_custom_requests: int

    recent_activity: List[dict]


class InboxCountsOut(BaseModel):
    pending_approvals: int
    new_custom_requests: int
    critical_alerts: int
    failed_payments_7d: int


def _day_series(db, model, date_column, since, *, value_expr=None, extra_filter=None) -> List[DayPoint]:
    """COUNT(*) or SUM(value_expr) per calendar day since `since`, zero-filled.

    One query, not one per day -- the zero-fill (days with no rows) happens in Python
    over a dict, which is the cheap side of this trade.
    """
    agg = func.count() if value_expr is None else func.coalesce(func.sum(value_expr), 0)
    query = db.query(func.date(date_column).label("day"), agg.label("value")).filter(date_column >= since)
    if extra_filter is not None:
        query = query.filter(extra_filter)
    rows = query.group_by(func.date(date_column)).all()
    by_day = {str(day): int(value) for day, value in rows}
    points: List[DayPoint] = []
    cursor = since.date()
    today = models.utcnow().date()
    while cursor <= today:
        key = str(cursor)
        points.append(DayPoint(date=key, value=by_day.get(key, 0)))
        cursor += timedelta(days=1)
    return points


def _build_overview(db, range_key: str) -> dict:
    now = models.utcnow()
    since = now - timedelta(days=_RANGE_DAYS[range_key])

    org = models.Organization
    status_counts = dict(
        db.query(org.status, func.count())
        .filter(org.deleted_at.is_(None))
        .group_by(org.status)
        .all()
    )

    signups = _day_series(db, org, org.created_at, since)

    screen = models.Screen
    from ..services.admin_metrics import fleet_screen_filter, online_cutoff

    total_screens = db.query(func.count()).select_from(screen).filter(*fleet_screen_filter()).scalar() or 0
    online_screens = (
        db.query(func.count()).select_from(screen)
        .filter(*fleet_screen_filter(), screen.last_seen >= online_cutoff(now))
        .scalar() or 0
    )
    offline_24h = (
        db.query(func.count()).select_from(screen)
        .filter(*fleet_screen_filter(), screen.last_seen < now - timedelta(hours=24))
        .scalar() or 0
    )

    latest = (
        db.query(models.AppRelease)
        .filter(models.AppRelease.rollout_state == "released")
        .order_by(models.AppRelease.version_code.desc())
        .first()
    )
    on_latest_pct = None
    if latest and online_screens:
        on_latest = (
            db.query(func.count()).select_from(screen)
            .filter(*fleet_screen_filter(), screen.last_seen >= online_cutoff(now),
                    screen.app_version == latest.version_name)
            .scalar() or 0
        )
        on_latest_pct = round(100.0 * on_latest / online_screens, 1)

    alert = models.Alert
    alert_counts = dict(
        db.query(alert.severity, func.count())
        .filter(alert.resolved_at.is_(None))
        .group_by(alert.severity)
        .all()
    )

    rollup = models.PlayLogHourlyRollup
    plays_online = _day_series(
        db, rollup, rollup.date_hour, since,
        value_expr=rollup.total_plays - rollup.offline_plays,
    )
    plays_offline = _day_series(db, rollup, rollup.date_hour, since, value_expr=rollup.offline_plays)

    # "captured" only -- the one status production code ever writes (a charge that fails
    # never reaches record_payment at all), but a future provider integration could record
    # others, and summing every row regardless of status would count money never collected.
    payment = models.PlatformPayment
    revenue_by_day = _day_series(
        db, payment, payment.paid_at, since,
        value_expr=payment.amount_paise, extra_filter=payment.status == "captured",
    )
    revenue_total = sum(p.value for p in revenue_by_day)

    sub = models.Subscription
    due_cutoff = now + timedelta(days=14)
    renewals = (
        db.query(sub.organization_id, sub.plan_id)
        .filter(sub.current_period_end.isnot(None), sub.current_period_end <= due_cutoff,
                sub.current_period_end >= now, sub.status == "active")
        .all()
    )
    renewal_plan_ids = {plan_id for _, plan_id in renewals if plan_id is not None}
    plan_prices = {}
    if renewal_plan_ids:
        plan_prices = dict(
            db.query(models.Plan.id, models.Plan.monthly_price_paise)
            .filter(models.Plan.id.in_(renewal_plan_ids)).all()
        )
    renewals_value = sum(plan_prices.get(plan_id, 0) for _, plan_id in renewals)

    storage_total = (
        db.query(func.coalesce(func.sum(models.Content.file_size_bytes), 0)).scalar() or 0
    ) + (
        db.query(func.coalesce(func.sum(models.MediaRendition.file_size_bytes), 0)).scalar() or 0
    )

    pending_approvals = db.query(func.count()).select_from(org).filter(org.status == "pending_approval").scalar() or 0
    pending_custom = (
        db.query(func.count()).select_from(models.CustomPlanRequest)
        .filter(models.CustomPlanRequest.status == "requested").scalar() or 0
    )

    recent = (
        db.query(models.AdminAuditEvent)
        .order_by(models.AdminAuditEvent.id.desc())
        .limit(10)
        .all()
    )
    recent_activity = [
        {
            "id": e.id,
            "occurred_at": e.occurred_at.isoformat() if e.occurred_at else None,
            "actor_username": e.actor_username,
            "action": e.action,
            "target_org_id": e.target_org_id,
            "target_type": e.target_type,
            "target_id": e.target_id,
        }
        for e in recent
    ]

    return {
        "range": range_key,
        "generated_at": now.isoformat(),
        "cached": False,
        "tenants_active": int(status_counts.get("active", 0)),
        "tenants_pending": int(status_counts.get("pending_approval", 0)),
        "tenants_suspended": int(status_counts.get("suspended", 0)),
        "signups_by_day": [p.model_dump() for p in signups],
        "screens_total": int(total_screens),
        "screens_online": int(online_screens),
        "screens_offline_24h": int(offline_24h),
        "on_latest_version_pct": on_latest_pct,
        "alerts_critical_open": int(alert_counts.get("critical", 0)),
        "alerts_warning_open": int(alert_counts.get("warning", 0)),
        "plays_online_by_day": [p.model_dump() for p in plays_online],
        "plays_offline_by_day": [p.model_dump() for p in plays_offline],
        "revenue_paise_by_day": [p.model_dump() for p in revenue_by_day],
        "revenue_paise_total": int(revenue_total),
        "renewals_due_14d_count": len(renewals),
        "renewals_due_14d_value_paise": int(renewals_value),
        "storage_used_bytes": int(storage_total),
        "pending_approvals": int(pending_approvals),
        "pending_custom_requests": int(pending_custom),
        "recent_activity": recent_activity,
    }


@router.get("/overview", response_model=OverviewOut)
def get_overview(
    range: Literal["7d", "30d", "90d"] = "30d",
    scope: TenantScope = Depends(require_super_admin),
):
    cached = _cache.get(range)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return OverviewOut(**{**cached[1], "cached": True})

    payload = _build_overview(scope.db, range)
    _cache[range] = (time.time(), payload)
    return OverviewOut(**payload)


def forget_overview_cache() -> None:
    """For tests: a fresh db fixture must not see a previous test's cached numbers."""
    _cache.clear()


class AuditEventOut(BaseModel):
    id: int
    occurred_at: Optional[str] = None
    actor_user_id: Optional[int] = None
    actor_username: Optional[str] = None
    acting_org_id: Optional[int] = None
    target_org_id: Optional[int] = None
    target_type: Optional[str] = None
    target_id: Optional[str] = None
    action: str
    reason: Optional[str] = None
    before_json: Optional[str] = None
    after_json: Optional[str] = None
    ip: Optional[str] = None
    batch_id: Optional[str] = None


AUDIT_TOTAL_COUNT_HEADER = "X-Total-Count"


@router.get("/audit", response_model=List[AuditEventOut])
def list_audit_events(
    response: Response,
    org_id: Optional[int] = None,
    action: Optional[str] = None,
    actor_user_id: Optional[int] = None,
    since: Optional[datetime] = None,
    until: Optional[datetime] = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    scope: TenantScope = Depends(require_super_admin),
):
    """The shared audit list -- Tenant 360's Activity tab passes org_id; the standalone
    Audit Log page passes none. One endpoint, not two, so the two views can never disagree
    about what an action is called or how it's formatted."""
    event = models.AdminAuditEvent
    query = scope.db.query(event)
    if org_id is not None:
        query = query.filter(event.target_org_id == org_id)
    if action:
        if "," in action:
            actions = [a.strip() for a in action.split(",") if a.strip()]
            query = query.filter(event.action.in_(actions))
        elif "%" in action:
            query = query.filter(event.action.like(action))
        else:
            query = query.filter(event.action == action)
    if actor_user_id is not None:
        query = query.filter(event.actor_user_id == actor_user_id)
    if since is not None:
        query = query.filter(event.occurred_at >= since)
    if until is not None:
        query = query.filter(event.occurred_at <= until)

    response.headers[AUDIT_TOTAL_COUNT_HEADER] = str(query.count())
    rows = query.order_by(event.id.desc()).offset(offset).limit(limit).all()
    return [
        AuditEventOut(
            id=e.id, occurred_at=e.occurred_at.isoformat() if e.occurred_at else None,
            actor_user_id=e.actor_user_id, actor_username=e.actor_username,
            acting_org_id=e.acting_org_id, target_org_id=e.target_org_id,
            target_type=e.target_type, target_id=e.target_id, action=e.action,
            reason=e.reason, before_json=e.before_json, after_json=e.after_json,
            ip=e.ip, batch_id=e.batch_id,
        )
        for e in rows
    ]


class SearchHit(BaseModel):
    type: str
    id: int | str
    label: str
    sublabel: Optional[str] = None
    href: str


class SearchOut(BaseModel):
    query: str
    results: List[SearchHit]


def _like(q: str) -> str:
    """A substring ILIKE pattern with the caller's own % and _ taken literally."""
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("/search", response_model=SearchOut)
def search(q: str = "", scope: TenantScope = Depends(require_super_admin)):
    """Top 5 of each kind. Several small indexed queries, not one cross-table UNION ALL --
    the types below have different column counts and types (int ids vs string order ids),
    and SQLAlchemy Core can express a type-safe UNION ALL across them, but hand-rolling one
    that behaves identically on SQLite (tests) and Postgres (everywhere else) was worse odds
    under time pressure than five obviously-correct, independently cheap queries."""
    db = scope.db
    query = (q or "").strip()
    if len(query) < 2:
        return SearchOut(query=query, results=[])
    pattern = _like(query)
    hits: List[SearchHit] = []

    for org in (
        db.query(models.Organization)
        .filter(models.Organization.deleted_at.is_(None))
        .filter(models.Organization.name.ilike(pattern, escape="\\") | models.Organization.slug.ilike(pattern, escape="\\"))
        .order_by(models.Organization.name).limit(5).all()
    ):
        hits.append(SearchHit(type="tenant", id=org.id, label=org.name, sublabel=org.slug,
                              href=f"/admin/tenants/{org.id}"))

    for user in (
        db.query(models.User)
        .filter(models.User.email.ilike(pattern, escape="\\") | models.User.username.ilike(pattern, escape="\\"))
        .order_by(models.User.username).limit(5).all()
    ):
        hits.append(SearchHit(type="user", id=user.id, label=user.email or user.username,
                              sublabel=f"org {user.organization_id}" if user.organization_id else None,
                              href=f"/admin/tenants/{user.organization_id}" if user.organization_id else "/admin/tenants"))

    for screen in (
        db.query(models.Screen)
        .filter(models.Screen.deleted_at.is_(None))
        .filter(models.Screen.name.ilike(pattern, escape="\\") | models.Screen.device_id.ilike(pattern, escape="\\"))
        .order_by(models.Screen.name).limit(5).all()
    ):
        hits.append(SearchHit(type="screen", id=screen.id, label=screen.name or screen.device_id,
                              sublabel=screen.device_id,
                              href=f"/admin/tenants/{screen.organization_id}" if screen.organization_id else "/admin/fleet"))

    for payment in (
        db.query(models.PlatformPayment)
        .filter(models.PlatformPayment.provider_order_id.ilike(pattern, escape="\\")
                | models.PlatformPayment.provider_payment_id.ilike(pattern, escape="\\"))
        .order_by(models.PlatformPayment.paid_at.desc()).limit(5).all()
    ):
        hits.append(SearchHit(type="payment", id=payment.id,
                              label=payment.provider_order_id or payment.provider_payment_id or f"payment {payment.id}",
                              sublabel=f"{payment.amount_paise / 100:.2f} {payment.currency}",
                              href=f"/admin/billing?payment={payment.id}"))

    for release in (
        db.query(models.AppRelease)
        .filter(models.AppRelease.version_name.ilike(pattern, escape="\\"))
        .order_by(models.AppRelease.version_code.desc()).limit(5).all()
    ):
        hits.append(SearchHit(type="release", id=release.version_code, label=release.version_name,
                              sublabel=release.rollout_state, href="/admin/releases"))

    return SearchOut(query=query, results=hits)


@router.get("/inbox-counts", response_model=InboxCountsOut)
def get_inbox_counts(scope: TenantScope = Depends(require_super_admin)):
    db = scope.db
    org = models.Organization
    pending_approvals = db.query(func.count()).select_from(org).filter(org.status == "pending_approval").scalar() or 0
    new_custom_requests = (
        db.query(func.count()).select_from(models.CustomPlanRequest)
        .filter(models.CustomPlanRequest.status == "requested").scalar() or 0
    )
    critical_alerts = (
        db.query(func.count()).select_from(models.Alert)
        .filter(models.Alert.resolved_at.is_(None), models.Alert.severity == "critical").scalar() or 0
    )
    since = models.utcnow() - timedelta(days=7)
    failed_payments = (
        db.query(func.count()).select_from(models.PlatformPayment)
        .filter(models.PlatformPayment.status == "failed", models.PlatformPayment.paid_at >= since).scalar() or 0
    )
    return InboxCountsOut(
        pending_approvals=int(pending_approvals),
        new_custom_requests=int(new_custom_requests),
        critical_alerts=int(critical_alerts),
        failed_payments_7d=int(failed_payments),
    )
