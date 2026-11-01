"""Removing a workspace, and destroying it 30 days later.

The console could block a tenant and end its plan; it could not remove one. So a workspace
that had left kept its screens, its adverts and its bytes for ever, and the only way to be
rid of it was a DELETE that fails on the first of twelve unguarded foreign keys.

What is actually worth testing here is the ordering. `db.delete(org)` cannot do this job,
and the hand-written order in `_deletion_plan` is only correct until someone adds a table --
so the test that matters asserts nothing is LEFT, table by table, rather than that the call
returned. It also pins the two refusals, because a purge that runs early or runs on a
workspace nobody removed is the one bug in here that cannot be undone.

Storage is mocked out: what belongs to this file is which rows go and in what order. The
object side is `media_storage.delete`, covered in test_media_deletion.py.
"""
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-purge-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-tenant-purge")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from backend import database, models  # noqa: E402
import backend.main  # noqa: E402,F401  (import order; see test_subscription_expiry)
from backend.services import tenant_purge  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)


def _workspace(db, *, name="Doomed Co"):
    """A workspace with something in every table the purge has to reach."""
    org = models.Organization(
        name=name, slug=f"o-{uuid.uuid4().hex[:8]}", status="active",
        storage_quota_bytes=10 * 1024 * 1024,
    )
    db.add(org)
    db.flush()

    user = models.User(
        username=f"u-{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x", role="owner", organization_id=org.id,
    )
    content = models.Content(
        organization_id=org.id, type="video", name="Ad",
        file_url=f"s3://tenants/Doomed-{org.id}/ad.mp4",
        thumbnail=f"s3://tenants/Doomed-{org.id}/ad.jpg",
        file_size_bytes=1024, status="ready",
    )
    playlist = models.Playlist(organization_id=org.id, name="Loop")
    screen = models.Screen(
        organization_id=org.id, name="Lobby", device_id=f"d-{uuid.uuid4().hex[:8]}",
        status="online",
    )
    group = models.ScreenGroup(organization_id=org.id, name="Ground floor")
    client = models.Client(
        organization_id=org.id, name="Advertiser", client_code=f"c-{uuid.uuid4().hex[:8]}",
    )
    db.add_all([user, content, playlist, screen, group, client])
    db.flush()

    item = models.PlaylistItem(playlist_id=playlist.id, content_id=content.id, order=0)
    rendition = models.MediaRendition(
        content_id=content.id, file_url=f"s3://tenants/Doomed-{org.id}/ad_720.mp4",
        file_size_bytes=512, resolution="720p", width=1280, height=720,
    )
    shot = models.ScreenshotLog(
        organization_id=org.id, screen_id=screen.id,
        file_url=f"s3://tenants/Doomed-{org.id}/screens/{screen.id}/shot.jpg",
    )
    db.add_all([item, rendition, shot])
    db.flush()

    placement = models.AdPlacement(
        organization_id=org.id, content_id=content.id, client_id=client.id,
        advertiser="Advertiser",
        starts_at=models.utcnow(), ends_at=models.utcnow() + timedelta(days=7),
    )
    db.add(placement)
    db.flush()
    db.add(models.AdPlacementTarget(placement_id=placement.id, screen_id=screen.id))
    now = models.utcnow()
    db.add(models.PlayLog(
        event_id=uuid.uuid4().hex, organization_id=org.id, screen_id=screen.id,
        media_id=content.id,
        device_started_at=now, device_finished_at=now,
        corrected_started_at=now, corrected_finished_at=now,
        duration_ms=15000, status="completed",
    ))
    db.add(models.PlayLogHourlyRollup(
        organization_id=org.id, screen_id=screen.id, media_id=content.id,
        date_hour=now.replace(minute=0, second=0, microsecond=0),
        total_plays=1, completed_plays=1, partial_plays=0, error_plays=0,
        duration_ms=15000,
    ))
    db.add(models.Alert(
        organization_id=org.id, kind="screen_offline", severity="warning",
        screen_id=screen.id, title="Lobby went offline",
        dedupe_key=f"offline-{screen.id}",
    ))
    db.commit()
    return org


def _deleted_nothing(*_args, **_kwargs):
    """Storage stubbed out. Row ordering is what this file is about."""
    return True


def _survivors(db, org_id):
    """Every row still pointing at this workspace, table by table."""
    left = {}
    for model, condition in tenant_purge._deletion_plan(org_id):
        count = db.query(model).filter(condition).count()
        if count:
            left[model.__tablename__] = count
    orgs = db.query(models.Organization).filter(models.Organization.id == org_id).count()
    if orgs:
        left["organizations"] = orgs
    return left


