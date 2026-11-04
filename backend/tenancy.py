import logging
from dataclasses import dataclass
from typing import TypeVar

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Query, Session

from . import database, models

logger = logging.getLogger(__name__)

TenantModel = TypeVar("TenantModel")


def is_super_admin(user: models.User) -> bool:
    """The single source of truth for platform-operator status.

    This used to be `role == "super_admin" or email in SEED_SUPER_ADMINS`, with the seed
    set copy-pasted into four files that had already drifted apart: the frontend's copy
    omitted the operator's own address (locking them out of /admin), and auth.py's copy
    omitted it again (so signing up with it created a pending_approval workspace). Worse,
    the set was hardcoded, so revoking platform access meant a redeploy.

    Role is now the only signal. The accompanying migration promotes the four legacy
    addresses to role='super_admin' once, so nothing is lost.
    """
    return user.role == "super_admin"


# Statuses that suspend a tenant's access to their own workspace. "pending_approval" is a
# tenant that has not been let in yet; the other two are one that has been put out.
#
# Only pending_approval used to be checked here, so `suspended` and `rejected` passed
# straight through: the Super Admin's Suspend button changed a label in a table and
# nothing else. The org kept full read and write access to every endpoint.
BLOCKED_ORGANIZATION_STATUSES = {
    "pending_approval": (
        "Workspace is pending manager approval. Access is restricted until approved."
    ),
    "suspended": (
        "This workspace has been suspended. Contact your platform administrator."
    ),
    "rejected": (
        "This workspace registration was not approved. Contact your platform administrator."
    ),
    # Not a real `status` value -- User.organization_status synthesises it from deleted_at,
    # so that removal travels the same path as every other reason to refuse a workspace.
    "removed": (
        "This workspace has been removed. Contact your platform administrator."
    ),
}


@dataclass(frozen=True)
class TenantScope:
    """The sole entry point for authenticated organization-scoped queries."""

    db: Session
    user: models.User
    # One workspace a platform operator has deliberately stepped into, set only by
    # `get_tenant_scope` from the X-Act-As-Org header and only for a super_admin.
    #
    # This is what lets an operator actually run a tenant's workspace -- book an advert,
    # repair a playlist, re-upload a creative -- through the ordinary tenant endpoints
    # instead of a parallel set of admin-only clones that would drift from them.
    #
    # It also closes a real hazard. Without it a super_admin opening the dashboard got
    # `query()` with the organisation filter DROPPED (every tenant's rows at once) while
    # `organization_id` still returned their own org -- so what they saw was a soup of all
    # workspaces, and anything they created was filed under the operator's own.
    acting_organization_id: int | None = None
    # "View workspace (read-only)" from the admin Tenant 360 page, via X-Act-As-Mode: read.
    # Only meaningful alongside acting_organization_id -- a super_admin working in their OWN
    # organisation is never put in this mode, and resolve_acting_organization already leaves
    # this False for anyone who is not a super_admin, since the header it reads is ignored.
    acting_read_only: bool = False

    @property
    def organization_id(self) -> int:
        # The workspace being acted in wins, so a booking an operator creates inside a
        # tenant is written to THAT tenant rather than to the operator's own organisation.
        if self.acting_organization_id is not None:
            return self.acting_organization_id
        # A super_admin has an organisation of its own like any other account, and this
        # returns it. Cross-tenant reach is expressed in `query()` below, which drops the
        # organisation filter -- not here, where a None would flow into non-nullable
        # foreign keys on every create.
        if self.user.organization_id is None:
            raise HTTPException(status_code=403, detail="User is not assigned to an organization")
        return self.user.organization_id

    @property
    def organization(self) -> models.Organization:
        """The workspace being acted in, as a row.

        Storage paths are built from the tenant's NAME, not its id, so the code that files
        an upload needs the organisation itself and not just the number. Resolved through
        `organization_id` so an operator working inside a tenant files into that tenant's
        folder rather than their own -- reading `user.organization` directly would put
        every object an operator uploaded on a customer's behalf under the operator.
        """
        return self.db.get(models.Organization, self.organization_id)

    def query(self, model: type[TenantModel]) -> Query:
        organization_column = getattr(model, "organization_id", None)
        if organization_column is None:
            raise RuntimeError(f"{model.__name__} is not tenant scoped")

        query = self.db.query(model)
        if self.acting_organization_id is not None:
            # Narrower than the super_admin default, not wider: inside a workspace the
            # operator sees that workspace only, which is the whole point of being in it.
            query = query.filter(organization_column == self.acting_organization_id)
        elif not is_super_admin(self.user):
            query = query.filter(organization_column == self.organization_id)

        # Archived rows are excluded here rather than at each call site, for the same
        # reason the organisation filter is: there are forty-odd places that query a
        # Screen, and a rule enforced in forty places is a rule that will be missed in
        # one. This funnel is where "rows this user may see" is already decided.
        #
        # Deliberately applies to any model carrying `deleted_at`, so a second archivable
        # table inherits it instead of repeating the mistake. A caller that genuinely
        # needs archived rows -- reporting over a period that includes a removed screen --
        # goes through self.db directly and says so.
        archived_column = getattr(model, "deleted_at", None)
        if archived_column is not None:
            query = query.filter(archived_column.is_(None))
        return query

    def get(self, model: type[TenantModel], record_id: int) -> TenantModel | None:
        return self.query(model).filter(model.id == record_id).first()

    def require_write_access(self) -> None:
        """Raise if this request is a read-only look into a tenant's workspace.

        Separate from is_read_only(): that one is about the TENANT's own billing state, and
        is deliberately bypassed while acting so an operator can fix a lapsed workspace. This
        is the operator's OWN choice to only look, never change -- "Enter workspace" stays
        full write access; "View workspace (read-only)" calls this on every write route.
        """
        if self.acting_read_only:
            raise HTTPException(
                status_code=403,
                detail="This is a read-only view of the workspace. Use \"Enter workspace\" to make changes.",
            )

    def is_read_only(self) -> bool:
        # A platform operator inside a workspace is not subject to that workspace's billing
        # state. Going in to fix something is most needed precisely when a subscription has
        # lapsed and the tenant has been put into read-only, and an operator locked out by
        # the same rule as the customer could not do the job they went in for.
        if self.acting_organization_id is not None:
            return False
        # Read directly off the user rather than through self.organization_id, which
        # raises 403 for an account with no organisation -- from inside a permission
        # check, turning "you have no workspace" into an unexplained failure on every
        # write. No organisation means no subscription means nothing to restrict.
        organization_id = self.user.organization_id
        if organization_id is None:
            return False
        subscription = (
            self.db.query(models.Subscription)
            .filter(models.Subscription.organization_id == organization_id)
            .first()
        )
        if not subscription:
            return False
        # One definition of expiry, shared with the sweep that sets it and with the screens
        # that stop advertising because of it. It used to be spelled out here, which is how
        # the paid window itself came to be ignored: this branch knew about `grace` and the
        # provider's terminal statuses, and nothing anywhere compared `current_period_end`
        # to the clock.
        # One definition of expiry, shared with the sweep that records it and with the
        # screens that stop advertising because of it. It used to be spelled out here, which
        # is how the paid window itself came to be ignored: this branch knew about `grace`
        # and the provider's terminal statuses, and nothing anywhere compared
        # `current_period_end` to the clock.
        from .billing import subscription_state

        return subscription_state(subscription) == "expired"


