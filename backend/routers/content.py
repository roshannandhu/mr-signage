import hashlib
import logging
import os
import pathlib
import shutil
import subprocess
import uuid
from typing import Optional

import boto3
from fastapi import Query, APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import selectinload

logger = logging.getLogger(__name__)

from arq import create_pool
from .. import models, schemas
from ..tenancy import TenantScope, get_tenant_scope, require_tenant_roles, require_feature
from .. import media_storage
from ..media_urls import (
    is_s3_enabled,
    resolve_media_url,
    storage_prefix,
    client_ads_prefix,
    general_media_prefix,
    mint_media_filename,
)

router = APIRouter()


def queue_processing(db, content: models.Content) -> None:
    """Hand a video to the transcode worker, ensuring it is processed immediately.

    In standalone or single-host deployments where an external arq worker process may not
    be active, we launch process_media_sync in a daemon thread so video processing and
    renditions are generated immediately without hanging on 'processing'.
    """
    import threading
    from ..worker import process_media_sync

    threading.Thread(target=process_media_sync, args=(content.id,), daemon=True).start()

UPLOAD_DIR = os.path.join(pathlib.Path(__file__).parent.parent.parent.absolute(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# The extension came straight off the client filename and was appended to the stored
# name, with nothing checking it. /uploads is mounted as StaticFiles, so uploading
# "poster.html" -- or an SVG with a <script> in it, which is the easy one to miss --
# stored and then served attacker-controlled markup from the API's own origin. Signage
# only ever plays pictures and video, so the set of things worth accepting is short and
# an allowlist costs nothing.
ALLOWED_UPLOAD_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
    ".mp4", ".mov", ".webm", ".mkv", ".m4v",
}
from ..media_urls import get_s3_config


def get_s3_client():
    cfg = get_s3_config()
    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint_url"],
        aws_access_key_id=cfg["aws_access_key_id"],
        aws_secret_access_key=cfg["aws_secret_access_key"],
        region_name=cfg["region_name"],
    )


def public_upload_url(storage_key: str) -> str:
    """Where an asset lives, stored as a path rather than a full URL.

    This used to bake an absolute origin into the database at upload time, so every row
    pointed at whatever host uploaded it — in practice a stale http://localhost:8000.
    Thumbnails 404'd in the dashboard, and a TV could never download the media at all
    because "localhost" on a TV means the TV. The origin is now applied when the record is
    served, so the same row works from this machine, from a phone on the LAN, and from a
    public deployment.
    """
    return f"/uploads/{storage_key}"