def test_a_due_workspace_leaves_nothing_behind(monkeypatch=None):
    """The one that matters: every table, not just the ones a cascade would have caught."""
    from backend import media_storage
    from backend.services import tenant_purge as purge_module

    original_delete = media_storage.delete
    original_sweep = purge_module.delete_prefix
    media_storage.delete = _deleted_nothing
    purge_module.delete_prefix = lambda prefix: {"deleted": 0, "failed": 0, "error": None}

    db = database.SessionLocal()
    try:
        org = _workspace(db)
        org_id = org.id
        assert _survivors(db, org_id), "fixture built nothing to purge"

        org.deleted_at = models.utcnow() - timedelta(
            days=tenant_purge.TENANT_PURGE_AFTER_DAYS + 1
        )
        db.commit()

        tenant_purge.purge_removed_tenants(db)
        assert _survivors(db, org_id) == {}, "rows survived the purge"
    finally:
        media_storage.delete = original_delete
        purge_module.delete_prefix = original_sweep
        db.close()


def test_a_workspace_inside_its_window_is_left_alone():
    """The 30 days are the whole feature. A purge on day 29 is data loss, not tidiness."""
    db = database.SessionLocal()
    try:
        org = _workspace(db, name="Recent Co")
        org.deleted_at = models.utcnow() - timedelta(days=1)
        db.commit()
        org_id = org.id

        tenant_purge.purge_removed_tenants(db)

        assert db.query(models.Organization).filter(
            models.Organization.id == org_id
        ).count() == 1
        assert _survivors(db, org_id), "a workspace still inside its window was emptied"
    finally:
        db.close()


def test_purging_a_workspace_nobody_removed_is_refused():
    """A mistaken call site must not be able to use this as delete-any-tenant."""
    db = database.SessionLocal()
    try:
        org = _workspace(db, name="Live Co")
        assert org.deleted_at is None
        try:
            tenant_purge.purge_organization(db, org)
        except ValueError as exc:
            assert "never removed" in str(exc)
        else:
            raise AssertionError("purged a workspace that was never removed")
    finally:
        db.rollback()
        db.close()


def test_purging_early_is_refused_even_when_asked_directly():
    """purge_removed_tenants filters by date; purge_organization must not trust that."""
    db = database.SessionLocal()
    try:
        org = _workspace(db, name="Early Co")
        org.deleted_at = models.utcnow() - timedelta(days=2)
        db.commit()
        try:
            tenant_purge.purge_organization(db, org)
        except ValueError as exc:
            assert "not due until" in str(exc)
        else:
            raise AssertionError("purged a workspace before its window closed")
    finally:
        db.rollback()
        db.close()


def test_removal_is_reported_as_a_status_every_gate_already_refuses():
    """Access has to stop at removal, not at purge -- 30 days later is 30 days too late."""
    db = database.SessionLocal()
    try:
        org = _workspace(db, name="Blocked Co")
        user = db.query(models.User).filter(models.User.organization_id == org.id).first()
        assert user.organization_status == "active"

        org.deleted_at = models.utcnow()
        db.commit()
        db.refresh(user)

        assert user.organization_status == "removed"

        from backend.tenancy import BLOCKED_ORGANIZATION_STATUSES

        assert "removed" in BLOCKED_ORGANIZATION_STATUSES
    finally:
        db.close()


def test_the_purge_date_is_thirty_days_after_removal():
    """What the console prints to the operator about to type a workspace name."""
    org = models.Organization(name="X", slug="x", status="active")
    assert tenant_purge.purge_due_at(org) is None

    org.deleted_at = models.utcnow()
    due = tenant_purge.purge_due_at(org)
    assert (due - org.deleted_at).days == tenant_purge.TENANT_PURGE_AFTER_DAYS


def test_a_sweep_refuses_to_match_the_whole_bucket():
    """An empty prefix is every object of every tenant. It must never mean 'all'."""
    from backend.services.storage_service import delete_prefix

    for prefix in ("", "   ", "/"):
        try:
            delete_prefix(prefix)
        except ValueError:
            continue
        raise AssertionError(f"delete_prefix({prefix!r}) was not refused")


