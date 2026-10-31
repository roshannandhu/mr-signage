"""Selling an advert: which client, for how long, in which places.

The placement is a record of the deal. The thing that actually plays is still an ordinary
playlist item, created and removed by this module on the placement's behalf. Everything
downstream — the player sync, proof-of-play, rendition selection, rotation — keeps reading
playlist items and needs no knowledge that bookings exist.
"""
import logging
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func

from .. import models, schemas
from ..tenancy import TenantScope, ensure_feature, get_tenant_scope, require_feature, require_tenant_roles
from ..media_urls import resolve_media_url, client_ads_prefix, get_s3_config, is_s3_enabled, s3_client
from .playlists import bump_playlist

logger = logging.getLogger(__name__)

from ..services import (
    REPORTING_GRACE,
    effective_ends_at,
    total_price_paise,
    settlement,
    refresh_paid_state,
    playlist_for_target as _playlist_for_target,
    place_advert as _place,
    unplace_advert as _unplace,
    repair_orphaned_targets as _repair_targets,
)

router = APIRouter()



def resolve_client(scope: TenantScope, client_id: int | None) -> models.Client | None:
    """Look a client up inside the tenant, 404 if it is not theirs.

    Via scope.get rather than a bare query so naming another tenant's client id is a 404
    and not a booking silently attributed to a stranger's customer.
    """
    if client_id is None:
        return None
    client = scope.get(models.Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")
    return client


def resolve_tenant_plan(scope: TenantScope, plan_id: int | None) -> models.TenantPlan | None:
    if plan_id is None:
        return None
    plan = scope.get(models.TenantPlan, plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    return plan


def _sell_more_time(
    scope: TenantScope,
    placement: models.AdPlacement,
    *,
    extended_to,
    additional_price_paise: int,
    extended_from=None,
    is_paid: bool = False,
    notes: str | None = None,
) -> models.AdPlacementExtension:
    """The one implementation of "more time was sold against this booking".

    Both routes here that sell time -- an outright extension and a plan change that carries
    the run forward -- used to build the row and then repeat the same two follow-ups, each
    with its own copy of the comment explaining why they are not optional. Two copies of a
    step that must never be skipped is how one of them eventually gets skipped; content.py
    documents that exact outcome elsewhere in this codebase.

    Deliberately does NOT commit. The callers own the transaction, because a plan change
    writes `plan_id` in the same one.
    """
    # Defaults to continuing from wherever the run currently finishes, so back-to-back
    # extensions cannot leave an unpaid gap the advert would go dark in.
    starts = extended_from or effective_ends_at(placement)
    if extended_to <= starts:
        raise HTTPException(
            status_code=422,
            detail="The extension must end after it begins",
        )

    extension = models.AdPlacementExtension(
        placement_id=placement.id,
        extended_from=starts,
        extended_to=extended_to,
        additional_price_paise=additional_price_paise,
        is_paid=is_paid,
        notes=notes,
    )
    scope.db.add(extension)
    scope.db.flush()
    scope.db.refresh(placement)

    # Without this the extension is a database row and nothing else: the player stops the
    # advert on PlaylistItem.end_at, which is still the original date.
    sync_placement_window(scope, placement)
    # More was sold, so a settled booking is settled no longer.
    refresh_paid_state(placement)
    return extension


def sync_placement_window(scope: TenantScope, placement: models.AdPlacement) -> None:
    """Push each location's run window onto the playlist item it placed.

    The player enforces the paid window through PlaylistItem.start_at/end_at, so an
    extension that only wrote a row here would be invisible on every screen: the advert
    would stop on the original date the client no longer holds.

    Per TARGET, not per booking. Two reasons:

      - A location may be sold its own length -- 30 days in a mall, 10 in a shop, 50 at an
        airport, one client, one booking. AdPlacementTarget.effective_ends_at falls back to
        the booking's, so a target with no window of its own is unaffected.

      - This used to assign `placement.starts_at` to every item, which silently undid the
        per-target start _place computes with max(placement.starts_at, assigned_at). A
        screen added on day 10 of a campaign had its start reset to day 1 the next time
        anything re-synced the window -- an extension, a date edit -- and then billed from
        a date it was not yet running.

    Bumps each affected playlist for the same reason _place does -- sync_tv answers 204
    until playlist.updated_at moves, so without it the screens keep the old window until
    something unrelated happens to touch the playlist.
    """
    playlists = set()
    for target in placement.targets:
        if not target.playlist_item_id:
            continue
        item = scope.db.query(models.PlaylistItem).filter(
            models.PlaylistItem.id == target.playlist_item_id
        ).first()
        if not item:
            continue
        item.start_at = target.effective_starts_at
        item.end_at = target.effective_ends_at
        if item.playlist:
            playlists.add(item.playlist)
    for playlist in playlists:
        bump_playlist(playlist)


@dataclass(frozen=True)
class BookingNames:
    """Names, locations and members for the screens and groups a booking can point at.

    Built ONCE per request. This used to be built inside `_serialize`, which the bookings
    list calls once per placement -- four full-table queries each, so twenty bookings
    issued eighty of them plus a member lookup per booking. Against a database in another
    region that is several times the entire page budget, for data that is identical on
    every pass.
    """

    screen_names: dict[int, str]
    screen_locations: dict[int, str | None]
    group_names: dict[int, str]
    group_locations: dict[int, list[str]]
    group_members: dict[int, list[int]]


def booking_names(scope: TenantScope) -> BookingNames:
    """One scoped pass over the workspace's screens and groups.

    Through `scope.query`, not `scope.db.query`: the funnel is what applies the
    organisation filter and drops archived rows. Reading around it pulled every tenant's
    screens -- and every deleted one -- into a map built to resolve the names of one
    booking's own targets.
    """
    screen_names: dict[int, str] = {}
    screen_locations: dict[int, str | None] = {}
    group_locations: dict[int, list[str]] = {}
    group_members: dict[int, list[int]] = {}

    rows = (
        scope.query(models.Screen)
        .with_entities(
            models.Screen.id,
            models.Screen.name,
            models.Screen.location,
            models.Screen.group_id,
        )
        .all()
    )
    for screen_id, name, location, group_id in rows:
        screen_names[screen_id] = name
        screen_locations[screen_id] = location
        if group_id is not None:
            group_members.setdefault(group_id, []).append(screen_id)
            if location and location.strip():
                group_locations.setdefault(group_id, []).append(location.strip())

    group_names = dict(
        scope.query(models.ScreenGroup)
        .with_entities(models.ScreenGroup.id, models.ScreenGroup.name)
        .all()
    )
    return BookingNames(
        screen_names=screen_names,
        screen_locations=screen_locations,
        group_names=group_names,
        group_locations=group_locations,
        group_members=group_members,
    )


def _serialize(
    scope: TenantScope,
    placement: models.AdPlacement,
    names: BookingNames | None = None,
) -> schemas.PlacementResponse:
    # Optional so the fourteen single-booking callers stay a one-liner; the list endpoint
    # builds it once and hands the same one to every row.
    names = names or booking_names(scope)
    screen_names = names.screen_names
    screen_locations = names.screen_locations
    group_names = names.group_names
    group_locations = names.group_locations

    now = models.utcnow()
    ends = effective_ends_at(placement)
    days_left = max(0, (ends - now).days) if ends > now else 0
    content = placement.content or scope.get(models.Content, placement.content_id)
    creative_name = content.name if content else None
    creative_thumb = resolve_media_url(content.thumbnail or content.file_url) if content else None
    # Groups expanded, the same way the cap counts them, so "3 of 5" means three TVs and
    # not three rows.
    usage = plan_screen_usage(
        placement.plan,
        set(_booking_screen_ids(scope, placement, names.group_members)),
        placement,
    )
    # Reported rather than left to each caller to work out. See settlement().
    money = settlement(placement)

    return schemas.PlacementResponse(
        id=placement.id,
        content_id=placement.content_id,
        creative_name=creative_name,
        creative_thumbnail_url=creative_thumb,
        advertiser=placement.advertiser,
        client=schemas.ClientResponse.model_validate(placement.client) if placement.client else None,
        plan=schemas.TenantPlanResponse.model_validate(placement.plan) if placement.plan else None,
        extensions=[schemas.ExtensionResponse.model_validate(e) for e in placement.extensions],
        effective_ends_at=ends,
        days_remaining=days_left,
        total_price_paise=money["total"],
        price_paise=placement.price_paise,
        is_paid=placement.is_paid,
        amount_paid_paise=money["received"],
        balance_due_paise=money["balance"],
        payment_status=money["status"],
        starts_at=placement.starts_at,
        ends_at=placement.ends_at,
        notes=placement.notes,
        created_at=placement.created_at,
        targets=[
            schemas.PlacementTargetResponse(
                id=t.id,
                screen_id=t.screen_id,
                group_id=t.group_id,
                name=(screen_names.get(t.screen_id) or f"Screen {t.screen_id}") if t.screen_id
                else (group_names.get(t.group_id) or f"Group {t.group_id}"),
                location=(
                    screen_locations.get(t.screen_id)
                    if t.screen_id
                    else (", ".join(dict.fromkeys(group_locations.get(t.group_id, []))) or None)
                ),
                kind="screen" if t.screen_id else "group",
                is_placed=t.playlist_item_id is not None,
                starts_at=t.starts_at,
                ends_at=t.ends_at,
                # Reported as days too, because that is the unit the deal was struck in
                # and the dashboard should not have to subtract two dates to show it back.
                days=(
                    max(1, round((t.ends_at - t.starts_at).total_seconds() / 86400))
                    if t.ends_at and t.starts_at
                    else None
                ),
            )
            for t in placement.targets
        ],
        screens_used=usage["used"],
        plan_max_locations=usage["allowed"],
        screens_unused=usage["unused"],
        max_locations=placement.max_locations or 0,
        # Every receipt, oldest first. The client asking "what have I already sent you?"
        # wants the list, and an operator reconciling a bank statement wants the
        # references -- neither is answerable from a single collapsed figure.
        payments=[
            schemas.PaymentResponse(
                id=payment.id,
                amount_paise=payment.amount_paise,
                method=payment.method,
                reference=payment.reference,
                paid_at=payment.paid_at,
                notes=payment.notes,
                # The name, not the id: this is read by a human chasing a receipt.
                recorded_by=payment.recorded_by.username if payment.recorded_by else None,
                created_at=payment.created_at,
            )
            for payment in placement.payments
        ],
    )


@router.get("/", response_model=list[schemas.PlacementResponse])
def list_placements(
    content_id: int | None = None,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor", "viewer")),
):
    query = scope.query(models.AdPlacement)
    if content_id is not None:
        query = query.filter(models.AdPlacement.content_id == content_id)
    names = booking_names(scope)
    return [
        _serialize(scope, p, names)
        for p in query.order_by(models.AdPlacement.created_at.desc()).all()
    ]


def ensure_ad_slot_quota(scope: TenantScope) -> None:
    """Reject the caller when the organisation is already at its ad-slot limit.

    Two tiers, in the same order and with the same "0 means unlimited" rule that
    screens.ensure_screen_quota uses for screens:

    1. The per-org limit a platform administrator set (Organization.max_ad_slots).
    2. The package the org is on (Plan.max_ad_slots), as the fallback.

    Only tier 1 existed, so a tenant on a package with an ad limit and no hand-set override
    was silently unlimited -- and every organisation defaults to max_ad_slots=0.

    "Active" means anything not yet finished, including a booking that has not started, so
    a future campaign holds its slot rather than being sellable twice.
    """
    org = scope.db.query(models.Organization).filter(
        models.Organization.id == scope.organization_id
    ).first()
    if not org:
        return

    # Same derivation as screens, via the shared property: override when set, otherwise
    # the package. This was the correct fallback written out inline here while
    # ensure_screen_quota had a subtly different one that returned early on a 0 override --
    # two copies of one rule that disagreed.
    limit = org.effective_max_ad_slots
    if limit is None or limit <= 0:
        return

    # On the EFFECTIVE end. Counting the sold `ends_at` freed the slot of any campaign kept
    # running by an extension or a longer per-location window, so a tenant at their limit
    # could sell past it simply by having extended something.
    active_ads = scope.db.query(models.AdPlacement).filter(
        models.AdPlacement.organization_id == scope.organization_id,
        models.AdPlacement.effective_ends_at >= models.utcnow(),
    ).count()
    if active_ads >= limit:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Ad slot quota reached ({active_ads}/{limit}). "
                f"Contact your platform administrator to increase your ad limit."
            ),
        )