def generate_video_thumbnail(video_path: str, stem: str, prefix: str) -> str | None:
    """Write a poster frame beside the video it came from.

    Takes the storage PREFIX rather than an organisation id: the caller builds the
    thumbnail's URL from the same prefix, and when this took an id it wrote the file to
    `uploads/19/` while the row pointed at `uploads/alice-example.com-19/`, so every video
    thumbnail 404'd.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    thumbnail_name = f"{stem}-thumbnail.jpg"
    thumbnail_path = os.path.join(UPLOAD_DIR, prefix, thumbnail_name)
    os.makedirs(os.path.dirname(thumbnail_path), exist_ok=True)
    try:
        subprocess.run(
            [ffmpeg, "-y", "-ss", "00:00:01", "-i", video_path, "-frames:v", "1", "-vf", "scale=640:-2", thumbnail_path],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        return thumbnail_path if os.path.exists(thumbnail_path) else None
    except (OSError, subprocess.SubprocessError):
        return None


# Everything serialize_content walks below Content, in one batched query each.
#
# Content.ad_placements is already lazy="selectin", but that only batches the placements
# themselves -- the client, the plan, the targets and each target's screen underneath them
# are all default-lazy, and serialize_content reads every one of them for every row. So the
# library listing cost 3 queries per ad-carrying asset plus one per target: a 100-advert
# account with three screens each was roughly 600 round trips, and at ~114 ms to the
# database's region that is over a minute for one page.
#
# This is the same defect the note on Content.playlist_items describes, one level deeper --
# it came back when the 1:1 ad/client enrichment was added to serialize_content. Requested
# at the call site rather than on the relationships because only the routes that actually
# serialise Content pay for it; /sync embeds ContentResponse without ever reading the
# placement block.
CONTENT_LOADERS = (
    selectinload(models.Content.playlist_items),
    selectinload(models.Content.ad_placements).selectinload(models.AdPlacement.client),
    selectinload(models.Content.ad_placements).selectinload(models.AdPlacement.plan),
    selectinload(models.Content.ad_placements)
    .selectinload(models.AdPlacement.targets)
    .selectinload(models.AdPlacementTarget.screen),
)


def serialize_content(content: models.Content) -> schemas.ContentResponse:
    # ContentResponse.absolutise_urls already resolved file_url and thumbnail; doing it
    # again here was a no-op that made it look like there were two places media URLs got
    # built.
    payload = schemas.ContentResponse.model_validate(content)

    # 1:1 Ad & Client enriched metadata
    placements = getattr(content, "ad_placements", [])
    if placements:
        latest = sorted(placements, key=lambda p: p.id, reverse=True)[0]
        payload.placement_id = latest.id
        payload.client_id = latest.client_id
        if latest.client:
            payload.client_name = latest.client.name
            payload.client_email = latest.client.email
            payload.client_phone = latest.client.phone
        elif latest.advertiser:
            payload.client_name = latest.advertiser

        if latest.plan:
            payload.plan_id = latest.plan_id
            payload.plan_name = latest.plan.name

        payload.placement_price_paise = latest.price_paise
        payload.placement_starts_at = latest.starts_at
        payload.placement_ends_at = latest.effective_ends_at
        # From the SOLD window, never the effective one -- see the field's note.
        payload.placement_duration_days = max(
            1, round((latest.ends_at - latest.starts_at).total_seconds() / 86400)
        )
        payload.placement_notes = latest.notes

        target_screens = []
        target_screen_names = []
        target_days: dict[int, int] = {}
        for t in getattr(latest, "targets", []):
            if t.screen_id:
                target_screens.append(t.screen_id)
                # Only locations sold their own length report one; the rest follow the
                # booking and are absent, which is what the modal reads to decide whether
                # to open its per-location panel at all.
                if t.starts_at and t.ends_at:
                    target_days[t.screen_id] = max(
                        1, round((t.ends_at - t.starts_at).total_seconds() / 86400)
                    )
                screen = getattr(t, "screen", None)
                if screen and screen.name:
                    target_screen_names.append(screen.name)
                else:
                    target_screen_names.append(f"Screen #{t.screen_id}")
        payload.screen_ids = target_screens
        payload.screen_names = target_screen_names
        payload.screen_days = target_days

    return payload


@router.post("/upload", response_model=schemas.ContentResponse, status_code=201)
def upload_content(
    file: UploadFile = File(...),
    name: str = Form(...),
    tags: Optional[str] = Form(None),
    client_id: Optional[int] = Form(None),
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    file.file.seek(0, os.SEEK_END)
    file_size_bytes = file.file.tell()
    
    file.file.seek(0)
    sha256_hash = hashlib.sha256()
    while chunk := file.file.read(8192):
        sha256_hash.update(chunk)
    sha256 = sha256_hash.hexdigest()
    
    file.file.seek(0)
    organization = scope.db.query(models.Organization).filter(
        models.Organization.id == scope.organization_id
    ).one()
    # Renditions count too. Summing only the source measured roughly a third of what a
    # video really occupies -- a transcode adds a full-size master and a smaller copy on
    # top -- so a 10GB quota let through nearer 25GB of objects and the bucket filled
    # while the dashboard still reported plenty of room.
    from ..services.storage_service import organization_storage_used

    used_bytes = organization_storage_used(scope.db, scope.organization_id)
    # 0 = unlimited, the same as ad slots and clients. Read literally it meant "no storage
    # at all", so an operator granting a tenant unlimited everything would have refused
    # every upload they made -- and nothing in the product ever wanted a zero-byte package.
    if organization.storage_quota_bytes and used_bytes + file_size_bytes > organization.storage_quota_bytes:
        remaining = max(organization.storage_quota_bytes - used_bytes, 0)
        raise HTTPException(
            status_code=413,
            detail=f"Storage quota exceeded. This upload is {file_size_bytes} bytes; {remaining} bytes remain. Upgrade your plan to continue.",
        )

    extension = os.path.splitext(file.filename or "")[1].lower()
    if extension not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type '{extension or file.filename}'. Allowed: "
                + ", ".join(sorted(ALLOWED_UPLOAD_EXTENSIONS))
            ),
        )

    client = None
    if client_id is not None:
        client = scope.get(models.Client, client_id)

    if client:
        folder = client_ads_prefix(organization, client)
    else:
        folder = general_media_prefix(organization)

    stem = str(uuid.uuid4())
    display_title = name.strip() if name else (file.filename or "media")
    unique_filename = mint_media_filename(display_title, extension, stem)
    thumb_stem = os.path.splitext(unique_filename)[0]
    storage_key = f"{folder}/{unique_filename}"
    content_type_header = file.content_type or "application/octet-stream"
    content_type = "video" if content_type_header.startswith("video") or extension in {".mp4", ".mov", ".webm"} else "image"
    thumbnail: str | None = None

    temp_local_file = os.path.join(UPLOAD_DIR, storage_key)
    os.makedirs(os.path.dirname(temp_local_file), exist_ok=True)
    file.file.seek(0)
    with open(temp_local_file, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    if is_s3_enabled():
        try:
            file_url = media_storage.store(pathlib.Path(temp_local_file), storage_key, content_type_header)
            if content_type == "image":
                thumbnail = file_url
            else:
                thumbnail_path = generate_video_thumbnail(temp_local_file, thumb_stem, folder)
                if thumbnail_path and os.path.exists(thumbnail_path):
                    thumb_key = f"{folder}/{os.path.basename(thumbnail_path)}"
                    thumbnail = media_storage.store(pathlib.Path(thumbnail_path), thumb_key, "image/jpeg")
                    try:
                        os.remove(thumbnail_path)
                    except Exception:
                        pass
                else:
                    thumbnail = file_url

            try:
                if os.path.exists(temp_local_file):
                    os.remove(temp_local_file)
            except Exception:
                pass
        except Exception as exc:
            try:
                if os.path.exists(temp_local_file):
                    os.remove(temp_local_file)
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=f"Upload failed: {exc}")
    else:
        file_url = public_upload_url(storage_key)
        if content_type == "image":
            thumbnail = file_url
        else:
            thumbnail_path = generate_video_thumbnail(temp_local_file, thumb_stem, folder)
            if thumbnail_path:
                thumbnail = public_upload_url(f"{folder}/{os.path.basename(thumbnail_path)}")
            else:
                thumbnail = file_url

    content = models.Content(
        organization_id=scope.organization_id,
        type=content_type,
        file_url=file_url,
        thumbnail=thumbnail,
        name=name.strip(),
        tags=tags.strip() if tags else None,
        file_size_bytes=file_size_bytes,
        sha256=sha256,
        status="processing" if content_type == "video" else "ready",
        processing_started_at=models.utcnow() if content_type == "video" else None,
    )
    scope.db.add(content)
    scope.db.commit()
    scope.db.refresh(content)
    
    if content.type == "video":
        queue_processing(scope.db, content)

    return serialize_content(content)


@router.post("/{content_id}/retry", response_model=schemas.ContentResponse)
def retry_content_processing(
    content_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    content = scope.get(models.Content, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    
    if content.type != "video":
        raise HTTPException(status_code=400, detail="Only video content can be retried")
    
    content.status = "processing"
    content.failed_reason = None
    content.processing_started_at = models.utcnow()
    scope.db.commit()
    scope.db.refresh(content)

    queue_processing(scope.db, content)

    return serialize_content(content)

@router.get("/", response_model=list[schemas.ContentResponse])
def get_all_content(
    search: str | None = None,
    tag: str | None = None,
    limit: int = Query(500, ge=1, le=2000),
    scope: TenantScope = Depends(get_tenant_scope),
):
    # expires_at walks playlist_items and the ad block walks the placement's client, plan
    # and targets. Requested here rather than on the relationships because eager-loading
    # playlist_items globally closes a cycle with PlaylistItem.content and slows the
    # playlist editor instead -- see the note on the model, and CONTENT_LOADERS above.
    query = scope.query(models.Content).options(*CONTENT_LOADERS)
    if search:
        query = query.filter(models.Content.name.ilike(f"%{search}%"))
    if tag:
        query = query.filter(models.Content.tags.ilike(f"%{tag}%"))
    # Capped rather than unbounded: this is the library listing, and without a ceiling one
    # request grows with the whole account's upload history forever.
    return [
        serialize_content(item)
        for item in query.order_by(models.Content.uploaded_at.desc()).limit(limit).all()
    ]


@router.put("/{content_id}", response_model=schemas.ContentResponse)
def update_content(
    content_id: int,
    payload: schemas.ContentUpdate,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    content = scope.get(models.Content, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    content.name = payload.name.strip()
    content.tags = payload.tags.strip() if payload.tags else None
    scope.db.commit()
    scope.db.refresh(content)
    return serialize_content(content)


@router.put("/{content_id}/client-ad", response_model=schemas.ContentResponse)
def update_content_client_ad(
    content_id: int,
    payload: schemas.ContentClientAdUpdate,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    content = scope.get(models.Content, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    
    if payload.name and payload.name.strip():
        content.name = payload.name.strip()

    # Stripped once, here, because the create and update branches below had drifted: one
    # stored the note as typed and the other trimmed it, so the same text round-tripped
    # differently depending on whether the booking already existed.
    notes = payload.notes.strip() if payload.notes else None

    # Find or create Client
    client_name = payload.client_name.strip()
    # Exact, case-insensitive. This was `ilike(client_name)`, which treats % and _ as
    # wildcards: a client named "%" matched whichever client sorted first.
    client = scope.db.query(models.Client).filter(
        models.Client.organization_id == scope.organization_id,
        func.lower(models.Client.name) == client_name.lower(),
    ).first()
    # Who this advert is booked to right now. Only that client's contact details are this
    # editor's to change; see the else branch below.
    current_client_id = scope.db.query(models.AdPlacement.client_id).filter(
        models.AdPlacement.organization_id == scope.organization_id,
        models.AdPlacement.content_id == content.id,
    ).order_by(models.AdPlacement.id.desc()).limit(1).scalar()

    # `notes` is deliberately NOT written to the client on either branch. The field it
    # comes from is labelled "Campaign Notes / Reference" in the editor and belongs to this
    # booking; writing it here as well meant a per-campaign reference typed against one
    # advert silently replaced the client's permanent note -- the one /dashboard/clients
    # shows -- and then leaked into every later booking through the editor's prefill.
    if not client:
        # The clients cap binds here too. This route creates a client implicitly from a
        # typed name, and it was the one door into the table that never asked -- so a
        # workspace at its limit could keep adding clients indefinitely by booking adverts
        # instead of using the clients page. Exactly the bypass the ad-slot guard below had
        # before it was added.
        from .clients import ensure_client_quota

        ensure_client_quota(scope)

        import re, random
        base_code = re.sub(r'[^A-Za-z0-9]', '', client_name.upper())[:6] or "CLNT"
        rand_suffix = f"{random.randint(100, 999)}"
        client = models.Client(
            organization_id=scope.organization_id,
            name=client_name,
            client_code=f"{base_code}{rand_suffix}",
            email=payload.client_email.strip() if payload.client_email else None,
            phone=payload.client_phone.strip() if payload.client_phone else None,
        )
        scope.db.add(client)
        scope.db.flush()
    elif client.id == current_client_id:
        # Correcting the contact details of the client this advert is already booked to.
        if payload.client_email is not None:
            client.email = payload.client_email.strip() if payload.client_email else None
        if payload.client_phone is not None:
            client.phone = payload.client_phone.strip() if payload.client_phone else None
    else:
        # Re-pointing the advert at a DIFFERENT, existing client. The editor pre-fills the
        # email and phone from the advert's previous client, so writing them here replaced
        # this client's real contact details with someone else's -- and their reports then
        # went to the wrong address. Fill a blank, never overwrite; the Clients page is where
        # an existing client's details are edited.
        if payload.client_email and not client.email:
            client.email = payload.client_email.strip()
        if payload.client_phone and not client.phone:
            client.phone = payload.client_phone.strip()

    # Find existing placement or create new.
    #
    # Everything about a booking below this point is delegated to routers/placements. This
    # used to be a second implementation of it, and every guard that surrounds _place and
    # _unplace everywhere else had been missed here: the ad-slot quota, the plan's location
    # cap counted properly, and above all the push of a changed window onto the playlist
    # item the player actually reads.
    from .placements import (
        _place,
        _screens_for_refs,
        _unplace,
        ensure_ad_slot_quota,
        ensure_plan_locations,
        reactivates,
        refresh_paid_state,
        sync_placement_window,
    )
    from datetime import timedelta
    placement = scope.db.query(models.AdPlacement).filter(
        models.AdPlacement.organization_id == scope.organization_id,
        models.AdPlacement.content_id == content.id
    ).order_by(models.AdPlacement.id.desc()).first()

    plan = scope.get(models.TenantPlan, payload.plan_id) if payload.plan_id else None

    if not placement:
        # The organisation's ad-slot limit binds on this path too. create_placement has
        # always checked it; selling through this editor instead was the way round it.
        ensure_ad_slot_quota(scope)
        now = models.utcnow()
        # The operator's length first, the plan's behind it, and 30 days only when there is
        # neither. A custom sale used to land on that 30 with no way to say otherwise.
        duration_days = payload.duration_days or (plan.duration_days if plan else 30)
        # The operator's figure first, the plan's as the default. A booking sold on no plan
        # used to be created at zero with no way to say otherwise, so every custom sale --
        # the client who wants a negotiated price rather than a package -- was recorded as
        # free and read as free on the ad's own page.
        price_paise = payload.price_paise if payload.price_paise is not None else (
            plan.price_paise if plan else 0
        )
        placement = models.AdPlacement(
            organization_id=scope.organization_id,
            content_id=content.id,
            client_id=client.id,
            advertiser=client.name,
            plan_id=plan.id if plan else None,
            price_paise=price_paise,
            starts_at=now,
            ends_at=now + timedelta(days=duration_days),
            notes=notes,
        )
        scope.db.add(placement)
        scope.db.flush()
    else:
        placement.client_id = client.id
        placement.advertiser = client.name
        # Tested with `in model_fields_set` rather than `if plan:` so that an explicit null
        # clears the plan. Testing the resolved object meant a booking put on the wrong
        # plan could be moved to another one but never taken off one.
        if "plan_id" in payload.model_fields_set:
            placement.plan_id = plan.id if plan else None
            # Price is NOT copied again here. It is copied once, when the booking is
            # created, and then it is the agreed figure -- routers/placements.py says so
            # where it first copies it, and test_tenant_plans pins that repricing a plan
            # never rebills a campaign already sold on it. Reassigning it on every edit
            # broke that through this door: renaming a client, or correcting a phone
            # number, silently rebilled the booking at today's list price.
        # An operator typing a figure is not that. It is the same edit PUT /placements/{id}
        # has always allowed, and it is the only way to correct a price -- or to set one at
        # all on a booking sold without a package. Null means "leave it", so the ordinary
        # edit still cannot touch it.
        if payload.price_paise is not None:
            placement.price_paise = payload.price_paise
            # What is outstanding is the total minus what was received, so correcting the
            # price here has to re-settle the booking. Without it, cutting the price to
            # what the client had already paid left them reading as part paid forever, and
            # raising it left a booking marked paid over money now owed.
            refresh_paid_state(placement)
        # Re-cut from the booking's own start, so correcting a run to 45 days means 45 days
        # of campaign and not 45 days from today. starts_at is the date it was SOLD on and
        # is left alone.
        if payload.duration_days is not None:
            new_ends_at = placement.starts_at + timedelta(days=payload.duration_days)
            # A finished booking given a future end date is running again and takes a slot.
            # Only new bookings were checked against the ad-slot limit, so this was a way
            # round it. Asked before the date is written: the count would otherwise include
            # this booking, already moved into the future.
            if reactivates(placement, new_ends_at):
                ensure_ad_slot_quota(scope)
            placement.ends_at = new_ends_at
        if payload.notes is not None:
            placement.notes = notes

    # Update assigned screens if provided
    if payload.screen_ids is not None:
        target_screen_ids = set(payload.screen_ids)
        # The plan's screen count binds on this path too, and `existing` has to carry the
        # screens reached through GROUP targets, which this edit leaves untouched. Passing
        # an empty set counted a ten-screen group as nothing and let the cap be walked past.
        kept_group_screens = _screens_for_refs(
            scope, [t for t in placement.targets if t.group_id]
        )
        # The plan this edit puts the booking ON, which for an explicit null is no plan at
        # all. Read off the payload rather than as `plan or placement.plan`: that spelling
        # gives the right answer only because placement.plan_id was set to None a few lines
        # up and SQLAlchemy resolves the relationship from the pending value. Touch .plan
        # anywhere above this and the fallback starts returning the plan being ABANDONED,
        # which would refuse a booking moving off a package using the cap of the package it
        # just left. Not worth leaving load order in charge of a quota.
        binding_plan = plan if "plan_id" in payload.model_fields_set else placement.plan
        # `placement` too, so a booking sold a custom screen count cannot be walked past its
        # cap from the client-ad editor -- the same reason binding_plan is worked out above.
        ensure_plan_locations(
            scope, binding_plan, kept_group_screens, target_screen_ids, placement
        )

        # Remove targets not in target_screen_ids
        for target in list(placement.targets):
            if target.screen_id and target.screen_id not in target_screen_ids:
                # _unplace already deletes the target row; a second delete here was
                # redundant.
                _unplace(scope, target)
        # Those deletes have to land before placement.targets is read again below, or the
        # re-window loop writes dates onto rows that are already on their way out.
        scope.db.flush()

        # Re-price the run length of places that are already on the booking. Handling only
        # new targets would mean an operator could add a location with 30 days but never
        # afterwards correct it to 10, and the modal would show a number the campaign was
        # not actually running to.
        for target in placement.targets:
            if not target.screen_id:
                continue
            days = (payload.screen_days or {}).get(target.screen_id)
            if days:
                # effective_starts_at is max(own-or-booking, assigned_at) -- the same rule
                # _place applies to a new target, read off the model so there is one copy
                # of it. Using assigned_at bare let a backdated booking start a screen
                # before it had been sold.
                base = target.effective_starts_at
                target.starts_at = base
                target.ends_at = base + timedelta(days=days)
            elif payload.clear_screen_days:
                # Explicitly cleared: this location goes back to following the booking.
                target.starts_at = None
                target.ends_at = None

        # Add new targets
        existing_screen_ids = {t.screen_id for t in placement.targets if t.screen_id}
        for s_id in target_screen_ids:
            if s_id not in existing_screen_ids:
                ref = schemas.PlacementTargetRef(
                    screen_id=s_id,
                    days=(payload.screen_days or {}).get(s_id),
                )
                _place(scope, placement, ref)

    # Target rows are the record of what was sold; PlaylistItem.start_at/end_at is what the
    # player enforces, and nothing copies one onto the other by itself -- so correcting a
    # location from 30 days to 10 updated the dashboard and the invoice while the TV kept
    # playing it for 30. This also bumps each affected playlist, without which sync answers
    # 204 and the screen never hears about the change at all.
    #
    # Outside the screens branch on purpose. While it sat inside `if screen_ids`, changing
    # only the run length moved the booking and left every TV playing to the old date. It
    # still runs after the targets are placed, so new items are synced in the same pass.
    if payload.screen_ids is not None or payload.duration_days is not None:
        sync_placement_window(scope, placement)

    scope.db.commit()
    scope.db.refresh(content)
    return serialize_content(content)


@router.delete("/{content_id}")
def delete_content(
    content_id: int,
    scope: TenantScope = Depends(require_tenant_roles("owner", "editor")),
):
    content = scope.get(models.Content, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    from .placements import content_deletion_blocker

    blocker = content_deletion_blocker(content)
    if blocker:
        raise HTTPException(status_code=409, detail=blocker)

    playlist_items = list(content.playlist_items)
    affected_playlists = {item.playlist for item in playlist_items}
    for playlist in affected_playlists:
        playlist.updated_at = models.utcnow()
    for item in playlist_items:
        scope.db.delete(item)

    # Renditions are the big one: a video becomes four transcoded files, and deleting the
    # content row cascaded the database rows away while leaving every file on disk. At
    # 500 MB an advert that stranded gigabytes per deletion.
    stored = [content.file_url, content.thumbnail]
    stored.extend(rendition.file_url for rendition in content.renditions)
    # Through media_storage, which handles both backends. delete_stored_file only
    # understands "/uploads/" paths and returns False for an "s3://" key without
    # touching anything -- so on object storage this loop deleted the database rows
    # and left all six objects (original, four renditions, thumbnail) in the bucket
    # for good. The quota they consumed was never released either.
    missed = [url for url in stored if url and not media_storage.delete(url)]

    if missed:
        # The row goes regardless: a bucket having a bad day must not make an advert
        # undeletable. But an object whose only pointer is about to be committed away is
        # the exact input scripts/cleanup_r2_orphans.py exists to sweep, and until now
        # nothing anywhere wrote down that it had happened.
        logger.warning(
            "Content %s deleted with %d object(s) left in storage: %s",
            content.id, len(missed), ", ".join(missed),
        )

    scope.db.delete(content)
    scope.db.commit()
    return {"status": "ok"}


@router.get("/{content_id}/report.pdf")
def download_content_report_pdf(
    content_id: int,
    scope: TenantScope = Depends(get_tenant_scope),
    _feature: TenantScope = Depends(require_feature("pdf_reports")),
):
    """Download a branded proof-of-play report PDF for an advert.

    If booked commercially, uses the booking's exact targets and window.
    If placed directly as media, synthesizes a playback performance audit
    with zero payment or invoice fields.
    """
    content = (
        scope.query(models.Content)
        .options(*CONTENT_LOADERS)
        .filter(models.Content.id == content_id)
        .first()
    )
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")

    from .placements import _render_report
    if content.ad_placements:
        latest_placement = sorted(content.ad_placements, key=lambda p: p.id, reverse=True)[0]
        pdf, filename = _render_report(scope, latest_placement)
        return Response(
            content=pdf,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    from ..reports.booking_report import build_pdf
    from ..worker import aggregate_play_logs_sync
    aggregate_play_logs_sync(scope.db)

    playlist_ids = {item.playlist_id for item in content.playlist_items}
    all_screens = (
        scope.query(models.Screen)
        .filter(models.Screen.organization_id == scope.organization_id)
        .all()
    )
    screens = [s for s in all_screens if s.effective_playlist_id in playlist_ids] if playlist_ids else []
    screen_ids = [s.id for s in screens]

    now = models.utcnow()
    raw_thumb = content.thumbnail or content.file_url
    content_thumbnail = resolve_media_url(raw_thumb) if raw_thumb else None
    spot_seconds = (content.duration_ms / 1000) if content.duration_ms else 10.0

    window = [
        models.PlayLogHourlyRollup.organization_id == scope.organization_id,
        models.PlayLogHourlyRollup.media_id == content.id,
    ]
    if screen_ids:
        window.append(models.PlayLogHourlyRollup.screen_id.in_(screen_ids))

    total, completed, errors, offline = scope.db.query(
        func.coalesce(func.sum(models.PlayLogHourlyRollup.total_plays), 0),
        func.coalesce(func.sum(models.PlayLogHourlyRollup.completed_plays), 0),
        func.coalesce(func.sum(models.PlayLogHourlyRollup.error_plays), 0),
        func.coalesce(func.sum(models.PlayLogHourlyRollup.offline_plays), 0),
    ).filter(*window).one()

    totals = {
        "total_plays": total,
        "completed_plays": completed,
        "error_plays": errors,
        "success_percent": round(completed / total * 100, 1) if total else 0.0,
        "online_plays": max(0, total - offline),
        "offline_plays": offline,
    }

    counts = dict(
        scope.db.query(
            models.PlayLogHourlyRollup.screen_id,
            func.coalesce(func.sum(models.PlayLogHourlyRollup.total_plays), 0),
        ).filter(*window).group_by(models.PlayLogHourlyRollup.screen_id).all()
    )

    days_elapsed = max(1, (now - content.uploaded_at).days)
    per_screen = []
    for screen in screens:
        per_screen.append({
            "screen_id": screen.id,
            "screen_name": screen.name or f"Screen {screen.id}",
            "location": screen.location,
            "assigned_at": content.uploaded_at,
            "starts_at": content.uploaded_at,
            "ends_at": now,
            "days": days_elapsed,
            "status": "Active",
            "days_elapsed": days_elapsed,
            "latitude": screen.latitude,
            "longitude": screen.longitude,
            "online": screen.status == "online",
            "last_seen": screen.last_seen,
            "total_plays": counts.get(screen.id, 0),
            "completed_plays": counts.get(screen.id, 0),
            "plays_per_day_avg": round(counts.get(screen.id, 0) / days_elapsed, 1),
            "counts_may_be_incomplete": False,
        })
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

    per_location = []
    for key, place in places.items():
        per_location.append({
            "location": place["location"],
            "screens": place["screens"],
            "total_plays": place["total_plays"],
            "avg_plays_per_screen": round(place["total_plays"] / max(1, place["screens"])),
            "plays_per_day_avg": round(place["total_plays"] / days_elapsed, 1),
        })

    import hashlib
    verify_payload = f"content:{content.id}:{scope.organization_id}:{content.uploaded_at.isoformat()}"
    token_hash = hashlib.sha256(verify_payload.encode()).hexdigest()[:8].upper()
    certificate_id = f"POP-C{content.id:04d}-{token_hash}"
    verification_url = f"https://olrac-signage.abhinavsanthosh221.workers.dev/verify/pop?cert={certificate_id}&id={content.id}"

    daily_rows = (
        scope.db.query(
            models.PlayLogHourlyRollup.date_hour,
            func.coalesce(func.sum(models.PlayLogHourlyRollup.total_plays), 0),
            func.coalesce(func.sum(models.PlayLogHourlyRollup.completed_plays), 0),
        )
        .filter(*window)
        .group_by(models.PlayLogHourlyRollup.date_hour)
        .all()
    )
    from .analytics import _bucket_daily
    daily = [
        {"date": day.isoformat(), "total_plays": t, "completed_plays": c}
        for day, t, c in _bucket_daily(daily_rows)
    ]

    report = {
        "placement_id": None,
        "advertiser": content.name,
        "content_name": content.name,
        "content_id": content.id,
        "content_type": content.type,
        "content_file_url": content.file_url,
        "content_thumbnail": content_thumbnail,
        "certificate_id": certificate_id,
        "verification_url": verification_url,
        "target_schedule": [],
        "organization": {
            "name": ((scope.organization.brand_name or scope.organization.name) if scope.organization else "Signage network"),
            "logo": resolve_media_url(scope.organization.logo_url) if scope.organization and scope.organization.logo_url else None,
            "brand_color": scope.organization.brand_color if scope.organization else None,
            "email": getattr(scope.organization, "owner_email", None) if scope.organization else None,
        },
        "starts_at": content.uploaded_at,
        "ends_at": now,
        "effective_ends_at": now,
        "days_total": days_elapsed,
        "days_elapsed": days_elapsed,
        "days_remaining": 0,
        "screens_used": len(screens),
        "plan": None,
        "plan_max_locations": len(screens),
        "plan_locations_exceeded": False,
        "extensions": [],
        "generated_at": now,
        "totals": totals,
        "spot_seconds": spot_seconds,
        "airtime_seconds": round(totals["total_plays"] * spot_seconds),
        "per_screen": per_screen,
        "per_location": per_location,
        "daily": daily,
        "stale_screens": [],
    }

    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in content.name).strip() or "media"
    pdf = build_pdf(report)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe} - playback report.pdf"'},
    )


# Declared last on purpose: FastAPI matches routes in declaration order, so a bare
# "/{content_id}" placed above "/{content_id}/retry" or "/{content_id}/client-ad" would
# swallow them.
@router.get("/{content_id}", response_model=schemas.ContentResponse)
def get_content_item(
    content_id: int,
    scope: TenantScope = Depends(get_tenant_scope),
):
    """One asset, for the pages that show a single advert.

    The detail page used to fetch the whole library and pick its row out in the browser,
    which meant an asset older than the listing's 500-row cap simply rendered "not found"
    -- and every link to one from Campaigns or Invoices dead-ended. It also paid for
    serialising 499 rows nobody was going to look at.
    """
    content = (
        scope.query(models.Content)
        .options(*CONTENT_LOADERS)
        .filter(models.Content.id == content_id)
        .first()
    )
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    return serialize_content(content)