def test_admin_can_delete_a_removed_workspace_now():
    """"Delete permanently" skips the 30 days, but only for a removed workspace whose name was typed."""
    from types import SimpleNamespace

    from fastapi import HTTPException

    from backend import media_storage
    from backend.routers import admin
    from backend.services import tenant_purge as purge_module

    original_delete, original_sweep = media_storage.delete, purge_module.delete_prefix
    media_storage.delete = _deleted_nothing
    purge_module.delete_prefix = lambda prefix: {"deleted": 0, "failed": 0, "error": None}
    # id and acting_organization_id: the delete writes an audit row naming its actor.
    scope = SimpleNamespace(user=SimpleNamespace(id=1, organization_id=None, username="root"),
                            acting_organization_id=None)

    db = database.SessionLocal()
    try:
        org = _workspace(db, name="Gone Today Co")
        db.commit()
        org_id = org.id

        def refused(name, status):
            try:
                admin.delete_tenant_permanently(org_id, name, request=None, scope=scope, db=db)
            except HTTPException as exc:
                assert exc.status_code == status, exc.detail
            else:
                raise AssertionError(f"deleted with name={name!r}")

        refused("Gone Today Co", 409)  # not removed yet
        org.deleted_at = models.utcnow() - timedelta(days=1)
        db.commit()
        refused("Gone Today", 400)  # wrong name
        assert _survivors(db, org_id), "a refused delete removed rows"

        report = admin.delete_tenant_permanently(org_id, "Gone Today Co", request=None, scope=scope, db=db)
        assert report["rows"]["organizations"] == 1
        assert _survivors(db, org_id) == {}, "rows survived the permanent delete"
        assert db.query(models.Organization).filter(models.Organization.id == org_id).count() == 0
    finally:
        media_storage.delete, purge_module.delete_prefix = original_delete, original_sweep
        db.close()


def _due_workspace(db, name):
    org = _workspace(db, name=name)
    org.deleted_at = models.utcnow() - timedelta(days=tenant_purge.TENANT_PURGE_AFTER_DAYS + 1)
    db.commit()
    return org.id


def test_an_unfinished_storage_sweep_keeps_the_rows():
    """Rows go only once the bucket is clean. Deleting them first orphans the bytes forever,
    because nothing would remain that points at them."""
    from backend import media_storage

    original_delete, original_sweep = media_storage.delete, tenant_purge.delete_prefix
    original_enabled = tenant_purge.is_s3_enabled
    media_storage.delete = _deleted_nothing
    tenant_purge.delete_prefix = lambda prefix: {"deleted": 0, "failed": 2, "error": "AccessDenied"}
    tenant_purge.is_s3_enabled = lambda: True

    db = database.SessionLocal()
    try:
        org_id = _due_workspace(db, "Refused Co")
        tenant_purge.purge_removed_tenants(db)  # logs the failure and rolls back
        assert _survivors(db, org_id), "rows were deleted while the bucket still held their files"

        # The same workspace is purged on the next pass once the bucket cooperates.
        tenant_purge.delete_prefix = lambda prefix: {"deleted": 2, "failed": 0, "error": None}
        tenant_purge.purge_removed_tenants(db)
        assert _survivors(db, org_id) == {}, "the retry did not finish the purge"
    finally:
        media_storage.delete, tenant_purge.delete_prefix = original_delete, original_sweep
        tenant_purge.is_s3_enabled = original_enabled
        db.close()


def test_a_sweep_with_no_storage_configured_does_not_block_the_purge():
    """No bucket means nothing to sweep -- local-disk and database-mirror deployments."""
    from backend import media_storage

    original_delete, original_sweep = media_storage.delete, tenant_purge.delete_prefix
    original_enabled = tenant_purge.is_s3_enabled
    media_storage.delete = _deleted_nothing
    tenant_purge.delete_prefix = lambda prefix: {
        "deleted": 0, "failed": 0, "error": "Object storage has no credentials."
    }
    tenant_purge.is_s3_enabled = lambda: False

    db = database.SessionLocal()
    try:
        org_id = _due_workspace(db, "Local Disk Co")
        tenant_purge.purge_removed_tenants(db)
        assert _survivors(db, org_id) == {}, "an unconfigured bucket blocked the purge"
    finally:
        media_storage.delete, tenant_purge.delete_prefix = original_delete, original_sweep
        tenant_purge.is_s3_enabled = original_enabled
        db.close()


if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print(f"  ok  {name}")
        print("OK - a removed workspace survives 30 days, then nothing of it does")
    finally:
        database.engine.dispose()
        _db_file.unlink(missing_ok=True)
