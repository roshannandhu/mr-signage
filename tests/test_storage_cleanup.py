"""Deleting media must free the disk, and screenshots must not grow forever.

Throwaway Postgres database and a temporary uploads directory — never the live ones.
Run directly:  python tests/test_storage_cleanup.py
"""
import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCRATCH = f"olrac_storage_{uuid.uuid4().hex[:8]}"
os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{SCRATCH}"
os.environ["SECRET_KEY"] = "storage-test-secret"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"

import psycopg2  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

admin = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
admin.cursor().execute(f'CREATE DATABASE "{SCRATCH}" OWNER olrac')

db = None
tmp = tempfile.TemporaryDirectory(prefix="olrac-uploads-", ignore_cleanup_errors=True)
try:
    from fastapi.testclient import TestClient  # noqa: E402
    from backend import models  # noqa: E402
    from backend.database import SessionLocal, engine  # noqa: E402
    from backend.main import app  # noqa: E402
    from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402
    from backend.media_urls import delete_stored_file  # noqa: E402
    import backend.routers.content as content_router  # noqa: E402
    import backend.media_storage as media_storage  # noqa: E402
    import backend.worker as worker  # noqa: E402

    # Point every writer at the temp directory, never the real uploads folder.
    content_router.UPLOAD_DIR = tmp.name
    worker.UPLOAD_DIR = tmp.name
    # Screenshot pruning deletes through media_storage now, so that module owns the root
    # the sweep resolves against; without this the test would silently exercise the real
    # uploads/ directory instead of the temp one.
    media_storage.UPLOAD_DIR = tmp.name
    org_dir = Path(tmp.name, "1")
    org_dir.mkdir(parents=True, exist_ok=True)

    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    org = models.Organization(name="Acme", slug="acme")
    db.add(org); db.commit()
    owner = models.User(organization_id=org.id, username="owner@acme.test",
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add(owner); db.commit()

    def make(name: str) -> str:
        path = org_dir / name
        path.write_bytes(b"x" * 2048)
        return f"/uploads/1/{name}"

    source = make("ad.mp4")
    thumb = make("ad_thumb.jpg")
    rends = [make(f"ad_{r}.mp4") for r in ("1080p", "720p", "540p", "360p")]

    ad = models.Content(organization_id=org.id, type="video", file_url=source,
                        thumbnail=thumb, name="Advert", status="ready")
    db.add(ad); db.commit()
    for res, url in zip(("1080p", "720p", "540p", "360p"), rends):
        db.add(models.MediaRendition(content_id=ad.id, resolution=res, width=1, height=1,
                                     rotation=0, duration_ms=1, codec="h264",
                                     file_size_bytes=2048, file_url=url))
    db.commit()

    assert len(list(org_dir.iterdir())) == 6, list(org_dir.iterdir())

    client = TestClient(app)
    auth = {"Authorization": f"Bearer {create_access_token(data={'sub': owner.username})}"}
    response = client.delete(f"/api/content/{ad.id}", headers=auth)
    assert response.status_code == 200, response.text

    left = list(org_dir.iterdir())
    assert not left, f"deleting content left files behind: {[p.name for p in left]}"
    print("  ok  deleting content removed the source, thumbnail and all four renditions")

    # A crafted path must not delete outside the uploads root.
    outside = Path(tmp.name).parent / "do-not-touch.txt"
    outside.write_text("keep me")
    assert delete_stored_file("/uploads/../do-not-touch.txt", tmp.name) is False
    assert outside.exists(), "path escape deleted a file outside the uploads root"
    outside.unlink()
    print("  ok  a path escaping the uploads root is refused")

    print("storage cleanup: all checks passed")
finally:
    try:
        if db: db.close()
    except Exception:
        pass
    try:
        engine.dispose()
    except Exception:
        pass
    tmp.cleanup()
    cur = admin.cursor()
    cur.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{SCRATCH}'")
    cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}"')
    admin.close()