ACT_AS_HEADER = "X-Act-As-Org"
ACT_AS_MODE_HEADER = "X-Act-As-Mode"


def resolve_acting_organization(
    user: models.User, header_value: str | None, db: Session
) -> int | None:
    """The workspace this request is being carried out inside, or None.

    Honoured ONLY for a super_admin. For anybody else the header is ignored outright
    rather than rejected: a tenant sending it is not owed an error message that tells them
    the mechanism exists, and failing closed to their own organisation is the safe answer.

    Every acknowledged use is logged. This is impersonation -- an operator writing into a
    customer's workspace -- and it has to be attributable after the fact.
    """
    if header_value is None or not header_value.strip():
        return None
    if not is_super_admin(user):
        logger.warning(
            "Ignoring %s from non-operator %s (role=%s)", ACT_AS_HEADER, user.username, user.role
        )
        return None
    try:
        organization_id = int(header_value)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{ACT_AS_HEADER} must be an organization id")

    organization = (
        db.query(models.Organization).filter(models.Organization.id == organization_id).first()
    )
    if organization is None:
        raise HTTPException(status_code=404, detail="Workspace not found")
    # An operator may not step into a workspace that is counting down to being purged.
    # Everything inside it is about to stop existing, and work done there would be lost
    # without a word.
    if organization.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Workspace has been removed")

    logger.info(
        "Operator %s acting inside workspace %s (%s)",
        user.username,
        organization.id,
        organization.name,
    )
    return organization.id


_READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def note_act_as_write(request: Request, user: models.User, acting: int | None) -> None:
    """Mark a write made from inside a tenant's workspace for the audit middleware.

    Only marked here; main.ActAsAuditMiddleware writes the row once the response is known
    to have succeeded, so a refused or failed request leaves no misleading entry.
    """
    # Admin routes write their own, richer audit rows; marking them here would double them.
    if acting is None or request.method in _READ_METHODS or request.url.path.startswith("/api/admin/"):
        return
    request.state.act_as_audit = {
        "actor_user_id": user.id,
        "actor_username": user.username,
        "acting_org_id": acting,
        "method": request.method,
        "path": request.url.path,
    }