def reactivates(placement: models.AdPlacement, new_ends_at) -> bool:
    """Whether setting this end date would bring a FINISHED booking back to life.

    A finished booking no longer holds an ad slot, so reviving it takes one and has to pass
    the same limit a new booking does. Editing the end date was a way round that limit.
    """
    if new_ends_at is None:
        return False
    now = models.utcnow()
    if new_ends_at.tzinfo is None:
        new_ends_at = new_ends_at.replace(tzinfo=timezone.utc)
    return placement.effective_ends_at <= now and new_ends_at > now


def deletion_blocker(placement: models.AdPlacement) -> str | None:
    """Why deleting this booking would destroy something that has to be kept, or None.

    Deleting a booking deletes its payment receipts with it (cascade), and a receipt is the
    only record that money changed hands. Mistaken entries can still be removed -- one at a
    time, deliberately -- from the booking's payments.
    """
    count = len(placement.payments)
    if count:
        return (
            f"This booking has {count} recorded payment{'s' if count != 1 else ''}. "
            "Payments are financial records: remove them first if they were entered by mistake."
        )
    return None


def content_deletion_blocker(content: models.Content) -> str | None:
    """Why deleting this advert would take bookings or payments with it, or None."""
    now = models.utcnow()
    for placement in content.ad_placements:
        reason = deletion_blocker(placement)
        if reason:
            return reason
        if placement.effective_ends_at > now:
            return (
                f"This advert is booked to {placement.advertiser or 'a client'} until "
                f"{placement.effective_ends_at.date().isoformat()}. End or delete that booking first."
            )
    return None


def relocate_content_to_client(scope: TenantScope, content_id: int, client: models.Client | None) -> None:
    """When an ad is assigned to a client, move its master file, thumbnail, and renditions
    into the client's ads folder if they are not already there.
    """
    if not client:
        return
    content = scope.get(models.Content, content_id)
    if not content or not content.file_url:
        return

    target_prefix = client_ads_prefix(scope.organization, client)

    # 1. Relocate S3 / R2 objects
    if content.file_url.startswith("s3://") and is_s3_enabled():
        current_key = content.file_url.removeprefix("s3://").lstrip("/")
        if not current_key.startswith(f"{target_prefix}/"):
            filename = current_key.rsplit("/", 1)[-1]
            new_key = f"{target_prefix}/{filename}"
            s3 = s3_client()
            cfg = get_s3_config()
            bucket = cfg["bucket"]
            try:
                s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": current_key}, Key=new_key)
                s3.delete_object(Bucket=bucket, Key=current_key)
                content.file_url = f"s3://{new_key}"
                logger.info("Relocated content %s master to %s", content_id, new_key)
            except Exception as exc:
                logger.warning("Failed to relocate S3 content %s from %s to %s: %s", content_id, current_key, new_key, exc)

            # Thumbnail
            if content.thumbnail and content.thumbnail.startswith("s3://"):
                old_thumb = content.thumbnail.removeprefix("s3://").lstrip("/")
                if not old_thumb.startswith(f"{target_prefix}/"):
                    new_thumb = f"{target_prefix}/{old_thumb.rsplit('/', 1)[-1]}"
                    try:
                        s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": old_thumb}, Key=new_thumb)
                        s3.delete_object(Bucket=bucket, Key=old_thumb)
                        content.thumbnail = f"s3://{new_thumb}"
                    except Exception as exc:
                        logger.warning("Failed to relocate thumbnail %s: %s", old_thumb, exc)

            # Renditions
            renditions = scope.db.query(models.MediaRendition).filter(models.MediaRendition.content_id == content.id).all()
            for rend in renditions:
                if rend.file_url and rend.file_url.startswith("s3://"):
                    old_rend = rend.file_url.removeprefix("s3://").lstrip("/")
                    if not old_rend.startswith(f"{target_prefix}/"):
                        new_rend = f"{target_prefix}/{old_rend.rsplit('/', 1)[-1]}"
                        try:
                            s3.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": old_rend}, Key=new_rend)
                            s3.delete_object(Bucket=bucket, Key=old_rend)
                            rend.file_url = f"s3://{new_rend}"
                        except Exception as exc:
                            logger.warning("Failed to relocate rendition %s: %s", old_rend, exc)

    # 2. Relocate local filesystem objects
    elif content.file_url.startswith("/uploads/"):
        upload_dir = os.getenv("UPLOAD_DIR", "uploads")
        rel_path = content.file_url.removeprefix("/uploads/").lstrip("/")
        if not rel_path.startswith(f"{target_prefix}/"):
            filename = os.path.basename(rel_path)
            new_rel_path = f"{target_prefix}/{filename}"
            old_full = os.path.join(upload_dir, rel_path)
            new_full = os.path.join(upload_dir, new_rel_path)
            os.makedirs(os.path.dirname(new_full), exist_ok=True)
            if os.path.exists(old_full):
                try:
                    shutil.move(old_full, new_full)
                    content.file_url = f"/uploads/{new_rel_path}"
                except Exception as exc:
                    logger.warning("Failed to move local file %s to %s: %s", old_full, new_full, exc)

            # Local thumbnail
            if content.thumbnail and content.thumbnail.startswith("/uploads/"):
                old_t_rel = content.thumbnail.removeprefix("/uploads/").lstrip("/")
                if not old_t_rel.startswith(f"{target_prefix}/"):
                    new_t_rel = f"{target_prefix}/{os.path.basename(old_t_rel)}"
                    old_t_full = os.path.join(upload_dir, old_t_rel)
                    new_t_full = os.path.join(upload_dir, new_t_rel)
                    os.makedirs(os.path.dirname(new_t_full), exist_ok=True)
                    if os.path.exists(old_t_full):
                        try:
                            shutil.move(old_t_full, new_t_full)
                            content.thumbnail = f"/uploads/{new_t_rel}"
                        except Exception:
                            pass

            # Local renditions
            renditions = scope.db.query(models.MediaRendition).filter(models.MediaRendition.content_id == content.id).all()
            for rend in renditions:
                if rend.file_url and rend.file_url.startswith("/uploads/"):
                    old_r_rel = rend.file_url.removeprefix("/uploads/").lstrip("/")
                    if not old_r_rel.startswith(f"{target_prefix}/"):
                        new_r_rel = f"{target_prefix}/{os.path.basename(old_r_rel)}"
                        old_r_full = os.path.join(upload_dir, old_r_rel)
                        new_r_full = os.path.join(upload_dir, new_r_rel)
                        os.makedirs(os.path.dirname(new_r_full), exist_ok=True)
                        if os.path.exists(old_r_full):
                            try:
                                shutil.move(old_r_full, new_r_full)
                                rend.file_url = f"/uploads/{new_r_rel}"
                            except Exception:
                                pass


