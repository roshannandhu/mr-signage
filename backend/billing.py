import json
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import models


# The five flags Phase 3.1 added: payslips & payments, ads counting, remote control & alerts,
# PDF playback reports, and TV kiosk lock. Listed once so a typo in a plan's dict is a
# NameError at import time rather than a silently-never-gated feature.
PAYSLIPS = "payslips"
ADS_COUNTING = "ads_counting"
REMOTE_CONTROL = "remote_control"
PDF_REPORTS = "pdf_reports"
KIOSK_LOCK = "kiosk_lock"

DEFAULT_PLANS = (
    {
        "name": "Free",
        "slug": "free",
        "monthly_price_paise": 0,
        "yearly_price_paise": 0,
        "duration_days": 30,
        "max_screens": 5,
        "max_clients": 3,
        "max_ad_slots": 5,
        "max_storage_bytes": 10 * 1024 * 1024 * 1024,
        "features": {"scheduling": True, ADS_COUNTING: True},
    },
    {
        "name": "Starter",
        "slug": "starter",
        "monthly_price_paise": 99_900,
        "yearly_price_paise": 999_000,
        "duration_days": 30,
        "max_screens": 10,
        "max_clients": 10,
        "max_ad_slots": 20,
        "max_storage_bytes": 25 * 1024 * 1024 * 1024,
        "features": {
            "scheduling": True, "transitions": True, ADS_COUNTING: True,
            PDF_REPORTS: True, PAYSLIPS: True, REMOTE_CONTROL: True, KIOSK_LOCK: True,
        },
    },
    {
        "name": "Business",
        "slug": "business",
        "monthly_price_paise": 299_900,
        "yearly_price_paise": 2_999_000,
        "duration_days": 30,
        "max_screens": 50,
        "max_clients": 50,
        "max_ad_slots": 0,  # 0 = unlimited, Plan.max_ad_slots's own convention (see the column)
        "max_storage_bytes": 100 * 1024 * 1024 * 1024,
        "features": {
            "scheduling": True, "transitions": True, "priority_support": True, "emergency_alert": True,
            ADS_COUNTING: True, PDF_REPORTS: True, PAYSLIPS: True, REMOTE_CONTROL: True, KIOSK_LOCK: True,
        },
    },
)

# Flags DEFAULT_PLANS has carried since before per-feature gating grew past these four. Only
# these ever backfill onto an EXISTING plan row (see ensure_billing_catalog below) -- that
# repairs real drift between this table and the alembic seed (the comment there explains the
# Business/emergency_alert case this was built for). A flag added to DEFAULT_PLANS after this
# line is deliberately NOT backfillable: it only seeds a freshly created plan. Existing plans
# keep it off until an operator turns it on in the packages editor, per the approved rule that
# a new feature never turns itself on under a tenant that is already paying for a plan without it.
_BACKFILLABLE_FEATURE_KEYS = {"scheduling", "transitions", "priority_support", "emergency_alert"}


def ensure_billing_catalog(db: Session) -> None:
    by_slug = {plan.slug: plan for plan in db.query(models.Plan).all()}
    for payload in DEFAULT_PLANS:
        existing = by_slug.get(payload["slug"])
        if existing is not None:
            # Backfill only flags this plan has never carried an opinion about.
            #
            # The alembic migration seeds the same three packages, and its Business row omits
            # emergency_alert. Because this loop used to `continue` on any existing slug, a
            # database brought up through migrations had a Business plan that could never use
            # the one feature that is actually enforced, and nothing here would ever repair
            # it -- the seeds only agree on a fresh create_all install.
            #
            # A key that is present stays untouched, true or false: an operator turning a
            # feature off in the packages editor is a decision, and re-asserting the seed
            # over it every boot would quietly undo them.
            current = plan_features(existing)
            missing = {
                k: v for k, v in payload["features"].items()
                if k not in current and k in _BACKFILLABLE_FEATURE_KEYS
            }
            if missing:
                existing.feature_flags_json = json.dumps({**current, **missing}, sort_keys=True)
            continue
        db.add(
            models.Plan(
                name=payload["name"],
                slug=payload["slug"],
                monthly_price_paise=payload["monthly_price_paise"],
                yearly_price_paise=payload["yearly_price_paise"],
                # The storefront charges this once for `duration_days` of access; seed it
                # from the monthly figure so a fresh install is immediately sellable.
                price_paise=payload["monthly_price_paise"],
                duration_days=payload["duration_days"],
                max_screens=payload["max_screens"],
                max_clients=payload["max_clients"],
                max_ad_slots=payload["max_ad_slots"],
                max_storage_bytes=payload["max_storage_bytes"],
                feature_flags_json=json.dumps(payload["features"], sort_keys=True),
                is_active=True,
            )
        )
    db.flush()

    free_plan = db.query(models.Plan).filter(models.Plan.slug == "free").one()
    for organization in db.query(models.Organization).all():
        if organization.plan_id is None:
            organization.plan_id = free_plan.id
            organization.storage_quota_bytes = free_plan.max_storage_bytes
        if organization.subscription is None:
            db.add(
                models.Subscription(
                    organization_id=organization.id,
                    plan_id=organization.plan_id,
                    status="active",
                    billing_period="monthly",
                )
            )
    db.commit()


