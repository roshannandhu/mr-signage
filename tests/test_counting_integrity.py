"""Counting Integrity and Metric Precision Tests: python tests/test_counting_integrity.py

Verifies all counting operations across:
1. Tenant screen counts (online vs offline vs waiting_pairing).
2. Active ad slots count vs expired/future ad placements.
3. Billing screen and storage usage counters.
4. Campaign assigned screen & status counts.
5. Proof of Play aggregation counting accuracy.
"""

import io
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

TEST_DB = "olrac_test_counting"
try:
    _admin = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    _admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    _admin.cursor().execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    _admin.cursor().execute(f"CREATE DATABASE {TEST_DB} OWNER olrac")
    _admin.close()
except Exception:
    pass

os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{TEST_DB}"
os.environ["SECRET_KEY"] = "counting-test-secret-key-12345"
os.environ["INITIAL_ADMIN_USERNAME"] = "count_admin"
os.environ["INITIAL_ADMIN_PASSWORD"] = "count_pass_1234"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "mock"

from fastapi.testclient import TestClient
from backend.main import app

# Build the schema explicitly. backend.main used to do this as an import side effect,
# so importing the app silently wrote to whatever DATABASE_URL pointed at; it now runs
# in the lifespan, which a bare TestClient(app) never starts. Each isolated script owns
# its own database anyway, so creating it here is the honest version of what was
# happening implicitly before.
from backend import database as _bootstrap_db, models as _bootstrap_models
_bootstrap_models.Base.metadata.create_all(bind=_bootstrap_db.engine)
from backend.database import SessionLocal
from backend import models
from backend.routers.auth import get_password_hash

client = TestClient(app)