@router.post("/", response_model=schemas.PlacementResponse, status_code=201)
def create_placement(
    payload: schemas.PlacementCreate,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    if not scope.get(models.Content, payload.content_id):
        raise HTTPException(status_code=404, detail="Content not found")

    # One advert, one live sale. A second booking of a creative that is still running bills
    # the client twice on the invoice, lists the campaign twice on their report, and spends
    # a second ad slot out of the workspace's quota -- so a tenant allowed ten slots hits
    # the cap at three real campaigns. Selling more screens or more time on a running
    # advert is what add_target and extensions are for, and both keep it one campaign.
    #
    # Only LIVE bookings block: once a campaign has finished, the creative is free to be
    # sold again, which is the ordinary renewal.
    running = (
        scope.query(models.AdPlacement)
        .filter(
            models.AdPlacement.content_id == payload.content_id,
            models.AdPlacement.effective_ends_at > models.utcnow(),
        )
        .first()
    )
    if running is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"This advert is already booked to {running.advertiser} until "
                f"{running.effective_ends_at.date().isoformat()}. Add screens to that "
                "booking, or extend it, rather than starting a second one."
            ),
        )

    ensure_ad_slot_quota(scope)

    client = resolve_client(scope, payload.client_id)
    plan = resolve_tenant_plan(scope, payload.plan_id)

    # The client record is the name of record; `advertiser` follows it. A booking may still
    # be made on a bare name -- schemas.PlacementCreate requires one or the other.
    advertiser = client.name if client else (payload.advertiser or "").strip()

    # A plan fills in what the caller left at its default. COPIED, never read through: the
    # tenant reprices plans as their business changes and a sold campaign must keep the
    # terms it was sold on. See routers/tenant_plans.
    price_paise = payload.price_paise
    ends_at = payload.ends_at
    if plan:
        if not price_paise:
            price_paise = plan.price_paise
        if ends_at is None:
            ends_at = payload.starts_at + timedelta(days=plan.duration_days)

    placement = models.AdPlacement(
        organization_id=scope.organization_id,
        content_id=payload.content_id,
        advertiser=advertiser,
        client_id=client.id if client else None,
        plan_id=plan.id if plan else None,
        # Only meaningful off a package; stored either way so moving OFF a plan later keeps
        # the figure the operator typed rather than silently uncapping the booking.
        max_locations=payload.max_locations,
        price_paise=price_paise,
        is_paid=payload.is_paid,
        starts_at=payload.starts_at,
        ends_at=ends_at,
        notes=payload.notes,
    )
    scope.db.add(placement)
    scope.db.flush()

    # Checked before the first item is placed, so a booking that breaches its plan is
    # refused whole rather than half-created.
    ensure_plan_locations(
        scope, plan, set(), _screens_for_refs(scope, payload.targets), placement
    )

    for ref in payload.targets:
        _place(scope, placement, ref, assigned_at=payload.starts_at)

    # Last, once nothing can refuse the booking. This moves (or deletes) the stored files, and
    # a rollback cannot bring them back: doing it before the plan-cap checks left the advert
    # pointing at an object that was no longer there whenever one of them said no.
    if client:
        relocate_content_to_client(scope, payload.content_id, client)

    scope.db.commit()
    scope.db.refresh(placement)
    return _serialize(scope, placement)


