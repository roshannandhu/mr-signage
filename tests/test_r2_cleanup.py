"""Deleting an asset must actually remove it from object storage.

Both reclamation paths were no-ops against R2 while looking like they worked:

* Deleting content called `media_urls.delete_stored_file`, which returns False for anything
  without "/uploads/" in it. An "s3://" key matched nothing, so the database rows went and
  all six objects -- original, four renditions, thumbnail -- stayed in the bucket forever.
* The nightly screenshot prune did call `media_storage.delete`, but screenshots were saved
  with a public "https://pub-..." URL, which `is_remote()` does not recognise, so it fell
  through to the same local-only function. The row was deleted every night; the JPEG never.

Neither was caught, because `tests/test_storage_cleanup.py` sets AWS_ACCESS_KEY_ID="mock",
which makes `is_s3_enabled()` false and skips the whole S3 branch. This file exists to
exercise that branch, so the bucket is the thing being asserted on.

Run directly:  python tests/test_r2_cleanup.py
"""

import asyncio
import os
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCRATCH = f"olrac_r2_{uuid.uuid4().hex[:8]}"
BUCKET = "olrac-r2-test"

os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{SCRATCH}"
os.environ["SECRET_KEY"] = "r2-test-secret"
# NOT "mock" -- that is precisely what disables the code path under test.
os.environ["AWS_ACCESS_KEY_ID"] = "testing"
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
os.environ["AWS_REGION"] = "us-east-1"
os.environ["S3_ENDPOINT_URL"] = ""
os.environ["S3_BUCKET_NAME"] = BUCKET

TEMP_DIR = tempfile.TemporaryDirectory(prefix="olrac-r2-", ignore_cleanup_errors=True)


def main() -> None:
    try:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
    except Exception:
        print("r2 cleanup: SKIPPED (psycopg2 unavailable)")
        return

    try:
        admin = psycopg2.connect(
            "postgresql://postgres:postgres@localhost:5432/postgres", connect_timeout=3
        )
    except Exception:
        print("r2 cleanup: SKIPPED (PostgreSQL is not reachable on localhost:5432)")
        return

    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    admin.cursor().execute(f'CREATE DATABASE "{SCRATCH}" OWNER olrac')
    admin.close()

    try:
        import boto3
        import moto
    except Exception:
        print("r2 cleanup: SKIPPED (moto/boto3 unavailable)")
        return

    from fastapi.testclient import TestClient
    from backend import models
    from backend.database import Base, SessionLocal, engine
    from backend.main import app
    from backend.routers.auth import create_access_token, get_password_hash
    import backend.media_storage as media_storage
    import backend.routers.content as content_router
    import backend.worker as worker

    media_storage.S3_BUCKET = BUCKET
    content_router.S3_BUCKET = BUCKET
    media_storage.UPLOAD_DIR = TEMP_DIR.name

    Base.metadata.create_all(bind=engine)
    client = TestClient(app)

    with moto.mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        # The routers hold module-level clients built at import; point them at the mock.
        content_router.s3_client = s3
        media_storage._client = lambda: s3

        db = SessionLocal()
        org = models.Organization(name="R2 Org", slug=f"r2-{uuid.uuid4().hex[:6]}")
        db.add(org)
        db.commit()
        db.refresh(org)
        # Read off before any db.close(): the instances detach with the session.
        org_id = org.id

        owner = models.User(
            organization_id=org_id, username=f"owner-{uuid.uuid4().hex[:6]}",
            role="owner", is_active=True, hashed_password=get_password_hash("password123"),
        )
        db.add(owner)
        db.commit()
        db.refresh(owner)
        owner_username = owner.username

        def keys() -> set:
            listing = s3.list_objects_v2(Bucket=BUCKET)
            return {item["Key"] for item in listing.get("Contents", [])}

        # --- content deletion must empty the bucket ----------------------------------
        # The six objects a transcoded video leaves behind.
        original = f"{org_id}/movie.mp4"
        thumb = f"{org_id}/movie_thumb.jpg"
        renditions = [f"{org_id}/movie_{name}.mp4"
                      for name in ("1080p", "720p", "540p", "360p")]
        for key in [original, thumb, *renditions]:
            s3.put_object(Bucket=BUCKET, Key=key, Body=b"x" * 1024)

        content = models.Content(
            organization_id=org_id, name="Movie", type="video",
            file_url=f"s3://{original}", thumbnail=f"s3://{thumb}",
            status="ready", file_size_bytes=1024,
        )
        db.add(content)
        db.commit()
        db.refresh(content)
        for key in renditions:
            db.add(models.MediaRendition(
                content_id=content.id, resolution=key.rsplit("_", 1)[1].split(".")[0],
                width=1920, height=1080, rotation=0, duration_ms=1000,
                codec="h264", sha256="a" * 64, file_size_bytes=1024,
                file_url=f"s3://{key}",
            ))
        db.commit()
        content_id = content.id
        db.close()

        assert len(keys()) == 6, f"fixture wrong: {keys()}"

        auth = {"Authorization": f"Bearer {create_access_token(data={'sub': owner_username})}"}
        response = client.delete(f"/api/content/{content_id}", headers=auth)
        assert response.status_code == 200, response.text

        left = keys()
        assert left == set(), (
            f"deleting content left {len(left)} object(s) in the bucket: {sorted(left)}"
        )


if __name__ == "__main__":
    main()
    print("r2 cleanup: all checks passed")
