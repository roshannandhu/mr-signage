import pathlib
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Its OWN database, chosen BEFORE backend is imported.
#
# This file set no DATABASE_URL at all, so running it directly -- which is how every script
# here is meant to be run -- let backend/database.py fall back to load_dotenv() and bind the
# engine to whatever backend/.env pointed at. On a developer machine that is the PRODUCTION
# Supabase database, and this script creates an organisation, a screen and play logs. Rows
# named "Ad Test Org" and "BTF Test Org" were found sitting in production, dated to test runs.
#
# conftest.py already guards the pytest path by setting DATABASE_URL before collection; it
# cannot help a script run on its own, which is why the choice belongs here.
import tempfile
_db_path = pathlib.Path(tempfile.gettempdir()) / f"olrac-adcount-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
os.environ.setdefault("SECRET_KEY", "pytest-isolated")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient
from backend.main import app

# Build the schema explicitly. backend.main used to do this as an import side effect,
# so importing the app silently wrote to whatever DATABASE_URL pointed at; it now runs
# in the lifespan, which a bare TestClient(app) never starts. Each isolated script owns
# its own database anyway, so creating it here is the honest version of what was
# happening implicitly before.
from backend import database as _bootstrap_db, models as _bootstrap_models
_bootstrap_models.Base.metadata.create_all(bind=_bootstrap_db.engine)
from backend import database, models
from backend.routers.auth import get_password_hash
from tests._test_plans import grant_plan

client = TestClient(app)

def run():
    db = database.SessionLocal()
    try:
        # Clean up
        db.query(models.PlayLog).delete()
        db.query(models.PlayLogHourlyRollup).delete()
        db.query(models.PlaylistItem).delete()
        db.query(models.Playlist).delete()
        db.query(models.Screen).filter(models.Screen.name.like("Ad Screen%")).delete()
        db.query(models.Content).filter(models.Content.name == "Test Morning Ad").delete()
        db.query(models.User).filter(models.User.email == "ad_owner@olrac.com").delete()
        # Child rows first: the organization FKs carry no ON DELETE, so an alert or a
        # subscription left by an earlier run blocks the delete and fails this test in its
        # own setup rather than in anything it is checking.
        stale_org = db.query(models.Organization).filter(models.Organization.name == "Ad Test Org").first()
        if stale_org:
            for child in (models.Alert, models.Subscription):
                db.query(child).filter(child.organization_id == stale_org.id).delete()
            db.delete(stale_org)
        db.commit()

        org = models.Organization(name="Ad Test Org", slug="ad-test-org", status="active")
        db.add(org)
        db.flush()
        grant_plan(db, org, ads_counting=True)  # the media report below is sold behind this flag
        db.commit()
        db.refresh(org)

        owner = models.User(
            email="ad_owner@olrac.com",
            username="ad_owner",
            hashed_password=get_password_hash("adminpass123"),
            organization_id=org.id,
            role="owner",
        )
        db.add(owner)
        db.commit()

        content = models.Content(
            name="Test Morning Ad",
            type="video",
            status="ready",
            file_url="https://example.com/ad.mp4",
            duration_ms=15000,
            organization_id=org.id,
        )
        db.add(content)
        db.commit()
        db.refresh(content)

        playlist = models.Playlist(
            name="Ad Morning Playlist",
            organization_id=org.id,
        )
        db.add(playlist)
        db.commit()
        db.refresh(playlist)

        item = models.PlaylistItem(
            playlist_id=playlist.id,
            content_id=content.id,
            order=0,
            duration=15,
        )
        db.add(item)
        db.commit()

        device_id = f"ad-device-{uuid.uuid4().hex[:8]}"
        screen = models.Screen(
            name="Ad Screen 1",
            device_id=device_id,
            organization_id=org.id,
            status="online",
            playlist_id=playlist.id,
            location="Main Lobby",
            approved_at=models.utcnow(),
            last_seen=models.utcnow(),
        )
        db.add(screen)
        db.commit()
        db.refresh(screen)

        content_id = content.id
        screen_id = screen.id
        org_id = org.id
        playlist_id = playlist.id

    finally:
        db.close()

    # 1. Device sync receives screen_id and organization_id
    sync_resp = client.get(f"/api/screens/{device_id}/sync")
    assert sync_resp.status_code == 200, sync_resp.text
    sync_data = sync_resp.json()
    assert sync_data["screen_id"] == screen_id, f"Expected {screen_id}, got {sync_data.get('screen_id')}"
    assert sync_data["organization_id"] == org_id, f"Expected {org_id}, got {sync_data.get('organization_id')}"

    # 2. Upload play logs from device (with omitted / -1 screen_id and org_id to verify resilient attribution)
    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=5)
    finished = now - timedelta(minutes=4, seconds=45)
    
    event1 = {
        "event_id": str(uuid.uuid4()),
        "media_id": content_id,
        "playlist_id": playlist_id,
        "campaign_id": None,
        "device_started_at": started.isoformat(),
        "device_finished_at": finished.isoformat(),
        "corrected_started_at": started.isoformat(),
        "corrected_finished_at": finished.isoformat(),
        "duration_ms": 15000,
        "status": "completed",
        "error_message": None,
    }
    
    event2 = {
        "event_id": str(uuid.uuid4()),
        "media_id": None, # Fallback to single-item playlist resolution
        "playlist_id": playlist_id,
        "campaign_id": None,
        "device_started_at": (started - timedelta(minutes=2)).isoformat(),
        "device_finished_at": (finished - timedelta(minutes=2)).isoformat(),
        "corrected_started_at": (started - timedelta(minutes=2)).isoformat(),
        "corrected_finished_at": (finished - timedelta(minutes=2)).isoformat(),
        "duration_ms": 15000,
        "status": "completed",
        "error_message": None,
    }

    upload_resp = client.post(
        "/api/screens/play-logs/batch",
        json={
            "device_id": device_id,
            "screen_id": None,
            "organization_id": None,
            "events": [event1, event2]
        }
    )
    assert upload_resp.status_code == 200, upload_resp.text
    assert upload_resp.json()["inserted"] == 2

    # 3. Authenticate as owner and query Ad Report
    login_resp = client.post("/api/auth/token", data={"username": "ad_owner@olrac.com", "password": "adminpass123"})
    assert login_resp.status_code == 200, login_resp.text
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    report_resp = client.get(f"/api/analytics/media/{content_id}", headers=headers)
    assert report_resp.status_code == 200, report_resp.text
    report = report_resp.json()

    assert report["today"]["total_plays"] == 2, f"Expected 2 total plays today, got {report['today']['total_plays']}"
    assert report["today"]["completed_plays"] == 2
    assert report["today"]["success_percent"] == 100.0
    assert report["lifetime"]["total_plays"] == 2

    assert len(report["per_screen"]) == 1
    assert report["per_screen"][0]["screen_id"] == screen_id
    assert report["per_screen"][0]["total_plays"] == 2
    assert report["per_screen"][0]["completed_plays"] == 2

    assert len(report["per_location"]) == 1
    assert report["per_location"][0]["location"] == "Main Lobby"
    assert report["per_location"][0]["total_plays"] == 2

    print("ALL AD DETAIL COUNTING & ATTRIBUTION TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    run()
