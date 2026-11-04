"""Destroying a workspace that an admin removed, once its 30 days are up.

Removal is a mark, not a delete (`Organization.deleted_at`), and this is the other half:
the job that makes it permanent. The gap between the two is the entire point -- an admin
who removes the wrong workspace has a month to put it back, and after that nothing of it
survives, neither rows nor bytes.

Three things make this harder than a cascade would suggest, and all three are why the
order below is written out by hand rather than left to `db.delete(org)`:

* Twelve of the eighteen tables carrying `organization_id` have a plain foreign key with
  no ON DELETE rule, so deleting the parent first simply fails.
* Five more tables belong to a workspace only transitively -- renditions through content,
  schedules through playlist items, targets and extensions through placements -- and carry
  no `organization_id` to filter on at all.
* SQLite does not enforce the ON DELETE CASCADE the rest rely on (`backend/database.py`
  sets no `PRAGMA foreign_keys=ON`), so anything leaning on the database to cascade would
  pass every test here and behave differently in production.

Objects go before rows, for the reason `prune_screenshots` already gives: the row is the
only pointer to the object, and dropping it first turns the object into an orphan nothing
can ever find again.
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import media_storage, models
from ..media_urls import is_s3_enabled, storage_prefix, tenant_storage_root
from .storage_service import delete_prefix, forget_cached_usage

logger = logging.getLogger(__name__)

# The window an admin has to undo a removal. Configurable mostly so a test does not have to
# wait a month, but a deployment with a different retention policy can set it too.
TENANT_PURGE_AFTER_DAYS = int(os.getenv("TENANT_PURGE_AFTER_DAYS", "30"))


def purge_due_at(organization: models.Organization):
    """When this workspace stops being recoverable, or None if it was never removed."""
    if organization.deleted_at is None:
        return None
    return organization.deleted_at + timedelta(days=TENANT_PURGE_AFTER_DAYS)


def dry_run_enabled() -> bool:
    """Report what would be destroyed and destroy nothing.

    Meant for the first production cycle, so the ordering below can be proved against real
    data before anything irreversible happens to it.
    """
    return os.getenv("TENANT_PURGE_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}


def _stored_urls(db: Session, org_id: int) -> list[str]:
    """Every object this workspace owns, according to the rows about to be deleted.

    The rows are authoritative and the prefix sweep afterwards is only a backstop, because
    a key derived from the workspace NAME stops matching the moment the workspace is
    renamed -- while the row still points at exactly the right object.
    """
    urls: list[str] = []
    for file_url, thumbnail in db.query(
        models.Content.file_url, models.Content.thumbnail
    ).filter(models.Content.organization_id == org_id):
        urls.extend(url for url in (file_url, thumbnail) if url)

    content_ids = select(models.Content.id).where(models.Content.organization_id == org_id)
    urls.extend(
        url
        for (url,) in db.query(models.MediaRendition.file_url).filter(
            models.MediaRendition.content_id.in_(content_ids)
        )
        if url
    )

    logo = db.query(models.Organization.logo_url).filter(
        models.Organization.id == org_id
    ).scalar()
    if logo:
        urls.append(logo)

    # For an image the thumbnail IS the original, so the same key arrives twice. Deleting
    # it twice is harmless but logs a second miss for an object that already went.
    return list(dict.fromkeys(urls))


def _deletion_plan(org_id: int):
    """(model, filter) pairs, children before whatever they point at."""
    content_ids = select(models.Content.id).where(models.Content.organization_id == org_id)
    playlist_ids = select(models.Playlist.id).where(models.Playlist.organization_id == org_id)
    placement_ids = select(models.AdPlacement.id).where(
        models.AdPlacement.organization_id == org_id
    )
    owns_item = or_(
        models.PlaylistItem.playlist_id.in_(playlist_ids),
        models.PlaylistItem.content_id.in_(content_ids),
    )
    item_ids = select(models.PlaylistItem.id).where(owns_item)

    return [
        (models.PlayLogHourlyRollup, models.PlayLogHourlyRollup.organization_id == org_id),
        (models.PlayLog, models.PlayLog.organization_id == org_id),
        (models.Alert, models.Alert.organization_id == org_id),
        (models.Schedule, models.Schedule.playlist_item_id.in_(item_ids)),
        (models.AdPlacementTarget, models.AdPlacementTarget.placement_id.in_(placement_ids)),
        (models.AdPlacementExtension, models.AdPlacementExtension.placement_id.in_(placement_ids)),
        (models.AdPayment, models.AdPayment.organization_id == org_id),
        (models.AdPlacement, models.AdPlacement.organization_id == org_id),
        (models.EmergencyBroadcast, models.EmergencyBroadcast.organization_id == org_id),
        (models.PlaylistItem, owns_item),
        # Before playlists and groups: a screen points at both.
        (models.Screen, models.Screen.organization_id == org_id),
        (models.ScreenGroup, models.ScreenGroup.organization_id == org_id),
        (models.Playlist, models.Playlist.organization_id == org_id),
        (models.MediaRendition, models.MediaRendition.content_id.in_(content_ids)),
        (models.Content, models.Content.organization_id == org_id),
        (models.Campaign, models.Campaign.organization_id == org_id),
        (models.Client, models.Client.organization_id == org_id),
        (models.TenantPlan, models.TenantPlan.organization_id == org_id),
        (models.EnrollmentToken, models.EnrollmentToken.organization_id == org_id),
        (models.PlatformPayment, models.PlatformPayment.organization_id == org_id),
        (models.DeviceCommandLog, models.DeviceCommandLog.organization_id == org_id),
        (models.TenantNote, models.TenantNote.organization_id == org_id),
        (models.KioskUnlockEvent, models.KioskUnlockEvent.organization_id == org_id),
        (models.AttentionDismissal, models.AttentionDismissal.organization_id == org_id),
        (models.CustomPlanRequest, models.CustomPlanRequest.organization_id == org_id),
        (models.Subscription, models.Subscription.organization_id == org_id),
        (models.User, models.User.organization_id == org_id),
    ]


def purge_organization(
    db: Session, organization: models.Organization, dry_run: bool = False,
    immediately: bool = False,
) -> dict:
    """Destroy one workspace completely. The caller owns the transaction boundary.

    Refuses anything not removed first, so a mistaken call site cannot turn this into a
    delete-any-tenant function. Refuses anything not yet due unless `immediately` -- the
    admin's "Delete permanently", which skips the rest of the 30 days on purpose.
    """
    due = purge_due_at(organization)
    if due is None:
        raise ValueError(f"Organization {organization.id} was never removed")
    if due > models.utcnow() and not immediately:
        raise ValueError(f"Organization {organization.id} is not due until {due.isoformat()}")

    org_id = organization.id
    prefixes = list(dict.fromkeys([tenant_storage_root(organization), storage_prefix(organization)]))
    report = {
        "organization_id": org_id,
        "name": organization.name,
        "rows": {},
        "objects_deleted": 0,
        "objects_missed": 0,
        "prefixes": prefixes,
        "dry_run": dry_run,
    }

    urls = _stored_urls(db, org_id)

    if dry_run:
        report["objects_deleted"] = len(urls)
        for model, condition in _deletion_plan(org_id):
            count = db.query(model).filter(condition).count()
            if count:
                report["rows"][model.__tablename__] = count
        report["rows"]["organizations"] = 1
        logger.info("[dry run] would purge %s", report)
        return report

    for url in urls:
        if media_storage.delete(url):
            report["objects_deleted"] += 1
        else:
            report["objects_missed"] += 1

    for model, condition in _deletion_plan(org_id):
        removed = db.query(model).filter(condition).delete(synchronize_session=False)
        if removed:
            report["rows"][model.__tablename__] = removed

    db.query(models.Organization).filter(models.Organization.id == org_id).delete(synchronize_session=False)
    db.expire_all()
    db.flush()
    report["rows"]["organizations"] = 1

    # The backstop, once the rows are gone: whatever is still filed under this workspace
    # was never in the database to be found by the loop above.
    for prefix in prefixes:
        swept = delete_prefix(prefix)
        report["objects_deleted"] += swept["deleted"]
        report["objects_missed"] += swept["failed"]
        if swept.get("error"):
            report["storage_error"] = swept["error"]
            logger.warning("Prefix sweep for %s had storage error: %s", prefix, swept["error"])
        # Objects before rows, honoured to the end: the rows above are only flushed, not
        # committed, so raising here rolls them back and the workspace is retried on the
        # next pass. Committing them with bytes still in the bucket would orphan those bytes
        # for good -- nothing would remain that points at them. With no storage configured
        # there is no bucket to sweep, which is not a failure.
        #
        # When `immediately=True` (an explicit permanent delete by an administrator), do not
        # block deleting database records if object storage is down, credentials are expired/unauthorized,
        # or the bucket returns an error. Log the warning and allow the permanent delete to finish.
        if (swept.get("error") or swept["failed"]) and is_s3_enabled() and not immediately:
            raise RuntimeError(
                f"Storage sweep of {prefix} did not finish for workspace {org_id} "
                f"({swept['failed']} objects refused, error: {swept.get('error')}); "
                "keeping its rows so the purge can be retried."
            )

    return report


def purge_removed_tenants(db: Session) -> list[dict]:
    """Every workspace whose 30 days are up.

    One transaction per workspace, for the reason `expire_due_subscriptions` gives: a single
    unwritable row must not roll back the purges already completed in the same pass.
    """
    dry_run = dry_run_enabled()
    cutoff = models.utcnow() - timedelta(days=TENANT_PURGE_AFTER_DAYS)
    due = db.query(models.Organization).filter(
        models.Organization.deleted_at.isnot(None),
        models.Organization.deleted_at <= cutoff,
    ).all()

    reports = []
    for organization in due:
        name, org_id = organization.name, organization.id
        try:
            report = purge_organization(db, organization, dry_run=dry_run)
            if dry_run:
                db.rollback()
            else:
                db.commit()
            reports.append(report)
            logger.info(
                "Purged workspace %s (%s): %s rows, %s objects%s",
                name,
                org_id,
                sum(report["rows"].values()),
                report["objects_deleted"],
                " [dry run]" if dry_run else "",
            )
        except Exception:
            db.rollback()
            logger.exception("Could not purge workspace %s (%s)", name, org_id)

    if reports and not dry_run:
        forget_cached_usage()
    return reports