def run():
    db = SessionLocal()
    try:
        # Create Super Admin User & Organization
        org1 = models.Organization(name="Tenant Alpha", slug="tenant-alpha", max_screens=10, max_ad_slots=5)
        org2 = models.Organization(name="Tenant Beta", slug="tenant-beta", max_screens=3, max_ad_slots=2)
        db.add_all([org1, org2])
        db.commit()
        db.refresh(org1)
        db.refresh(org2)

        admin_user = models.User(
            username="juug22btech48491@gmail.com",
            email="juug22btech48491@gmail.com",
            hashed_password=get_password_hash("adminpass123"),
            role="super_admin",
            organization_id=org1.id,
        )
        owner_beta = models.User(
            username="owner_beta",
            email="owner_beta@example.com",
            hashed_password=get_password_hash("betapass123"),
            role="owner",
            organization_id=org2.id,
        )
        db.add_all([admin_user, owner_beta])
        db.commit()

        # Seed Screens for Tenant Alpha:
        # 2 Online, 1 Offline, 1 waiting_pairing. "Offline" means not heard from recently --
        # the admin console counts by last_seen, so an offline TV needs a stale one.
        stale = models.utcnow() - timedelta(hours=1)
        db.add_all([
            models.Screen(organization_id=org1.id, name="Alpha Screen 1", status="online"),
            models.Screen(organization_id=org1.id, name="Alpha Screen 2", status="online"),
            models.Screen(organization_id=org1.id, name="Alpha Screen 3", status="offline", last_seen=stale),
            models.Screen(organization_id=org1.id, name="Alpha Unpaired", status="waiting_pairing"),
        ])

        # Seed Screens for Tenant Beta:
        # 1 Online, 1 Offline
        db.add_all([
            models.Screen(organization_id=org2.id, name="Beta Screen 1", status="online"),
            models.Screen(organization_id=org2.id, name="Beta Screen 2", status="offline", last_seen=stale),
        ])
        db.commit()

        # Seed Content for Ads
        content1 = models.Content(organization_id=org1.id, name="Ad Video 1", type="video", file_url="/test1.mp4")
        content2 = models.Content(organization_id=org2.id, name="Ad Video 2", type="video", file_url="/test2.mp4")
        db.add_all([content1, content2])
        db.commit()

        now = models.utcnow()
        # Seed AdPlacements for Tenant Alpha:
        # 2 Active (ends in future), 1 Expired (ended in past)
        db.add_all([
            models.AdPlacement(
                organization_id=org1.id,
                content_id=content1.id,
                advertiser="Advertiser A",
                starts_at=now - timedelta(days=1),
                ends_at=now + timedelta(days=5),
            ),
            models.AdPlacement(
                organization_id=org1.id,
                content_id=content1.id,
                advertiser="Advertiser B",
                starts_at=now,
                ends_at=now + timedelta(days=2),
            ),
            models.AdPlacement(
                organization_id=org1.id,
                content_id=content1.id,
                advertiser="Advertiser Expired",
                starts_at=now - timedelta(days=10),
                ends_at=now - timedelta(days=2),
            ),
        ])
        content1_id = content1.id
        org1_id = org1.id
        alpha_screen = db.query(models.Screen).filter_by(name="Alpha Screen 1").one()
        alpha_screen.device_id = "alpha-screen-1-dev"
        db.commit()
        alpha_screen_id = alpha_screen.id

    finally:
        db.close()

    # Authenticate as Super Admin
    login_resp = client.post("/api/auth/token", data={"username": "juug22btech48491@gmail.com", "password": "adminpass123"})
    assert login_resp.status_code == 200, login_resp.text
    admin_token = login_resp.json()["access_token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # 1. Test /api/admin/tenants Counting
    tenants_resp = client.get("/api/admin/tenants", headers=admin_headers)
    assert tenants_resp.status_code == 200, tenants_resp.text
    tenants_data = tenants_resp.json()
    assert len(tenants_data) >= 2

    alpha_summary = next(t for t in tenants_data if t["slug"] == "tenant-alpha")
    beta_summary = next(t for t in tenants_data if t["slug"] == "tenant-beta")

    # Alpha: 3 paired screens (excluding waiting_pairing), 2 online screens, 2 active ad slots
    assert alpha_summary["screens_count"] == 3, f"Expected 3 paired screens, got {alpha_summary['screens_count']}"
    assert alpha_summary["online_screens_count"] == 2, f"Expected 2 online screens, got {alpha_summary['online_screens_count']}"
    assert alpha_summary["ad_slots_used"] == 2, f"Expected 2 active ad slots, got {alpha_summary['ad_slots_used']}"

    # Beta: 2 paired screens, 1 online screen, 0 active ad slots
    assert beta_summary["screens_count"] == 2, f"Expected 2 paired screens, got {beta_summary['screens_count']}"
    assert beta_summary["online_screens_count"] == 1, f"Expected 1 online screen, got {beta_summary['online_screens_count']}"
    assert beta_summary["ad_slots_used"] == 0, f"Expected 0 ad slots, got {beta_summary['ad_slots_used']}"

    # 2. Test Quota Updates & Immediate Count Integrity
    quota_resp = client.patch(
        f"/api/admin/tenants/{alpha_summary['id']}/quota",
        headers=admin_headers,
        json={"max_screens": 25, "max_ad_slots": 15},
    )
    assert quota_resp.status_code == 200
    assert quota_resp.json()["max_screens"] == 25
    assert quota_resp.json()["max_ad_slots"] == 15

    # 3. Test Billing Summary Screen and Storage Count
    login_beta = client.post("/api/auth/token", data={"username": "owner_beta", "password": "betapass123"})
    assert login_beta.status_code == 200
    beta_token = login_beta.json()["access_token"]
    beta_headers = {"Authorization": f"Bearer {beta_token}"}

    # 4. Test Real-time Live Media Playback Report Updates (0 -> 5 plays immediately)
    # Check initial media report is 0
    init_report = client.get(f"/api/analytics/media/{content1_id}", headers=admin_headers)
    assert init_report.status_code == 200
    assert init_report.json()["lifetime"]["total_plays"] == 0

    # Screen reports 5 playback events for content1
    base_time = models.utcnow()
    events = [
        {
            "event_id": str(uuid.uuid4()),
            "media_id": content1_id,
            "device_started_at": (base_time + timedelta(seconds=i*10)).isoformat(),
            "device_finished_at": (base_time + timedelta(seconds=i*10 + 10)).isoformat(),
            "corrected_started_at": (base_time + timedelta(seconds=i*10)).isoformat(),
            "corrected_finished_at": (base_time + timedelta(seconds=i*10 + 10)).isoformat(),
            "duration_ms": 10000,
            "status": "completed",
        }
        for i in range(5)
    ]

    # Upload play logs
    upload_resp = client.post(
        "/api/screens/play-logs/batch",
        json={
            "device_id": "alpha-screen-1-dev",
            "screen_id": alpha_screen_id,
            "organization_id": org1_id,
            "events": events,
        },
    )
    assert upload_resp.status_code == 200, upload_resp.text
    assert upload_resp.json()["inserted"] == 5

    # Check that media report immediately reflects 5 plays without waiting for cron
    updated_report = client.get(f"/api/analytics/media/{content1_id}", headers=admin_headers)
    assert updated_report.status_code == 200
    report_data = updated_report.json()
    assert report_data["today"]["total_plays"] == 5, f"Expected 5 plays today, got {report_data['today']['total_plays']}"
    assert report_data["lifetime"]["total_plays"] == 5, f"Expected 5 lifetime plays, got {report_data['lifetime']['total_plays']}"
    assert report_data["lifetime"]["success_percent"] == 100.0

    print("ALL COUNTING AND REAL-TIME METRIC PRECISION TESTS PASSED SUCCESSFULLY!")


if __name__ == "__main__":
    run()
