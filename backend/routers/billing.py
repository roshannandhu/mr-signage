import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import func

from .. import models, schemas
from ..billing import apply_plan, paid_window, plan_features
from ..payments import get_payment_provider
from ..tenancy import TenantScope, get_billing_scope, get_tenant_scope


router = APIRouter()


def serialize_plan(plan: models.Plan) -> schemas.PlanResponse:
    return schemas.PlanResponse(
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
        feature_flags=plan_features(plan),
    )


def serialize_subscription(subscription: models.Subscription) -> schemas.SubscriptionResponse:
    return schemas.SubscriptionResponse.model_validate(subscription, from_attributes=True)


@router.get("/plans", response_model=list[schemas.PlanResponse])
def list_plans(scope: TenantScope = Depends(get_billing_scope)):
    # get_billing_scope, not get_tenant_scope: a pending_approval workspace has to see the
    # packages to buy one. The list is the global catalogue, not tenant data, so this leaks
    # nothing.
    return [
        serialize_plan(plan)
        for plan in scope.db.query(models.Plan)
        .filter(models.Plan.is_active.is_(True))
        .order_by(models.Plan.price_paise)
        .all()
    ]


@router.get("/summary", response_model=schemas.BillingSummaryResponse)
def billing_summary(scope: TenantScope = Depends(get_tenant_scope)):
    from ..billing import ensure_billing_catalog
    organization = scope.db.query(models.Organization).filter(
        models.Organization.id == scope.organization_id
    ).one()
    plan_id = organization.plan_id or 1
    plan = scope.db.query(models.Plan).filter(models.Plan.id == plan_id).first()
    if not plan:
        ensure_billing_catalog(scope.db)
        scope.db.commit()
        plan = scope.db.query(models.Plan).filter(models.Plan.id == plan_id).first() or scope.db.query(models.Plan).order_by(models.Plan.id.asc()).first()
    
    subscription = scope.db.query(models.Subscription).filter(
        models.Subscription.organization_id == scope.organization_id
    ).first()
    if not subscription and plan:
        subscription = models.Subscription(
            organization_id=scope.organization_id,
            plan_id=plan.id,
            status="active",
            billing_period="monthly",
        )
        scope.db.add(subscription)
        scope.db.commit()
        scope.db.refresh(subscription)
    # Renditions included, matching what the upload route refuses on. Without them this
    # page told a tenant they had room and the next upload returned 413.
    from ..services.storage_service import organization_storage_used

    storage_used = organization_storage_used(scope.db, scope.organization_id)
    screens_used = scope.query(models.Screen).filter(
        models.Screen.status != "waiting_pairing"
    ).count()
    return schemas.BillingSummaryResponse(
        plan=serialize_plan(plan),
        subscription=serialize_subscription(subscription),
        screens_used=screens_used,
        storage_used_bytes=storage_used,
        is_read_only=scope.is_read_only(),
    )


