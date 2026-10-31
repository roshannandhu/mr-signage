"""The packages a tenant sells to its own clients.

Deliberately separate from `models.Plan`, which is what MR Signage bills the TENANT. One table
for both would let a tenant edit the plan they are billed on, and would put MR Signage's pricing
in front of their customers.

A booking COPIES a plan's price and duration at the moment it is created (see
routers/placements.create_placement). Nothing downstream reads through to this table for
money, so repricing a plan never rebills a campaign that was already sold.
"""
from fastapi import APIRouter, Depends, HTTPException

from .. import models, schemas
from ..tenancy import TenantScope, get_tenant_scope, require_tenant_roles

router = APIRouter()


@router.get("/", response_model=list[schemas.TenantPlanResponse])
def list_tenant_plans(
    include_inactive: bool = False,
    scope: TenantScope = Depends(get_tenant_scope),
):
    query = scope.query(models.TenantPlan)
    if not include_inactive:
        query = query.filter(models.TenantPlan.is_active.is_(True))
    return query.order_by(models.TenantPlan.price_paise).all()


@router.post("/", response_model=schemas.TenantPlanResponse, status_code=201)
def create_tenant_plan(
    payload: schemas.TenantPlanCreate,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    plan = models.TenantPlan(
        organization_id=scope.organization_id,
        **payload.model_dump(),
    )
    scope.db.add(plan)
    scope.db.commit()
    scope.db.refresh(plan)
    return plan


@router.get("/{plan_id}", response_model=schemas.TenantPlanResponse)
def get_tenant_plan(plan_id: int, scope: TenantScope = Depends(get_tenant_scope)):
    plan = scope.get(models.TenantPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


@router.put("/{plan_id}", response_model=schemas.TenantPlanResponse)
def update_tenant_plan(
    plan_id: int,
    payload: schemas.TenantPlanUpdate,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    plan = scope.get(models.TenantPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(plan, field, value)
    scope.db.commit()
    scope.db.refresh(plan)
    return plan


@router.delete("/{plan_id}")
def delete_tenant_plan(
    plan_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    """Retire a plan, or delete it outright if nothing was ever sold on it.

    A plan that has bookings is deactivated rather than removed. The foreign key would
    survive the delete -- it is ON DELETE SET NULL -- but the report would then lose the
    plan name it prints, and a client asking "what did I buy?" is entitled to an answer
    after the tenant stops offering it.
    """
    plan = scope.get(models.TenantPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    sold = scope.db.query(models.AdPlacement).filter(
        models.AdPlacement.plan_id == plan.id
    ).count()
    if sold:
        plan.is_active = False
        scope.db.commit()
        return {"status": "retired", "bookings": sold}

    scope.db.delete(plan)
    scope.db.commit()
    return {"status": "deleted"}


def plan_capacity_screen_days(plan) -> int:
    """What a plan actually sells, expressed in screen-days: locations x days.

    The unit the whole quote rests on. A plan of Rs.25,000 for 30 days across 5 locations
    is selling 150 screen-days, and every custom shape a client asks for -- "50 days at the
    airport, 30 in the mall, 10 in the shop" -- is some other number of that same unit.
    Without a shared unit the two are not comparable at all, which is why a custom request
    previously had to be priced by hand.
    """
    if not plan:
        return 0
    return max(0, plan.duration_days) * max(0, plan.max_locations)


def quote_paise(plan, screen_days: int) -> int:
    """What a custom shape costs on a plan. Never below the plan's own price.

    Pro-rata on screen-days, so a client asking for a different SHAPE is charged the rate
    per screen-day their package already implies. The rate comes from the plan itself
    rather than a single global figure, so a larger package being cheaper per screen-day
    is preserved -- the volume discount the tenant built into their tiers survives instead
    of being flattened into one linear price list.

    Floored at the plan price, so this only ever prices the EXCESS. Quoting below it would
    contradict the model the rest of the code already commits to: plan_screen_usage treats
    unused capacity as the client's to waste and the plan_underused alert tells the tenant
    to go and fill it. If underuse were refundable, a client could buy a five-location plan,
    take one location and pay a fifth of it.

    Advisory only. Nothing here writes a price -- a sold booking keeps the figure it was
    sold at (routers/placements.create_placement copies it once) -- this is the number to
    put in front of an operator BEFORE they agree the next one.
    """
    if not plan:
        return 0
    capacity = plan_capacity_screen_days(plan)
    if capacity <= 0:
        return plan.price_paise
    # Integer arithmetic, rounding half up. `round()` on a float is banker's rounding and
    # would land a paise either side depending on parity, which is not how a price list is
    # read back to a client -- and the float division loses exactness on large paise
    # figures long before the integers would.
    pro_rata = (plan.price_paise * screen_days + capacity // 2) // capacity
    return max(plan.price_paise, pro_rata)


@router.post("/{plan_id}/quote", response_model=schemas.PlanQuoteResponse)
def quote_plan(
    plan_id: int,
    payload: schemas.PlanQuoteRequest,
    scope: TenantScope = Depends(get_tenant_scope),
):
    """Price a custom run before selling it: N locations, each for its own number of days.

    Answers the question the per-location windows feature created and left open. A booking
    can already run 30 days in a mall and 50 at an airport, and ensure_plan_locations caps
    how many LOCATIONS a plan covers -- but nothing capped or priced the DAYS, so those
    same five locations could each be sold a year and still be billed the 30-day plan
    price. This makes the gap visible and puts a number on it.

    Deliberately does not enforce. The operator is the one negotiating, prices are copied
    onto a booking exactly once, and refusing the sale would break the legitimate case of a
    discount agreed with the client. It returns the arithmetic; the human decides.
    """
    plan = scope.get(models.TenantPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    # A location with no length of its own runs for the plan's duration, which is exactly
    # what a booking does when a target carries no window (AdPlacementTarget.starts_at /
    # ends_at NULL). Quoting it as zero would price the ordinary case at nothing.
    days = [d or plan.duration_days for d in payload.days]
    screen_days = sum(days)
    capacity = plan_capacity_screen_days(plan)
    price = quote_paise(plan, screen_days)

    return schemas.PlanQuoteResponse(
        plan_id=plan.id,
        plan_name=plan.name,
        locations=len(days),
        screen_days=screen_days,
        capacity_screen_days=capacity,
        plan_price_paise=plan.price_paise,
        quoted_price_paise=price,
        extra_price_paise=price - plan.price_paise,
        # Locations is the hard cap -- ensure_plan_locations refuses a booking that breaches
        # it -- so the UI can grey out the sale rather than quoting a price it cannot book.
        exceeds_locations=plan.max_locations > 0 and len(days) > plan.max_locations,
    )