@router.put("/{placement_id}", response_model=schemas.PlacementResponse)
def update_placement(
    placement_id: int,
    payload: schemas.PlacementUpdate,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")

    fields = payload.model_fields_set
    # Before any field is written: the check counts running bookings, and a pending end date on
    # this one would make it count itself.
    if "ends_at" in fields and reactivates(placement, payload.ends_at):
        ensure_ad_slot_quota(scope)

    relocate_after = None
    # `is_paid` is deliberately not in this list any more. It is now the shadow of the
    # payment record, set only by POST/DELETE /payment, so the flag and the receipt cannot
    # tell different stories -- which they could when a blind PUT alongside a date edit
    # could mark a campaign paid with no amount, method or date behind it.
    for field in ("advertiser", "price_paise", "notes", "starts_at", "ends_at"):
        if field in fields:
            setattr(placement, field, getattr(payload, field))

    if "client_id" in fields:
        client = resolve_client(scope, payload.client_id)
        placement.client_id = client.id if client else None
        # The label follows the record, so a report never prints a name the booking is no
        # longer attributed to.
        if client:
            placement.advertiser = client.name
            relocate_after = client
    if "plan_id" in fields:
        plan = resolve_tenant_plan(scope, payload.plan_id)
        # Against the screens the booking ALREADY reaches. Moving a six-screen booking onto
        # a five-screen plan is the same breach as adding a sixth screen to that plan, and
        # this path let it through -- the plan was enforced when screens changed but not
        # when the plan did.
        ensure_plan_locations(
            scope, plan, set(_booking_screen_ids(scope, placement)), set(), placement
        )
        placement.plan_id = plan.id if plan else None

    if placement.ends_at <= placement.starts_at:
        raise HTTPException(status_code=422, detail="The end date must be after the start date")

    # Correcting the price changes what is outstanding against a payment already taken.
    if "price_paise" in fields:
        refresh_paid_state(placement)

    # Every placed item carries the booking's window, so moving the dates moves them all --
    # and the screens have to be told, which the previous version of this did not do. It
    # rewrote item.start_at/end_at and stopped, leaving playlist.updated_at untouched, so
    # sync_tv answered 204 and every screen kept running the OLD window. A booking moved
    # earlier kept playing past the date it was moved to.
    if fields & {"starts_at", "ends_at"}:
        sync_placement_window(scope, placement)

    # Last, for the same reason as in create_placement: every check above has passed.
    if relocate_after:
        relocate_content_to_client(scope, placement.content_id, relocate_after)

    scope.db.commit()
    scope.db.refresh(placement)
    return _serialize(scope, placement)


@router.post("/{placement_id}/targets", response_model=schemas.PlacementResponse, status_code=201)
def add_target(
    placement_id: int,
    ref: schemas.PlacementTargetRef,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")
    already = any(
        (t.screen_id == ref.screen_id and ref.screen_id is not None)
        or (t.group_id == ref.group_id and ref.group_id is not None)
        for t in placement.targets
    )
    if already:
        raise HTTPException(status_code=409, detail="This booking already runs in that place")

    # A screen added AFTER the sale cannot run longer than the sale itself.
    #
    # Deliberately here and not in place_advert, because the two paths are not the same
    # deal. At creation, per-location days ARE the sale -- "50 days at the airport, 30 in
    # the mall" is quoted in screen-days and priced, and AdPlacement.effective_ends_at is
    # defined as the max over its targets precisely so the campaign covers its longest
    # location. Adding a location later has no such quote: `days` was bounded only by its
    # own schema (le=3650), so typing 365 on the screen page silently dragged the whole
    # campaign's end date out with it -- more delivery, no extension row, nothing billed.
    #
    # Refused rather than quietly shortened: the operator typed a number and has to see
    # that it is not the one being sold. Extending the booking is the route to a longer
    # run, and that one charges for it.
    if ref.days:
        starts_at = max(placement.starts_at, models.utcnow())
        booking_ends_at = effective_ends_at(placement)
        if starts_at + timedelta(days=ref.days) > booking_ends_at:
            allowed = (booking_ends_at - starts_at).days
            raise HTTPException(
                status_code=409,
                detail=(
                    f"This booking runs until {booking_ends_at.date().isoformat()}, which "
                    f"is {max(0, allowed)} more day{'' if allowed == 1 else 's'}. Extend the "
                    "booking to sell a longer run, or leave the run length empty to finish "
                    "with the campaign."
                ),
            )

    # The plan's screen count binds here too, not only at creation -- adding places one at
    # a time was otherwise the way straight past it.
    ensure_plan_locations(
        scope, placement.plan,
        set(_booking_screen_ids(scope, placement)),
        _screens_for_refs(scope, [ref]),
        placement,
    )

    _place(scope, placement, ref)
    scope.db.commit()
    scope.db.refresh(placement)

    from .websockets import trigger_screen_sync
    target_screen = scope.get(models.Screen, ref.screen_id) if ref.screen_id else None
    trigger_screen_sync(
        organization_id=scope.organization_id,
        screen_device_id=target_screen.device_id if target_screen else None,
        group_id=ref.group_id,
    )
    return _serialize(scope, placement)


@router.post("/{placement_id}/replace", response_model=schemas.PlacementResponse)
def replace_targets(
    placement_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    """Put a booking back on the screens it was sold to, for every place it has fallen off.

    A target holds the playlist item it created, and that column is ON DELETE SET NULL --
    deliberately, so an operator deleting the row by hand on the screen page leaves the
    booking standing as a record of what was sold. But PlaylistItem.content_id is ON DELETE
    CASCADE, so deleting or re-uploading a creative takes every playlist item with it and
    nulls the same column. The booking then reads "Running" and "Paid" while playing on
    nothing at all, and nothing in the product could put it back: the only route was to
    delete the target and re-add it, which loses assigned_at and with it the per-location
    figures on the client's report.

    Idempotent, and only ever ADDS. A target that still holds its item is left exactly as it
    is, so this cannot duplicate an advert into a loop or rewrite a window that is playing.
    """
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")

    # Shared with the reconcile loop that does this automatically, so the manual button
    # and the self-healing timer can never disagree about what "put it back" means.
    if not _repair_targets(scope, placement):
        return _serialize(scope, placement)

    scope.db.commit()
    scope.db.refresh(placement)

    from .websockets import trigger_screen_sync
    trigger_screen_sync(organization_id=scope.organization_id)
    return _serialize(scope, placement)


@router.delete("/{placement_id}/targets/{target_id}", response_model=schemas.PlacementResponse)
def remove_target(
    placement_id: int,
    target_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    """Stop this advert playing in one place. Everywhere else is unaffected."""
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")
    target = next((t for t in placement.targets if t.id == target_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="This booking does not run in that place")

    screen_device_id = target.screen.device_id if target.screen else None
    group_id = target.group_id

    _unplace(scope, target)
    scope.db.commit()
    scope.db.refresh(placement)

    from .websockets import trigger_screen_sync
    trigger_screen_sync(
        organization_id=scope.organization_id,
        screen_device_id=screen_device_id,
        group_id=group_id,
    )
    return _serialize(scope, placement)


@router.post("/{placement_id}/targets/{target_id}/split", response_model=schemas.PlacementResponse)
def split_group_target(
    placement_id: int,
    target_id: int,
    payload: schemas.PlacementSplit,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    """Take a group booking off one screen without disturbing the rest of the group.

    A group target places a single item in the shared group playlist, so it cannot be
    removed from one member alone. This replaces it with one target per remaining screen,
    which gives the same result and keeps a single rule deciding what plays.
    """
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")
    target = next((t for t in placement.targets if t.id == target_id), None)
    if not target or not target.group_id:
        raise HTTPException(status_code=404, detail="That is not a group booking")

    members = scope.query(models.Screen).filter(models.Screen.group_id == target.group_id).all()
    keep = [s for s in members if s.id not in set(payload.exclude_screen_ids)]
    if not keep:
        raise HTTPException(status_code=422, detail="That would leave the booking with nowhere to play")

    # No ensure_plan_locations here, deliberately. A group target already counts as every
    # member, and `keep` is a subset of those, so a split can only hold the screen count
    # level or lower it. Checking would be dead code at best -- and at worst it would refuse
    # a split on a booking that is already over its plan, which is the very edit that fixes
    # the breach.

    # Read before _unplace deletes the row it lives on. Splitting changes only how the
    # booking is recorded, not what the client bought, so each surviving screen keeps the
    # date the GROUP was assigned -- taking `now` here would silently reset a fortnight of
    # airtime to today on every screen in the group.
    group_assigned_at = target.assigned_at

    _unplace(scope, target)
    for screen in keep:
        _place(scope, placement, schemas.PlacementTargetRef(screen_id=screen.id),
               assigned_at=group_assigned_at)

    scope.db.commit()
    scope.db.refresh(placement)
    return _serialize(scope, placement)


@router.delete("/{placement_id}")
def delete_placement(
    placement_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")
    blocker = deletion_blocker(placement)
    if blocker:
        raise HTTPException(status_code=409, detail=blocker)
    for target in list(placement.targets):
        _unplace(scope, target)
    scope.db.delete(placement)
    scope.db.commit()
    return {"status": "deleted"}


@router.post("/{placement_id}/extensions", response_model=schemas.PlacementResponse, status_code=201)
def add_extension(
    placement_id: int,
    payload: schemas.ExtensionCreate,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    """Sell more time on a booking that is already running.

    A row per extension, not an `extended_to` column: a campaign that performs is extended
    more than once and each sale has to survive on the invoice.
    """
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")

    _sell_more_time(
        scope,
        placement,
        extended_to=payload.extended_to,
        additional_price_paise=payload.additional_price_paise,
        extended_from=payload.extended_from,
        is_paid=payload.is_paid,
        notes=payload.notes,
    )

    scope.db.commit()
    scope.db.refresh(placement)
    return _serialize(scope, placement)


@router.delete("/{placement_id}/extensions/{extension_id}", response_model=schemas.PlacementResponse)
def remove_extension(
    placement_id: int,
    extension_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")

    extension = scope.db.query(models.AdPlacementExtension).filter(
        models.AdPlacementExtension.id == extension_id,
        # Scoped through the placement, which scope.get already proved is this tenant's.
        # Querying the extension by id alone would let one tenant delete another's.
        models.AdPlacementExtension.placement_id == placement.id,
    ).first()
    if not extension:
        raise HTTPException(status_code=404, detail="Extension not found")

    scope.db.delete(extension)
    scope.db.flush()
    scope.db.refresh(placement)
    # Pulls the run back in: an extension that was cancelled must stop playing.
    sync_placement_window(scope, placement)
    # And it is no longer owed, so a booking part-paid only because of it is settled again.
    refresh_paid_state(placement)

    scope.db.commit()
    scope.db.refresh(placement)
    return _serialize(scope, placement)


@router.get("/{placement_id}/plan-options", response_model=list[schemas.PlanOption])
def plan_options(
    placement_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor", "viewer")),
):
    """Every plan this booking could move to, with one marked as the one to pick.

    The recommendation is answered from what the client is ACTUALLY using -- the cheapest
    active plan that covers the screens already assigned. A client on Basic running five
    screens is told to move up because five screens is what they are being given, not
    because a heuristic guessed at their appetite.
    """
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")

    used = len(_booking_screen_ids(scope, placement))
    current = placement.plan
    plans = scope.query(models.TenantPlan).filter(
        models.TenantPlan.is_active.is_(True)
    ).order_by(models.TenantPlan.price_paise).all()

    # The plan the booking is ON, even after the tenant stopped selling it. Active-only was
    # right for what can be moved TO and wrong for what is being moved FROM: a campaign on
    # a retired package listed no current plan at all, so "Change plan" opened with nothing
    # marked as current and no way to tell what the client had bought. It is listed, badged
    # as current, and -- being retired -- not selectable, which is what `is_active` on the
    # plan itself says.
    if current is not None and not any(plan.id == current.id for plan in plans):
        plans = sorted([*plans, current], key=lambda plan: plan.price_paise)

    options = [
        schemas.PlanOption(
            plan=schemas.TenantPlanResponse.model_validate(plan),
            is_current=bool(current and plan.id == current.id),
            # max_locations of 0 means uncapped, so it fits anything. Kept purely about
            # the screen count: a retired plan is unsellable for a different reason, which
            # `plan.is_active` says on its own and the UI reports in its own words.
            fits=plan.max_locations <= 0 or plan.max_locations >= used,
        )
        for plan in plans
    ]

    # Cheapest that fits and is not the one they are already on. `plans` is already ordered
    # by price, so the first match is the cheapest. Nothing is recommended when the current
    # plan is the only one that fits -- an upgrade prompt with nowhere better to go is
    # noise, and recommending a costlier plan for no reason is worse than that.
    upgrade = next(
        (o for o in options if o.fits and not o.is_current and o.plan.is_active), None
    )
    if upgrade is not None:
        upgrade.recommended = True
    return options


@router.post("/{placement_id}/change-plan", response_model=schemas.PlacementResponse)
def change_plan(
    placement_id: int,
    payload: schemas.PlanChange,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    """Move a booking onto a different plan, or off one onto a custom price. REPLACES.

    The plan and the price become what was chosen. Nothing is added and no date moves:
    selling the client more time is a separate action (`/extensions`), and conflating the
    two is what made a correction bill twice. This used to charge the price *difference* as
    an extension AND push the end date out by the new plan's length, so "put this booking on
    the right plan" quietly sold the client another campaign -- their total read
    old price + difference over a run nobody agreed.

    Both directions, and back to itself: custom -> package, package -> custom, and custom ->
    custom to re-cut an agreed figure.

    One booking throughout, deliberately. Closing the old sale and opening a new one would
    split a single client's campaign across two records, two reports and two invoices, and
    the proof-of-play a client receives should cover the campaign they think they bought.
    """
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")

    if payload.plan_id is None and "plan_id" not in payload.model_fields_set:
        raise HTTPException(
            status_code=422,
            detail="Name the plan to change to, or send plan_id: null for a custom price",
        )

    plan = resolve_tenant_plan(scope, payload.plan_id)

    # The custom cap is re-cut here, because a renegotiation is exactly when it moves --
    # "same money, one more screen". Written BEFORE the check below so the check measures
    # the booking as it will be, not as it was: lowering the cap to fewer screens than the
    # booking already runs on has to be refused, and raising it has to be allowed to pass
    # immediately rather than being judged against the number being replaced.
    if payload.max_locations is not None:
        placement.max_locations = payload.max_locations

    # A change must never leave the booking delivering more screens than it is now billed
    # for -- the same breach as adding a screen, arrived at sideways. A plan states its own
    # limit and overrides the custom figure; with neither, nothing caps it, which
    # location_cap already answers.
    ensure_plan_locations(
        scope, plan, set(_booking_screen_ids(scope, placement)), set(), placement
    )

    placement.plan_id = plan.id if plan else None

    # Replaced, never added. An explicit figure wins -- that is the negotiated price the
    # tenant fixed. Otherwise a package hands over its list price, and moving to custom
    # without naming one keeps what the booking already costs rather than zeroing it.
    if payload.price_paise is not None:
        placement.price_paise = payload.price_paise
    elif plan is not None:
        placement.price_paise = plan.price_paise

    # The total just moved, so what the client still owes moved with it. Without this a
    # booking settled at the old price keeps reading "paid" over a balance it no longer
    # covers -- the gap this endpoint had from the day it was written.
    refresh_paid_state(placement)

    scope.db.commit()
    scope.db.refresh(placement)
    return _serialize(scope, placement)


@router.post("/{placement_id}/payments", response_model=schemas.PlacementResponse, status_code=201)
def record_payment(
    placement_id: int,
    payload: schemas.PaymentWrite,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
    _feature: TenantScope = Depends(require_feature("payslips")),
):
    """Record one receipt against the booking. The only way a booking becomes paid.

    ADDS a receipt; it does not replace the last one. It used to replace, because the table
    allowed one row per booking -- so an operator entering a client's second 5,000
    instalment overwrote the first, and the client was filed as having paid half of what
    they had actually handed over. A wrong entry is now corrected by deleting that receipt
    and recording the right one, which is also what leaves an honest ledger behind.

    Overpayment is accepted rather than rejected. Clients round up, pay an advance against
    the next campaign, or settle two bookings with one transfer; refusing the receipt would
    only mean the money went unrecorded. `settlement` floors the balance at zero.
    """
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")

    payment = models.AdPayment(
        organization_id=scope.organization_id,
        placement_id=placement.id,
        amount_paise=payload.amount_paise,
        method=payload.method,
        reference=(payload.reference or "").strip() or None,
        paid_at=payload.paid_at or models.utcnow(),
        notes=(payload.notes or "").strip() or None,
        # Who took the money. Kept even if they later leave -- the FK is SET NULL.
        recorded_by_user_id=scope.user.id,
    )
    scope.db.add(payment)
    scope.db.flush()
    scope.db.refresh(placement)
    # NOT an unconditional True. A part payment is a part payment: this flag is read
    # everywhere as "settled in full", and setting it on the first rupee is what made a
    # 5,000 deposit against a 50,000 campaign show as "Paid in full" on the ad page.
    placement.is_paid = settlement(placement)["balance"] == 0

    scope.db.commit()
    scope.db.refresh(placement)
    return _serialize(scope, placement)


@router.delete("/{placement_id}/payments", response_model=schemas.PlacementResponse)
def clear_payment(
    placement_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
    _feature: TenantScope = Depends(require_feature("payslips")),
):
    """Wipe the whole settlement: every receipt goes and the booking is unpaid again.

    Kept alongside the per-receipt delete below because they answer different questions.
    This one is "none of this was ever paid" -- and it is the only thing that can clear a
    booking flagged paid before payments were recorded at all, which has no receipt to
    remove and would otherwise be stuck reading as settled forever.
    """
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")
    for payment in list(placement.payments):
        scope.db.delete(payment)
    # Set regardless, for the legacy booking described above: it has no row to delete, and
    # leaving the flag true would make it unclearable.
    placement.is_paid = False

    scope.db.commit()
    scope.db.refresh(placement)
    return _serialize(scope, placement)


@router.delete("/{placement_id}/payments/{payment_id}", response_model=schemas.PlacementResponse)
def delete_payment(
    placement_id: int,
    payment_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
    _feature: TenantScope = Depends(require_feature("payslips")),
):
    """Remove ONE receipt -- a duplicate entry, or a cheque that bounced.

    For money that never arrived. A receipt that arrived but was typed wrong is corrected
    with PATCH instead, which keeps who took it and when. The remaining receipts still
    count, so pulling a mistaken second instalment does not un-pay the deposit that really
    was taken.
    """
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")

    payment = next((p for p in placement.payments if p.id == payment_id), None)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    scope.db.delete(payment)
    scope.db.flush()
    scope.db.refresh(placement)
    # Straight off the remaining receipts. refresh_paid_state would return early once the
    # last one is gone and leave the flag reading paid over a booking with no money on it.
    placement.is_paid = bool(placement.payments) and settlement(placement)["balance"] == 0

    scope.db.commit()
    scope.db.refresh(placement)
    return _serialize(scope, placement)


@router.patch("/{placement_id}/payments/{payment_id}", response_model=schemas.PlacementResponse)
def edit_payment(
    placement_id: int,
    payment_id: int,
    payload: schemas.PaymentEdit,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
    _feature: TenantScope = Depends(require_feature("payslips")),
):
    """Correct ONE receipt in place: a mistyped amount, the wrong method, a missing UTR.

    The money DID arrive -- only the record of it is wrong. Deleting and retyping was the
    only route before, which made an operator re-enter a row that was mostly right and threw
    away `recorded_by` and the original entry time in the process. Money that never arrived
    is still a DELETE.

    Only the fields actually sent are touched, so correcting a reference cannot blank a date
    by omission.
    """
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")

    payment = next((p for p in placement.payments if p.id == payment_id), None)
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(payment, field, value)

    scope.db.flush()
    scope.db.refresh(placement)
    # The amount may have moved, so what the client still owes moved with it. At least one
    # receipt exists by definition here, which is the case refresh_paid_state handles.
    refresh_paid_state(placement)

    scope.db.commit()
    scope.db.refresh(placement)
    return _serialize(scope, placement)


def _booking_screen_ids(
    scope: TenantScope,
    placement: models.AdPlacement,
    members_by_group: dict[int, list[int]] | None = None,
) -> list[int]:
    """Every screen this booking actually reaches.

    A group target is expanded to its current members, because that is what the advert
    physically played on — the client bought "the mall", not "a row in a groups table".
    """
    screen_ids: set[int] = set()
    group_ids: list[int] = []
    for target in placement.targets:
        if target.screen_id:
            screen_ids.add(target.screen_id)
        elif target.group_id:
            group_ids.append(target.group_id)
    if group_ids:
        if members_by_group is not None:
            # Already loaded for this request, so expanding a group costs nothing. Without
            # it the bookings list issued one member lookup per booking on top of everything
            # else it was repeating.
            for group_id in group_ids:
                screen_ids.update(members_by_group.get(group_id, ()))
        else:
            members = scope.query(models.Screen).filter(models.Screen.group_id.in_(group_ids)).all()
            screen_ids.update(screen.id for screen in members)
    return sorted(screen_ids)


def _commercials(placement: models.AdPlacement, generated_at: "datetime") -> dict:
    """The client, plan, period and money blocks a client report is built around.

    Shared by both exits of build_booking_report so the "no screens booked" report carries
    the same header as a full one -- a client whose screens were all removed still gets a
    document that names them and what they paid.
    """
    ends = effective_ends_at(placement)
    starts = placement.starts_at

    # Whole days, and never negative: a report pulled on the last afternoon should read
    # "0 days remaining", not "-1".
    day = 86400
    days_total = max(1, round((ends - starts).total_seconds() / day))
    days_remaining = max(0, round((ends - generated_at).total_seconds() / day))
    # Elapsed drives the per-day averages. Clamped to at least 1 so a report pulled on the
    # first day divides by one day rather than by zero, and never exceeds the period so a
    # report pulled after the end does not keep diluting the average.
    days_elapsed = min(days_total, max(1, round((min(generated_at, ends) - starts).total_seconds() / day)))

    client = placement.client
    plan = placement.plan
    organization = placement.organization
    money = settlement(placement)
    # Whose report this is FROM. The header band and the footer both carry it, so a client
    # receiving the PDF can tell who sent it without opening the covering email.
    return {
        "organization": {
            # brand_name is what the tenant wants a CLIENT to see; `name` is the workspace
            # name they typed at signup, which is often "<person>'s Workspace".
            "name": ((organization.brand_name or organization.name) if organization else "Signage network"),
            "logo": resolve_media_url(organization.logo_url) if organization and organization.logo_url else None,
            "brand_color": organization.brand_color if organization else None,
            "email": getattr(organization, "owner_email", None) if organization else None,
        },
        "client": {
            "name": client.name if client else placement.advertiser,
            "client_code": client.client_code if client else None,
            "email": client.email if client else None,
            "phone": client.phone if client else None,
        },
        "plan": {
            "name": plan.name,
            "description": plan.description,
            "duration_days": plan.duration_days,
            "max_locations": plan.max_locations,
            "ad_slots": plan.ad_slots,
            "support_tier": plan.support_tier,
            "price_paise": plan.price_paise,
        } if plan else None,
        "extensions": [
            {
                "id": e.id,
                "extended_from": e.extended_from,
                "extended_to": e.extended_to,
                "additional_price_paise": e.additional_price_paise,
                "is_paid": e.is_paid,
            }
            for e in placement.extensions
        ],
        "effective_ends_at": ends,
        "days_total": days_total,
        "days_remaining": days_remaining,
        "days_elapsed": days_elapsed,
        "extension_price_paise": sum(e.additional_price_paise for e in placement.extensions),
        "total_price_paise": money["total"],
        # The same three figures the dashboard reads, so the PDF a client is sent and the
        # page an operator is looking at cannot describe the same booking differently.
        "amount_paid_paise": money["received"],
        "balance_due_paise": money["balance"],
        "payment_status": money["status"],
        # For the invoice: its reference number is derived from these two ids, and the
        # payment block is what turns "unpaid" into "unpaid, and here is what we chased".
        # Both live here rather than at each exit so the two report shapes cannot diverge.
        "organization_id": placement.organization_id,
        # Every receipt, not a collapsed total. A client who paid a deposit and a balance
        # is entitled to see both on the invoice -- one line reading "amount received
        # 10,000" against two transfers they made months apart is not something they can
        # check against their own bank statement.
        "payments": [
            {
                "amount_paise": payment.amount_paise,
                "method": payment.method,
                "reference": payment.reference,
                "paid_at": payment.paid_at,
            }
            for payment in placement.payments
        ],
    }


def _screens_for_refs(scope: TenantScope, refs) -> set[int]:
    """Every screen a set of target refs actually reaches, groups expanded."""
    screen_ids: set[int] = set()
    group_ids = [ref.group_id for ref in refs if getattr(ref, "group_id", None)]
    screen_ids.update(ref.screen_id for ref in refs if getattr(ref, "screen_id", None))
    if group_ids:
        members = scope.query(models.Screen).filter(models.Screen.group_id.in_(group_ids)).all()
        screen_ids.update(member.id for member in members)
    return screen_ids


def location_cap(plan, placement=None) -> int:
    """How many screens this booking may cover. 0 means nothing caps it.

    Two sources, and only ever one of them in force. A package states the limit it sells and
    wins outright -- repricing or resizing the plan is the tenant's own business. Off a
    package, the booking carries its own figure, because "three screens for 40,000" is a
    real deal that had nowhere to be recorded: a custom sale was uncapped by definition, so
    the fourth screen went on from the booking page, the screen page or the playlist builder
    and the client received more than they bought with nothing able to see it.

    0 on both sides is the historical default and still means unlimited, so every booking
    sold before this behaves exactly as it did.
    """
    if plan is not None:
        return plan.max_locations if plan.max_locations > 0 else 0
    return max(0, getattr(placement, "max_locations", 0) or 0)


def plan_screen_usage(plan, screen_ids: set[int], placement=None) -> dict:
    """How much of a booking's screen allowance a set of screens uses. Measures; never raises.

    Separate from the check below because the two halves are not symmetrical. Going OVER a
    plan is a refusal -- the tenant is about to deliver something they did not sell. Going
    UNDER is not: a plan bought for five screens and running on three is a booking the
    client has paid for and is not receiving, which is worth saying loudly and is never
    worth blocking a sale over. Raising on it would make "sell now, pick the screens on
    Monday" impossible.

    `allowed` of 0 means nothing caps the booking -- no plan and no custom figure -- so
    nothing is over or unused.
    """
    allowed = location_cap(plan, placement)
    used = len(screen_ids)
    return {
        "used": used,
        "allowed": allowed,
        "over": max(0, used - allowed) if allowed else 0,
        "unused": max(0, allowed - used) if allowed else 0,
    }


def ensure_plan_locations(
    scope: TenantScope, plan, existing: set[int], adding: set[int], placement=None
) -> None:
    """A booking sells a number of TVs, so it has to actually cap them.

    Counted on EXPANDED screens, not on target rows: a single group target can carry ten
    screens, so counting targets would wave a ten-screen group straight past a five-TV
    plan -- the limit would read as enforced and mean nothing.

    Distinct from ensure_ad_slot_quota above, which is the limit MR Signage places on the
    TENANT. This is the limit the tenant placed on their own client, so the message has to
    make clear which of the two was hit.

    Every path that can change which screens a booking reaches has to come through here.
    Three of them did not: a plan swap on PUT, a group split, and the client-ad editor in
    routers/content.py, each of which could leave a booking delivering more screens than
    the plan it is billed on.
    """
    total = existing | adding
    usage = plan_screen_usage(plan, total, placement)
    if not usage["over"]:
        return
    allowed = usage["allowed"]
    plural = "" if allowed == 1 else "s"
    # Which of the two limits was hit, in the words that tell the operator what to do about
    # it. A package is changed; a custom cap is simply a number on this booking and is
    # raised where it was set.
    if plan is not None:
        detail = (
            f"The {plan.name} plan covers {allowed} screen{plural}, and this would make "
            f"{len(total)}. Move the booking to a larger plan, or remove a screen."
        )
    else:
        detail = (
            f"This booking was sold {allowed} screen{plural}, and this would make "
            f"{len(total)}. Raise the screen count under Change plan, or remove a screen."
        )
    raise HTTPException(status_code=409, detail=detail)


def build_booking_report(scope: TenantScope, placement: models.AdPlacement) -> dict:
    """Proof of delivery for one booking: only its window, only its screens.

    Scoping is the whole point. The per-advert report elsewhere totals every play of that
    creative for all time; handing that to a client would bill them for another client's
    booking of the same file, or for plays from before they paid.
    """
    content = scope.get(models.Content, placement.content_id)
    screen_ids = _booking_screen_ids(scope, placement)
    generated_at = models.utcnow()

    # Resolved, not raw: the column holds "s3://<key>" or "/uploads/<path>".
    # Fall back to file_url if thumbnail is unset so images always render.
    raw_thumb = (content.thumbnail or content.file_url) if content else None
    content_thumbnail = resolve_media_url(raw_thumb) if raw_thumb else None

    # On-screen length of one play: the real video length, or the flat 10s slot an image
    # occupies (the same fallback the playlist uses). Feeds "total airtime" on the report --
    # plays x this -- which is real exposure, not a guessed audience number.
    spot_seconds = (content.duration_ms / 1000) if (content and content.duration_ms) else 10.0

    import hashlib
    verify_payload = f"{placement.id}:{placement.organization_id}:{placement.advertiser}:{placement.starts_at.isoformat()}"
    token_hash = hashlib.sha256(verify_payload.encode()).hexdigest()[:8].upper()
    certificate_id = f"POP-{placement.id:04d}-{token_hash}"
    verification_url = f"https://olrac-signage.abhinavsanthosh221.workers.dev/verify/pop?cert={certificate_id}&id={placement.id}"

    empty = {"total_plays": 0, "completed_plays": 0, "error_plays": 0, "success_percent": 0.0}
    day = 86400
    ends = effective_ends_at(placement)
    commercials = _commercials(placement, generated_at)

    screen_map = {
        s.id: s for s in scope.db.query(models.Screen).filter(models.Screen.organization_id == placement.organization_id).all()
    }
    group_map = {
        g.id: g for g in scope.db.query(models.ScreenGroup).filter(models.ScreenGroup.organization_id == placement.organization_id).all()
    }

    target_info: dict[int, dict] = {}
    target_schedule = []
    for target in placement.targets:
        t_starts = target.effective_starts_at
        t_ends = target.effective_ends_at or ends
        t_days = max(1, round((t_ends - t_starts).total_seconds() / day)) if (t_ends and t_starts) else commercials["days_total"]

        if target.screen_id and target.screen_id in screen_map:
            sc = screen_map[target.screen_id]
            s_name = sc.name or f"Screen {sc.id}"
            s_loc = sc.location or "Location not set"
        elif target.group_id and target.group_id in group_map:
            gr = group_map[target.group_id]
            s_name = gr.name or f"Group {gr.id}"
            s_loc = "Venue Group"
        else:
            s_name = f"Target #{target.id}"
            s_loc = "-"

        target_schedule.append({
            "name": s_name,
            "location": s_loc,
            "kind": "screen" if target.screen_id else "group",
            "starts_at": t_starts,
            "ends_at": t_ends,
            "days": t_days,
        })

        info = {
            "assigned_at": target.assigned_at,
            "starts_at": t_starts,
            "ends_at": t_ends,
            "days": t_days,
        }
        if target.screen_id:
            target_info[target.screen_id] = info
        elif target.group_id:
            for member in scope.query(models.Screen).filter(models.Screen.group_id == target.group_id).all():
                target_info.setdefault(member.id, info)

    if not screen_ids:
        return {
            "placement_id": placement.id,
            "advertiser": placement.advertiser,
            "content_name": content.name if content else "Unknown",
            "content_id": placement.content_id,
            "content_type": content.type if content else "image",
            "content_file_url": content.file_url if content else None,
            "content_thumbnail": content_thumbnail,
            "certificate_id": certificate_id,
            "verification_url": verification_url,
            "target_schedule": target_schedule,
            **commercials,
            "starts_at": placement.starts_at,
            "ends_at": placement.ends_at,
            "price_paise": placement.price_paise,
            "is_paid": placement.is_paid,
            "generated_at": generated_at,
            "totals": empty,
            "spot_seconds": spot_seconds,
            "airtime_seconds": 0,
            "per_screen": [],
            "per_location": [],
            "daily": [],
            "stale_screens": [],
        }

    window = [
        models.PlayLogHourlyRollup.organization_id == placement.organization_id,
        models.PlayLogHourlyRollup.media_id == placement.content_id,
        models.PlayLogHourlyRollup.screen_id.in_(screen_ids),
        models.PlayLogHourlyRollup.date_hour >= placement.starts_at,
        # The EFFECTIVE end, not the sold one. Bounding on placement.ends_at dropped every
        # play bought through an extension -- the client paid for the extra fortnight and
        # the report showed none of it.
        models.PlayLogHourlyRollup.date_hour <= effective_ends_at(placement),
    ]

    totals_row = scope.db.query(
        func.coalesce(func.sum(models.PlayLogHourlyRollup.total_plays), 0),
        func.coalesce(func.sum(models.PlayLogHourlyRollup.completed_plays), 0),
        func.coalesce(func.sum(models.PlayLogHourlyRollup.error_plays), 0),
    ).filter(*window).one()
    total, completed, errors = totals_row
    totals = {
        "total_plays": total,
        "completed_plays": completed,
        "error_plays": errors,
        "success_percent": round(completed / total * 100, 1) if total else 0.0,
    }

    counts = dict(
        scope.db.query(
            models.PlayLogHourlyRollup.screen_id,
            func.coalesce(func.sum(models.PlayLogHourlyRollup.total_plays), 0),
        ).filter(*window).group_by(models.PlayLogHourlyRollup.screen_id).all()
    )
    completions = dict(
        scope.db.query(
            models.PlayLogHourlyRollup.screen_id,
            func.coalesce(func.sum(models.PlayLogHourlyRollup.completed_plays), 0),
        ).filter(*window).group_by(models.PlayLogHourlyRollup.screen_id).all()
    )

    screens = scope.query(models.Screen).filter(models.Screen.id.in_(screen_ids)).all()
    per_screen = []
    stale = []
    for screen in screens:
        t_meta = target_info.get(screen.id) or {
            "assigned_at": placement.starts_at,
            "starts_at": placement.starts_at,
            "ends_at": ends,
            "days": commercials["days_total"],
        }
        screen_starts = t_meta["starts_at"] or placement.starts_at
        screen_ends = t_meta["ends_at"] or ends
        screen_days = t_meta["days"]

        if screen_starts > generated_at:
            screen_status = "Scheduled"
        elif screen_ends < generated_at:
            screen_status = "Completed"
        else:
            screen_status = "Active"

        elapsed_screen_days = max(1, round((min(generated_at, screen_ends) - max(screen_starts, placement.starts_at)).total_seconds() / day))
        elapsed_screen_days = min(screen_days, elapsed_screen_days)

        cutoff = min(screen_ends, generated_at - REPORTING_GRACE)
        is_stale = screen.last_seen is None or screen.last_seen < cutoff
        row = {
            "screen_id": screen.id,
            "screen_name": screen.name or f"Screen {screen.id}",
            "location": screen.location,
            "assigned_at": t_meta["assigned_at"],
            "starts_at": screen_starts,
            "ends_at": screen_ends,
            "days": screen_days,
            "status": screen_status,
            "days_elapsed": elapsed_screen_days,
            "latitude": screen.latitude,
            "longitude": screen.longitude,
            "online": screen.status == "online",
            "last_seen": screen.last_seen,
            "total_plays": counts.get(screen.id, 0),
            "completed_plays": completions.get(screen.id, 0),
            "plays_per_day_avg": round(counts.get(screen.id, 0) / elapsed_screen_days, 1),
            "counts_may_be_incomplete": is_stale,
        }
        per_screen.append(row)
        if is_stale:
            stale.append(row["screen_name"])
    per_screen.sort(key=lambda r: r["total_plays"], reverse=True)

    places: dict[str, dict] = {}
    for row in per_screen:
        key = row["location"] or "Location not set"
        place = places.setdefault(key, {
            "location": key, "screens": 0, "total_plays": 0,
            "_earliest_assignment": row["assigned_at"],
        })
        place["screens"] += 1
        place["total_plays"] += row["total_plays"]
        place["_earliest_assignment"] = min(place["_earliest_assignment"], row["assigned_at"])

    # Over days ELAPSED, not days sold. Dividing a mid-campaign total by the full period
    # understates every row on the page, and a client reading "1,200/day" on a report they
    # were told covers 30 days will multiply it out and ask where the rest went.
    #
    # And elapsed FOR THAT LOCATION: a screen added on the tenth day of a campaign has had
    # ten fewer days to play, so dividing it by the campaign's elapsed days reports a late
    # addition as an underperformer. The earliest assignment in the location wins, since
    # that is when the location started carrying the advert at all.
    day = 86400
    for place in places.values():
        started = place.pop("_earliest_assignment")
        elapsed = (min(generated_at, ends) - max(started, placement.starts_at)).total_seconds() / day
        place["days_elapsed"] = max(1, min(commercials["days_elapsed"], round(elapsed)))
        place["plays_per_day_avg"] = round(place["total_plays"] / place["days_elapsed"], 1)

    # Daily trend. Grouped by the hour column (portable) and bucketed to calendar days in
    # Python -- date_trunc is Postgres-only and this path must also run on the SQLite the
    # test suite and local dev use, the same reason effective_ends_at avoids GREATEST. On
    # Postgres the previous date_trunc worked; on SQLite it raised "no such function" and
    # 500'd the whole report.
    from collections import defaultdict

    daily_totals: dict = defaultdict(int)
    per_hour = scope.db.query(
        models.PlayLogHourlyRollup.date_hour,
        func.coalesce(func.sum(models.PlayLogHourlyRollup.total_plays), 0),
    ).filter(*window).group_by(models.PlayLogHourlyRollup.date_hour).all()
    for date_hour, plays in per_hour:
        daily_totals[date_hour.date()] += plays

    return {
        "placement_id": placement.id,
        "advertiser": placement.advertiser,
        "content_name": content.name if content else "Unknown",
        "content_id": placement.content_id,
        "content_type": content.type if content else "image",
        "content_file_url": content.file_url if content else None,
        "content_thumbnail": content_thumbnail,
        "certificate_id": certificate_id,
        "verification_url": verification_url,
        "target_schedule": target_schedule,
        **commercials,
        "starts_at": placement.starts_at,
        "ends_at": placement.ends_at,
        "price_paise": placement.price_paise,
        "is_paid": placement.is_paid,
        "generated_at": generated_at,
        "totals": totals,
        "spot_seconds": spot_seconds,
        "airtime_seconds": int(round(totals["total_plays"] * spot_seconds)),
        "per_screen": per_screen,
        "per_location": sorted(places.values(), key=lambda p: p["total_plays"], reverse=True),
        # Reported, never enforced. A plan's "5 locations" is what was quoted, and real
        # sales add a sixth screen mid-campaign; refusing that would mean editing the plan
        # to sell one extra TV. But the client's report prints the plan's inclusions, so a
        # booking running past them has to be visible rather than a quiet contradiction
        # between the plan card and the table underneath it.
        "plan_locations_exceeded": bool(
            placement.plan and len(places) > placement.plan.max_locations
        ),
        "plan_max_locations": placement.plan.max_locations if placement.plan else None,
        "daily": [{"date": d.isoformat(), "total_plays": n} for d, n in sorted(daily_totals.items())],
        "stale_screens": stale,
    }


@router.get("/{placement_id}/report")
def get_booking_report(
    placement_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor", "viewer")),
):
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")
    return build_booking_report(scope, placement)


@router.get("/{placement_id}/report.pdf")
def download_booking_report(
    placement_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor", "viewer")),
    _feature: TenantScope = Depends(require_feature("pdf_reports")),
):
    """The client-facing PDF for one booking."""
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")

    pdf, filename = _render_report(scope, placement)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{placement_id}/invoice.pdf")
def download_invoice(
    placement_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor", "viewer")),
    _feature: TenantScope = Depends(require_feature("payslips")),
):
    """The commercial document: what was sold, what it cost, what was paid.

    Separate from report.pdf because they answer to different people. The report proves
    delivery and is safe to forward to anyone; this one restates the terms.
    """
    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")

    pdf, filename = _render_invoice(scope, placement)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _render_invoice(scope: TenantScope, placement: models.AdPlacement) -> tuple[bytes, str]:
    """The invoice PDF and the name to give it. Mirrors _render_report."""
    from ..reports.invoice import build_pdf, invoice_number

    report = build_booking_report(scope, placement)
    return build_pdf(report), f"{invoice_number(report)}.pdf"


def _render_report(scope: TenantScope, placement: models.AdPlacement) -> tuple[bytes, str]:
    """The PDF and the name to give it. Shared by the download and the email."""
    from ..reports.booking_report import build_pdf

    report = build_booking_report(scope, placement)
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in placement.advertiser).strip() or "client"
    return build_pdf(report), f"{safe} - playback report.pdf"


@router.get("/email-status")
def email_status(scope: TenantScope = Depends(get_tenant_scope)):
    # Signed in, like every other route here: it reports the server's sender address, which is
    # not for the public. It also called mailer.missing_configuration(), which does not exist,
    # so it answered 500 whenever mail was unset -- the very state it is asked about.
    from .. import mailer
    configured = mailer.is_configured()
    return {
        "is_configured": configured,
        "sender": mailer.sender() if configured else None,
        "missing_variables": [] if configured else mailer.missing_variables(),
    }


@router.post("/{placement_id}/email")
def email_booking_report(
    placement_id: int,
    document: str = "report",
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    """Send the client their own report, or their invoice.

    The address comes from the client record, never from the request: accepting a
    recipient here would turn an authenticated tenant endpoint into a way to mail an
    arbitrary attachment to an arbitrary address from this server's domain. `document`
    chooses which PDF rather than a second route existing, so that guard is written once.
    """
    from .. import mailer

    placement = scope.get(models.AdPlacement, placement_id)
    if not placement:
        raise HTTPException(status_code=404, detail="Booking not found")

    client = placement.client
    if not client or not client.email:
        raise HTTPException(
            status_code=422,
            detail="This booking has no client email address. Add one on the client record first.",
        )

    if document not in ("report", "invoice"):
        raise HTTPException(status_code=422, detail="document must be 'report' or 'invoice'")

    is_invoice = document == "invoice"
    # One route, two documents sold on two different flags -- the route-level dependency
    # used by the other PDF routes can only check one feature, so this checks the one the
    # actual request is for.
    ensure_feature(scope, "payslips" if is_invoice else "pdf_reports")
    pdf, filename = (_render_invoice if is_invoice else _render_report)(scope, placement)
    organization = scope.db.query(models.Organization).filter(
        models.Organization.id == placement.organization_id
    ).first()
    # Brand first, same fallback the PDF itself already uses -- the sign-off under the
    # tenant's own branded document used to still read "Regards, Default Organization".
    sender_name = (organization.brand_name or organization.name) if organization else "your signage partner"
    noun = "invoice" if is_invoice else "playback report"

    try:
        mailer.send(
            to=client.email,
            subject=f"{'Invoice' if is_invoice else 'Playback report'} - {placement.advertiser}",
            body=(
                f"Hello {client.name},\n\n"
                f"Attached is the {noun} for your campaign "
                f"\"{placement.advertiser}\", covering "
                f"{placement.starts_at:%d %b %Y} to {effective_ends_at(placement):%d %b %Y}.\n\n"
                f"Regards,\n{sender_name}"
            ),
            attachment=pdf,
            attachment_name=filename,
        )
    except mailer.MailNotConfigured as exc:
        # 503 rather than 500: nothing is wrong with the request, the server simply cannot
        # send mail yet. The message names the missing variables so it is actionable.
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 - SMTP failures are many and all mean the same here
        logger.exception("Failed to email report for placement %s", placement.id)
        raise HTTPException(status_code=502, detail=f"Could not send the email: {exc}")

    return {"status": "sent", "to": client.email}