@router.post("/checkout", response_model=schemas.CheckoutResponse)
def create_checkout(payload: schemas.CheckoutRequest, scope: TenantScope = Depends(get_tenant_scope)):
    if scope.user.role != "owner":
        raise HTTPException(status_code=403, detail="Only an organization owner can change billing")
    plan = scope.db.query(models.Plan).filter(
        models.Plan.id == payload.plan_id,
        models.Plan.is_active.is_(True),
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    organization = scope.db.query(models.Organization).filter(
        models.Organization.id == scope.organization_id
    ).one()
    subscription = scope.db.query(models.Subscription).filter(
        models.Subscription.organization_id == scope.organization_id
    ).one()
    amount = plan.monthly_price_paise if payload.billing_period == "monthly" else plan.yearly_price_paise
    if amount == 0 and plan.price_paise > 0:
        # Priced for a one-time purchase only: it has no monthly or yearly figure, and the
        # free path below would hand it over for nothing.
        raise HTTPException(
            status_code=409,
            detail="This package is sold as a one-time purchase, not as a subscription",
        )
    if amount == 0:
        apply_plan(organization, plan)
        subscription.plan_id = plan.id
        subscription.status = "active"
        subscription.billing_period = payload.billing_period
        subscription.provider = None
        subscription.provider_subscription_id = None
        subscription.grace_period_end = None
        scope.db.commit()
        return schemas.CheckoutResponse(
            provider="internal",
            provider_subscription_id=f"free-{scope.organization_id}",
            checkout_url="/dashboard/billing?updated=1",
        )

    provider_name = os.getenv("PAYMENT_PROVIDER", "razorpay").lower()
    env_name = f"RAZORPAY_{plan.slug.upper().replace('-', '_')}_{payload.billing_period.upper()}_PLAN_ID"
    provider_plan_id = os.getenv(env_name)
    if provider_name == "mock":
        provider_plan_id = provider_plan_id or f"plan_mock_{plan.slug}_{payload.billing_period}"
    if not provider_plan_id:
        raise HTTPException(status_code=503, detail=f"Billing plan is not configured ({env_name})")
    try:
        checkout = get_payment_provider().create_subscription(
            provider_plan_id=provider_plan_id,
            billing_period=payload.billing_period,
            organization_id=scope.organization_id,
            local_plan_id=plan.id,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    subscription.plan_id = plan.id
    subscription.status = "pending"
    subscription.billing_period = payload.billing_period
    subscription.provider = provider_name
    subscription.provider_subscription_id = checkout.provider_subscription_id
    subscription.updated_at = models.utcnow()
    scope.db.commit()
    return schemas.CheckoutResponse(
        provider=provider_name,
        provider_subscription_id=checkout.provider_subscription_id,
        checkout_url=checkout.checkout_url,
    )


def _activate_plan(
    org: models.Organization, subscription: models.Subscription, plan: models.Plan
) -> None:
    """Grant access for one paid period.

    Reads the plan for caps rather than copying them onto org overrides, so editing the
    package later moves the tenant with it -- effective_max_screens / _clients / _ad_slots
    all fall through to the plan when no override is set. Flipping org.status to "active" is
    the pay-to-access step: get_tenant_scope blocks every route until this happens.
    """
    now = models.utcnow()
    # Worked out first: it reads the package the workspace is on today, which apply_plan is
    # about to replace.
    start, end = paid_window(org, subscription, plan, now)
    subscription.plan_id = plan.id
    subscription.status = "active"
    subscription.billing_period = "one_time"
    subscription.current_period_start = start
    subscription.current_period_end = end
    subscription.grace_period_end = None
    subscription.updated_at = now
    apply_plan(org, plan)
    # Paying is the way in from the approval queue. It is NOT a way back from a suspension or a
    # rejection an operator applied: this used to set the status unconditionally, so any
    # payment (or a replayed webhook) un-suspended a workspace.
    if org.status == "pending_approval":
        org.status = "active"
    if org.status == "active" and org.approved_at is None:
        org.approved_at = now


def activate_purchase(db, order_id: str) -> bool:
    """Apply a captured one-time order to whatever it paid for.

    Idempotent: a re-delivered webhook or a repeated confirm finds the subscription already
    active and leaves it. Returns True when the order matched a known purchase. Standard
    packages are matched by the subscription that raised the order; custom requests are
    matched separately (added with the custom-request routes).
    """
    subscription = db.query(models.Subscription).filter(
        models.Subscription.provider_subscription_id == order_id
    ).first()
    if subscription and subscription.plan and subscription.organization:
        if subscription.status != "active":
            _activate_plan(subscription.organization, subscription, subscription.plan)
        return True
    return activate_custom_request(db, order_id)


def activate_custom_request(db, order_id: str) -> bool:
    """Apply a captured order that paid for a bespoke request.

    A custom request has no Plan of its own, but the subscription's plan_id is NOT NULL and
    features live on a plan -- so payment mints a HIDDEN plan (is_active False, slug
    'custom-<id>') carrying the agreed caps, period and features, and points the org at it.
    That reuses effective_max_* and plan_features unchanged; the admin catalogue filters
    'custom-' slugs out so these never appear as sellable packages. Idempotent on status.
    """
    request = db.query(models.CustomPlanRequest).filter(
        models.CustomPlanRequest.provider_order_id == order_id
    ).first()
    if not request:
        return False
    org = db.query(models.Organization).filter(
        models.Organization.id == request.organization_id
    ).first()
    if not org:
        return False
    if request.status == "paid":
        return True

    slug = f"custom-{request.id}"
    plan = db.query(models.Plan).filter(models.Plan.slug == slug).first()
    if plan is None:
        plan = models.Plan(
            name=f"Custom plan #{request.id}",
            slug=slug,
            monthly_price_paise=0,
            yearly_price_paise=0,
            price_paise=request.price_paise,
            duration_days=request.duration_days,
            max_screens=request.max_screens,
            max_clients=request.max_clients,
            max_ad_slots=request.max_ad_slots,
            max_storage_bytes=request.max_storage_bytes,
            feature_flags_json=request.feature_flags_json,
            is_active=False,
        )
        db.add(plan)
        db.flush()

    subscription = db.query(models.Subscription).filter(
        models.Subscription.organization_id == org.id
    ).first()
    if subscription is None:
        subscription = models.Subscription(
            organization_id=org.id, plan_id=plan.id, status="pending", billing_period="one_time"
        )
        db.add(subscription)
        db.flush()

    _activate_plan(org, subscription, plan)
    request.status = "paid"
    request.updated_at = models.utcnow()
    return True


@router.post("/purchase", response_model=schemas.PurchaseResponse)
def purchase_plan(
    payload: schemas.PurchaseRequest,
    scope: TenantScope = Depends(get_billing_scope),
):
    """Buy a standard package as a one-time charge for its access period.

    get_billing_scope, so a pending_approval workspace can pay its way in. A free package is
    activated on the spot; a paid one raises a provider order and is activated by the webhook
    (real) or /billing/mock/confirm (mock).
    """
    if scope.user.role != "owner":
        raise HTTPException(status_code=403, detail="Only an organization owner can buy a plan")
    plan = scope.db.query(models.Plan).filter(
        models.Plan.id == payload.plan_id,
        models.Plan.is_active.is_(True),
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    org = scope.db.query(models.Organization).filter(
        models.Organization.id == scope.organization_id
    ).one()
    subscription = scope.db.query(models.Subscription).filter(
        models.Subscription.organization_id == org.id
    ).first()
    if subscription is None:
        # A self-serve signup has no subscription row yet -- create one bound to the plan it
        # is buying (plan_id is NOT NULL).
        subscription = models.Subscription(
            organization_id=org.id, plan_id=plan.id, status="pending", billing_period="one_time"
        )
        scope.db.add(subscription)
        scope.db.flush()

    if plan.price_paise <= 0:
        _activate_plan(org, subscription, plan)
        scope.db.commit()
        return schemas.PurchaseResponse(provider="internal", checkout_url="/dashboard/screens")

    try:
        order = get_payment_provider().create_order(
            amount_paise=plan.price_paise,
            organization_id=org.id,
            notes={"plan_id": plan.id, "kind": "plan"},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    subscription.plan_id = plan.id
    subscription.status = "pending"
    subscription.billing_period = "one_time"
    subscription.provider = order.provider
    # The order id is the correlation key the webhook / confirm looks the purchase up by.
    subscription.provider_subscription_id = order.order_id
    subscription.updated_at = models.utcnow()
    scope.db.commit()
    return schemas.PurchaseResponse(
        provider=order.provider,
        order_id=order.order_id,
        amount_paise=order.amount_paise,
        key_id=order.key_id,
        checkout_url=order.checkout_url,
    )


@router.post("/mock/confirm", response_model=schemas.PurchaseResponse)
def mock_confirm(
    payload: schemas.MockConfirmRequest,
    scope: TenantScope = Depends(get_billing_scope),
):
    """Finish a mock purchase, standing in for the provider webhook when PAYMENT_PROVIDER=mock.

    Refuses outright under a real provider AND on any hosted deployment, so setting
    PAYMENT_PROVIDER=mock in production by accident cannot turn payment off; only activates
    an order raised by the caller's own workspace.
    """
    from ..billing import mock_payments_enabled

    if not mock_payments_enabled():
        raise HTTPException(status_code=404, detail="Not found")

    owned = scope.db.query(models.Subscription).filter(
        models.Subscription.provider_subscription_id == payload.order_id,
        models.Subscription.organization_id == scope.organization_id,
    ).first()
    owned_custom = scope.db.query(models.CustomPlanRequest).filter(
        models.CustomPlanRequest.provider_order_id == payload.order_id,
        models.CustomPlanRequest.organization_id == scope.organization_id,
    ).first()
    if not owned and not owned_custom:
        raise HTTPException(status_code=404, detail="Unknown order for this workspace")

    if not activate_purchase(scope.db, payload.order_id):
        raise HTTPException(status_code=409, detail="Order could not be applied")
    scope.db.commit()
    return schemas.PurchaseResponse(provider="internal", checkout_url="/dashboard/screens")


def serialize_custom_request(
    request: models.CustomPlanRequest, include_org: bool = False
) -> schemas.CustomPlanRequestResponse:
    try:
        features = json.loads(request.feature_flags_json or "{}")
    except json.JSONDecodeError:
        features = {}
    return schemas.CustomPlanRequestResponse(
        id=request.id,
        organization_id=request.organization_id,
        max_screens=request.max_screens,
        max_clients=request.max_clients,
        max_ad_slots=request.max_ad_slots,
        max_storage_bytes=request.max_storage_bytes,
        duration_days=request.duration_days,
        feature_flags=features if isinstance(features, dict) else {},
        price_paise=request.price_paise,
        status=request.status,
        notes=request.notes,
        created_at=request.created_at,
        organization_name=(request.organization.name if include_org and request.organization else None),
    )


@router.post("/custom-request", response_model=schemas.CustomPlanRequestResponse, status_code=201)
def create_custom_request(
    payload: schemas.CustomPlanRequestCreate,
    scope: TenantScope = Depends(get_billing_scope),
):
    """Ask for a bespoke package. It queues for the operator to price; the tenant pays once
    priced. Does not grant anything on its own."""
    if scope.user.role != "owner":
        raise HTTPException(status_code=403, detail="Only an organization owner can request a custom plan")
    request = models.CustomPlanRequest(
        organization_id=scope.organization_id,
        max_screens=payload.max_screens,
        max_clients=payload.max_clients,
        max_ad_slots=payload.max_ad_slots,
        max_storage_bytes=payload.max_storage_bytes,
        duration_days=payload.duration_days,
        feature_flags_json=json.dumps(payload.feature_flags, sort_keys=True),
        notes=payload.notes,
        status="requested",
    )
    scope.db.add(request)
    scope.db.commit()
    scope.db.refresh(request)
    return serialize_custom_request(request)


@router.get("/custom-request", response_model=list[schemas.CustomPlanRequestResponse])
def list_my_custom_requests(scope: TenantScope = Depends(get_billing_scope)):
    requests = scope.db.query(models.CustomPlanRequest).filter(
        models.CustomPlanRequest.organization_id == scope.organization_id
    ).order_by(models.CustomPlanRequest.created_at.desc()).all()
    return [serialize_custom_request(request) for request in requests]


@router.post("/custom-request/{request_id}/purchase", response_model=schemas.PurchaseResponse)
def purchase_custom_request(request_id: int, scope: TenantScope = Depends(get_billing_scope)):
    """Pay a priced custom request. Same order flow as a standard package."""
    if scope.user.role != "owner":
        raise HTTPException(status_code=403, detail="Only an organization owner can buy a plan")
    request = scope.db.query(models.CustomPlanRequest).filter(
        models.CustomPlanRequest.id == request_id,
        models.CustomPlanRequest.organization_id == scope.organization_id,
    ).first()
    if not request:
        raise HTTPException(status_code=404, detail="Request not found")
    if request.status == "paid":
        raise HTTPException(status_code=409, detail="This request has already been paid")
    if request.status != "priced":
        raise HTTPException(status_code=409, detail="This request has not been priced yet")

    if request.price_paise <= 0:
        request.provider_order_id = f"internal-custom-{request.id}"
        scope.db.flush()
        activate_custom_request(scope.db, request.provider_order_id)
        scope.db.commit()
        return schemas.PurchaseResponse(provider="internal", checkout_url="/dashboard/screens")

    try:
        order = get_payment_provider().create_order(
            amount_paise=request.price_paise,
            organization_id=scope.organization_id,
            notes={"custom_request_id": request.id, "kind": "custom"},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    request.provider_order_id = order.order_id
    request.updated_at = models.utcnow()
    scope.db.commit()
    return schemas.PurchaseResponse(
        provider=order.provider,
        order_id=order.order_id,
        amount_paise=order.amount_paise,
        key_id=order.key_id,
        checkout_url=order.checkout_url,
    )


@router.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    x_razorpay_signature: str | None = Header(default=None),
    x_razorpay_event_id: str | None = Header(default=None),
):
    secret = os.getenv("RAZORPAY_WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=503, detail="RAZORPAY_WEBHOOK_SECRET is not configured")
    raw_body = await request.body()
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not x_razorpay_signature or not hmac.compare_digest(expected, x_razorpay_signature):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
    if not x_razorpay_event_id:
        raise HTTPException(status_code=400, detail="Missing X-Razorpay-Event-Id")

    # Webhooks are unauthenticated by design; use the application's database
    # factory after signature verification instead of a tenant dependency.
    from .. import database

    db = database.SessionLocal()
    try:
        if db.query(models.WebhookEvent).filter(
            models.WebhookEvent.provider_event_id == x_razorpay_event_id
        ).first():
            return {"status": "duplicate"}
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc
        event_type = payload.get("event", "")

        # One-time purchases (the storefront's pay-to-access path) arrive as order.paid, not
        # a subscription event. Attribute it by the order id and activate, then stop -- the
        # subscription-entity handling below is only for the older recurring plans.
        if event_type == "order.paid":
            order_entity = payload.get("payload", {}).get("order", {}).get("entity", {})
            order_id = order_entity.get("id")
            db.add(
                models.WebhookEvent(
                    provider="razorpay",
                    provider_event_id=x_razorpay_event_id,
                    event_type=event_type,
                )
            )
            if order_id:
                activate_purchase(db, order_id)
            db.commit()
            return {"status": "ok"}

        entity = payload.get("payload", {}).get("subscription", {}).get("entity", {})
        provider_subscription_id = entity.get("id")
        subscription = db.query(models.Subscription).filter(
            models.Subscription.provider_subscription_id == provider_subscription_id
        ).first()
        db.add(
            models.WebhookEvent(
                provider="razorpay",
                provider_event_id=x_razorpay_event_id,
                event_type=event_type or "unknown",
            )
        )
        if subscription:
            active_events = {
                "subscription.authenticated",
                "subscription.activated",
                "subscription.charged",
                "subscription.resumed",
            }
            failure_events = {"subscription.pending", "subscription.halted"}
            read_only_events = {
                "subscription.cancelled",
                "subscription.completed",
                "subscription.paused",
            }
            if event_type in active_events:
                subscription.status = "active"
                subscription.grace_period_end = None
                subscription.current_period_start = timestamp_to_datetime(entity.get("current_start"))
                subscription.current_period_end = timestamp_to_datetime(entity.get("current_end"))
                apply_plan(subscription.organization, subscription.plan)
            elif event_type in failure_events:
                grace_days = max(1, min(int(os.getenv("BILLING_GRACE_DAYS", "7")), 30))
                subscription.status = "grace"
                subscription.grace_period_end = models.utcnow() + timedelta(days=grace_days)
            elif event_type in read_only_events:
                subscription.status = "read_only"
                subscription.grace_period_end = None
            subscription.updated_at = models.utcnow()
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


def timestamp_to_datetime(value) -> datetime | None:
    try:
        return datetime.utcfromtimestamp(int(value)) if value is not None else None
    except (TypeError, ValueError, OSError):
        return None