def _read_only_view_requested(acting: int | None, mode_header: str | None) -> bool:
    return acting is not None and (mode_header or "").strip().lower() == "read"


from .routers.auth import get_current_user


def get_tenant_scope(
    request: Request,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(get_current_user),
    act_as: str | None = Header(default=None, alias=ACT_AS_HEADER),
    act_as_mode: str | None = Header(default=None, alias=ACT_AS_MODE_HEADER),
) -> TenantScope:
    acting = resolve_acting_organization(user, act_as, db)
    note_act_as_write(request, user, acting)
    if not is_super_admin(user):
        blocked = BLOCKED_ORGANIZATION_STATUSES.get(user.organization_status)
        if blocked:
            raise HTTPException(status_code=403, detail=blocked)
    return TenantScope(
        db=db, user=user, acting_organization_id=acting,
        acting_read_only=_read_only_view_requested(acting, act_as_mode),
    )


def get_billing_scope(
    request: Request,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(get_current_user),
    act_as: str | None = Header(default=None, alias=ACT_AS_HEADER),
    act_as_mode: str | None = Header(default=None, alias=ACT_AS_MODE_HEADER),
) -> TenantScope:
    """Scope for the storefront routes.

    Unlike get_tenant_scope this lets a `pending_approval` workspace through, because paying
    is exactly how a self-serve workspace leaves that state -- gating the purchase behind the
    status it is trying to clear would be a deadlock. A `suspended` or `rejected` workspace
    still cannot reach it: those are operator decisions money is not allowed to override.
    """
    acting = resolve_acting_organization(user, act_as, db)
    note_act_as_write(request, user, acting)
    if not is_super_admin(user):
        status = user.organization_status
        if status in ("suspended", "rejected", "removed"):
            raise HTTPException(status_code=403, detail=BLOCKED_ORGANIZATION_STATUSES[status])
    # Same header as the tenant scope, or an operator inside a workspace would see the
    # storefront answer for their OWN organisation while every other page showed the
    # tenant's -- the two disagreeing about who is being looked at.
    return TenantScope(
        db=db, user=user, acting_organization_id=acting,
        acting_read_only=_read_only_view_requested(acting, act_as_mode),
    )


def require_super_admin(
    scope: TenantScope = Depends(get_tenant_scope),
) -> TenantScope:
    """Platform-operator routes only.

    Every caller of this used to accept `role in ("manager", "owner")` as well. Since
    every Google signup is created with role="owner", that made every customer a platform
    administrator: able to list all tenants, read their owners' email addresses, approve
    their own workspace, rewrite anyone's quota and suspend a competitor.
    """
    if not is_super_admin(scope.user):
        raise HTTPException(
            status_code=403,
            detail="Only platform administrators can perform this action.",
        )
    return scope


def ensure_feature(scope: "TenantScope", feature: str) -> None:
    """Refuse unless this workspace's package includes `feature`. 403 if it does not.

    The imperative half of `require_feature`, for the gates that cannot be a dependency
    because the feature is only involved when the payload asks for it -- attaching a
    schedule to a playlist item, say, where refusing the whole route would stop a tenant
    adding items at all.
    """
    if is_super_admin(scope.user):
        return
    # Local import: billing imports models only, but keeping it here avoids any
    # import-order coupling in this early module.
    from .billing import plan_features

    org = scope.db.query(models.Organization).filter(
        models.Organization.id == scope.organization_id
    ).first()
    plan = org.plan if org else None
    if not (plan and plan_features(plan).get(feature)):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Your plan does not include {feature.replace('_', ' ')}. "
                f"Upgrade your plan to enable it."
            ),
        )


def require_feature(feature: str):
    """Gate a route behind a plan feature flag (e.g. "emergency_alert").

    Add alongside the route's role dependency; both resolve the same request-cached scope.
    A super admin is exempt, and a workspace with no plan has no features, so the gate holds
    closed until a package that includes the flag is bought.
    """
    def dependency(scope: TenantScope = Depends(get_tenant_scope)) -> TenantScope:
        ensure_feature(scope, feature)
        return scope

    return dependency


def require_tenant_roles(*roles: str, writable: bool = True):
    def dependency(scope: TenantScope = Depends(get_tenant_scope)) -> TenantScope:
        if not is_super_admin(scope.user) and scope.user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        if writable:
            scope.require_write_access()
            if scope.is_read_only():
                raise HTTPException(
                    status_code=403,
                    detail="Subscription requires attention; dashboard changes are read-only until billing is restored",
                )
        return scope

    return dependency
