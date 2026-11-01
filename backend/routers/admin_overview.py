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
from datetime import timedelta
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends
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
