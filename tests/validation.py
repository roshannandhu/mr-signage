"""Isolated storage and failure-path validation: python tests/validation.py"""

import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEMP_DIR = tempfile.TemporaryDirectory(prefix="olrac-storage-test-")
DB_PATH = Path(TEMP_DIR.name) / "validation.db"
os.environ["DATABASE_URL"] = f"sqlite:///{DB_PATH.as_posix()}"
os.environ["SECRET_KEY"] = "storage-test-secret-key-not-for-production"
os.environ["INITIAL_ADMIN_USERNAME"] = "validation-owner"
os.environ["INITIAL_ADMIN_PASSWORD"] = "validation-password"
os.environ["AWS_ACCESS_KEY_ID"] = "mock_key"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock_secret"
os.environ["S3_BUCKET_NAME"] = "olrac-media"
os.environ["AWS_REGION"] = "us-east-1"
os.environ.pop("S3_ENDPOINT_URL", None)

import boto3
from fastapi.testclient import TestClient
from moto import mock_aws

from backend import database, models
from backend.main import app

# Build the schema explicitly. backend.main used to do this as an import side effect,
# so importing the app silently wrote to whatever DATABASE_URL pointed at; it now runs
# in the lifespan, which a bare TestClient(app) never starts. Each isolated script owns
# its own database anyway, so creating it here is the honest version of what was
# happening implicitly before.
from backend import database as _bootstrap_db, models as _bootstrap_models
_bootstrap_models.Base.metadata.create_all(bind=_bootstrap_db.engine)


def owner_headers(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/token",
        data={"username": "validation-owner", "password": "validation-password"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@mock_aws
def run() -> None:
    import backend.routers.content as content_router
    import backend.routers.screens as screens_router

    content_router.s3_client = boto3.client("s3", region_name="us-east-1")
    screens_router.s3_client = boto3.client("s3", region_name="us-east-1")
    content_router.s3_client.create_bucket(Bucket="olrac-media")

    with TestClient(app) as client:
        headers = owner_headers(client)
        image_path = Path(TEMP_DIR.name) / "test-image.png"
        image_path.write_bytes(b"mock image data")
        with image_path.open("rb") as image:
            upload = client.post(
                "/api/content/upload",
                headers=headers,
                data={"name": "Mock Image", "tags": "test"},
                files={"file": ("test-image.png", image, "image/png")},
            )
        assert upload.status_code == 201, upload.text
        upload_data = upload.json()
        assert upload_data["file_url"].startswith("http")
        content_id = upload_data["id"]

        storage_db = database.SessionLocal()
        stored = storage_db.get(models.Content, content_id)
        assert stored and stored.file_url.startswith("s3://")
        s3_key = stored.file_url.removeprefix("s3://")
        storage_db.close()
        s3_object = content_router.s3_client.get_object(Bucket="olrac-media", Key=s3_key)
        assert s3_object["Body"].read() == b"mock image data"

        registration = client.post("/api/screens/register", json={"device_id": "r2-test-tv"})
        screen_id = registration.json()["id"]
        assert client.post("/api/screens/pair", headers=headers, json={"pair_code": registration.json()["pair_code"]}).status_code == 200
        playlist = client.post("/api/playlists/", headers=headers, json={"name": "R2 Playlist"})
        playlist_id = playlist.json()["id"]
        assert client.post(
            f"/api/playlists/{playlist_id}/items",
            headers=headers,
            json={"content_id": content_id, "duration": 10, "order": 0},
        ).status_code == 201
        assert client.post(f"/api/screens/{screen_id}/assign/{playlist_id}", headers=headers).status_code == 200
        sync = client.get("/api/screens/r2-test-tv/sync")
        media_url = sync.json()["playlist"]["items"][0]["content"]["file_url"]
        # Absolute and fetchable, and deliberately NOT signed. A TV caches this URL in its
        # local database; when it was a presigned R2 link the cached copy expired and
        # playback failed with an opaque 403, so the contract is now a stable /api/media
        # path that signs the real URL at the moment it is followed.
        assert media_url.startswith("http")
        assert "/api/media/" in media_url
        assert "X-Amz-Signature" not in media_url and "X-Amz-Expires" not in media_url

        expired = client.post("/api/screens/register", json={"device_id": "expired-test-tv"})
        db = database.SessionLocal()
        screen = db.get(models.Screen, expired.json()["id"])
        screen.pair_code_expires_at = datetime.utcnow() - timedelta(minutes=10)
        db.commit()
        db.close()
        rejected = client.post("/api/screens/pair", headers=headers, json={"pair_code": expired.json()["pair_code"]})
        assert rejected.status_code == 400 and "expired" in rejected.json()["detail"].lower()
        assert client.get("/api/playlists/999999", headers=headers).status_code == 404

    print("MR Signage storage and failure-path validation passed")


if __name__ == "__main__":
    try:
        run()
    finally:
        database.engine.dispose()
        TEMP_DIR.cleanup()