def mock_payments_enabled() -> bool:
    """Whether the free-activation test shortcut is live.

    `POST /api/billing/mock/confirm` activates a workspace with no payment. That is correct
    for a test run and catastrophic anywhere real, and the only thing standing between the
    two is an environment variable nobody looks at -- so a hosted deployment refuses it
    outright regardless, and `/api/health` names it while it is on, the same treatment
    legacy device auth gets.

    RENDER_EXTERNAL_URL is set by the host, not by us, so it cannot be forgotten the way a
    deliberate PRODUCTION flag can.
    """
    if os.getenv("PAYMENT_PROVIDER", "razorpay").lower() != "mock":
        return False
    return not os.getenv("RENDER_EXTERNAL_URL", "").strip()


def plan_features(plan: models.Plan) -> dict[str, bool]:
    try:
        value = json.loads(plan.feature_flags_json or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


# --------------------------------------------------------------------------------------
# Whether a plan is still in force.
#
# Next to plan_features deliberately: that answers "what does this plan grant", this answers
# "is it still granting it", and the two are read together everywhere. They also have to live
# below the service layer -- `tenancy` consults them from inside a permission check, and the
# repositories import tenancy, so putting them in a service makes the data floor import
# upwards and fails the architecture contract in `.importlinter`.
#
# `Subscription.current_period_end` was written at purchase and compared to the clock by
# nothing, so a workspace that bought thirty days kept full access indefinitely. One
# definition of expiry lives here so the dashboard, the televisions and the purge cannot
# drift into disagreeing about who has paid.
# --------------------------------------------------------------------------------------

# Settled by the billing provider; not waiting on the clock.
TERMINAL_SUBSCRIPTION_STATUSES = {"read_only", "cancelled", "completed", "expired"}


def _aware(value: datetime | None) -> datetime | None:
    """A stored instant as UTC-aware, so it compares with `models.utcnow()`.

    The Razorpay webhook writes `datetime.utcfromtimestamp(...)` (`routers/billing.py`),
    which is naive. Comparing that to an aware now raises TypeError -- and this runs inside
    the permission check on every write, so it would surface as a 500 on an unrelated
    request rather than as anything to do with billing.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def apply_plan(org, plan) -> None:
    """Put a workspace on a package: the plan, and ONLY the plan's limits.

    Clears the per-workspace screen and ad-slot overrides rather than copying the package's
    numbers into them. `Organization.max_screens` / `max_ad_slots` mean "this workspace differs
    from its package", and `effective_max_*` resolves override -> package -> unlimited, so a
    copy made every tenant permanently different from a package whose numbers merely happened
    to match that day. It also means buying a package replaces limits an operator set by hand,
    which is what buying a package is: the hand-set numbers described the OLD arrangement.

    storage_quota_bytes is a real column with no derived equivalent, so it is copied.
    """
    org.plan_id = plan.id
    org.max_screens = 0
    org.max_ad_slots = 0
    org.storage_quota_bytes = plan.max_storage_bytes


def paid_window(org, subscription, plan, now: datetime) -> tuple[datetime, datetime]:
    """The (start, end) of access a purchase of `plan` grants.

    Renewing the package a workspace is already on, while it still has days left, adds the new
    period AFTER the old one, so paying early loses no paid day. Switching to a different
    package, or buying after the window closed, starts today: there is no credit for the days
    left on a different package, because that would mean inventing a price for them.

    Called before the workspace is moved onto the new plan, because "the package it is already
    on" is read from `org.plan_id` -- purchase stamps the subscription with the new plan first.
    """
    duration = timedelta(days=plan.duration_days)
    current_end = _aware(subscription.current_period_end) if subscription else None
    still_running = (
        org.plan_id == plan.id
        and current_end is not None
        and current_end > now
        and (subscription is None or subscription.status not in TERMINAL_SUBSCRIPTION_STATUSES)
    )
    if still_running:
        return (_aware(subscription.current_period_start) or now), current_end + duration
    return now, now + duration


def subscription_state(subscription) -> str:
    """`active`, `grace` or `expired` for one subscription row.

    Pure: reads the row and the clock, writes nothing, touches no session. That is what lets
    the same answer be reached from a request, from a background sweep and from a test
    without any of them needing a database.

    No subscription at all is `active` on purpose. A workspace that has never bought
    anything is governed by its organisation status and its plan caps, and treating the
    absence of a row as an expiry would lock out every account created before billing
    existed.
    """
    if subscription is None:
        return "active"
    if subscription.status in TERMINAL_SUBSCRIPTION_STATUSES:
        return "expired"

    now = models.utcnow()
    period_end = _aware(subscription.current_period_end)
    grace_end = _aware(subscription.grace_period_end)

    # Grace outlives the period it follows, so it is checked first: a provider that moved a
    # subscription into grace has already decided the period is over.
    if grace_end is not None and subscription.status == "grace":
        return "grace" if grace_end > now else "expired"

    if period_end is None:
        # A recurring subscription the provider manages, with no window of our own to
        # enforce. Its status is the authority and it is not terminal, so it is live.
        return "active"
    if period_end > now:
        return "active"
    # The window closed. A grace end still in the future keeps them writing a little longer.
    if grace_end is not None and grace_end > now:
        return "grace"
    return "expired"


def serving_block_reason(organization, subscription) -> str | None:
    """Why this organisation's screens must not play its adverts, or None if they may.

    Covers both halves of being cut off: an organisation status an operator set by hand, and
    a plan that simply ran out. Both stop advertising, and neither deletes anything.

    A screen with no organisation returns None rather than a reason -- it has not been
    claimed yet, and answering with a block would hide the pairing state the player needs to
    show its code, leaving a brand new television blank and unpairable.
    """
    if organization is None:
        return None
    if organization.status in {"pending_approval", "suspended", "rejected"}:
        return organization.status
    if subscription_state(subscription) == "expired":
        return "expired"
    return None
