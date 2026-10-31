"""Five cross-cutting flows, end to end: python tests/test_all_five_areas_e2e.py

This file used to set no DATABASE_URL at all. Every other database-owning script here
creates a throwaway database before importing backend; this one imported backend straight
away, so it bound to whatever the ambient environment pointed at -- in practice the
developer's own database via backend/.env. Two consequences, both bad:

  1. It read state it never created. Area 4 minted a token for "admin@olrac.com" and
     assumed that account already existed as a super admin, which was true on the machine
     it was written on and nowhere else. Run alone it passed; run in the suite, after
     another script had touched that row, it failed -- and the failure looked like a
     product bug rather than a harness one.
  2. It wrote to that same database. It creates and deletes organisations, users and
     screens, so a green suite was quietly mutating real data.

It now builds its own database like every sibling, and seeds the super admin it depends on
instead of hoping for it.
"""

import os
import sys
import pathlib
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))

TEMP_DIR = tempfile.TemporaryDirectory(prefix="olrac-e2e-test-", ignore_cleanup_errors=True)
DB_PATH = pathlib.Path(TEMP_DIR.name) / "e2e.db"

import psycopg2  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

test_db_name = f"olrac_test_{DB_PATH.stem.replace('-', '_')}"
try:
    conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    conn.cursor().execute(f"DROP DATABASE IF EXISTS {test_db_name}")
    conn.cursor().execute(f"CREATE DATABASE {test_db_name} OWNER olrac")
    conn.close()
except Exception:
    pass
os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{test_db_name}"
os.environ["SECRET_KEY"] = "e2e-test-secret-not-for-production"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "mock"

import json  # noqa: E402
import pytest  # noqa: E402
from datetime import datetime, timezone, timedelta  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from backend.main import app  # noqa: E402
from backend.database import SessionLocal  # noqa: E402
from backend import models  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402
from backend.worker import aggregate_play_logs_sync  # noqa: E402
from tests._test_plans import grant_plan  # noqa: E402

client = TestClient(app)

SUPER_ADMIN_USERNAME = "admin@olrac.com"


def seed_super_admin():
    """The platform operator Area 4 acts as. Seeded rather than assumed."""
    db = SessionLocal()
    try:
        existing = (
            db.query(models.User)
            .filter(models.User.username == SUPER_ADMIN_USERNAME)
            .first()
        )
        if existing:
            existing.role = "super_admin"
            existing.is_active = True
            db.commit()
            return
        org = (
            db.query(models.Organization)
            .filter(models.Organization.slug == "platform")
            .first()
        )
        if not org:
            org = models.Organization(name="Platform", slug="platform", status="active")
            db.add(org)
            db.flush()
        db.add(
            models.User(
                organization_id=org.id,
                username=SUPER_ADMIN_USERNAME,
                email=SUPER_ADMIN_USERNAME,
                role="super_admin",
                is_active=True,
                hashed_password=get_password_hash("e2e-platform-op-pass"),
            )
        )
        db.commit()
    finally:
        db.close()

def test_area_1_counting_online_and_offline():
    """Area 1: Test ad counting, offline buffering, batch upload, and rollup aggregation."""
    db = SessionLocal()
    u = uuid.uuid4().hex[:6]
    org = models.Organization(name=f"Counting Org {u}", slug=f"counting-org-{u}", status="active")
    db.add(org)
    db.flush()
    grant_plan(db, org, ads_counting=True)  # the media report below is sold behind this flag

    screen = models.Screen(organization_id=org.id, device_id=f"count-dev-{u}", name="Counting Screen 1", status="online")
    content = models.Content(organization_id=org.id, name="Test Ad Video", type="video/mp4", file_url="/test.mp4", duration_ms=15000, status="ready")
    playlist = models.Playlist(organization_id=org.id, name="Test Playlist")
    db.add_all([screen, content, playlist])
    db.commit()

    try:
        # Simulate 5 offline play events buffered on the TV
        now = datetime.now(timezone.utc)
        events = []
        for i in range(5):
            t_start = (now - timedelta(minutes=10 - i)).isoformat()
            t_end = (now - timedelta(minutes=10 - i) + timedelta(seconds=15)).isoformat()
            events.append({
                "event_id": f"evt-count-{u}-{i}",
                "media_id": content.id,
                "playlist_id": playlist.id,
                "campaign_id": None,
                "device_started_at": t_start,
                "device_finished_at": t_end,
                "corrected_started_at": t_start,
                "corrected_finished_at": t_end,
                "duration_ms": 15000,
                "status": "completed",
                "error_message": None
            })

        # Upload batch
        res = client.post("/api/screens/play-logs/batch", json={
            "device_id": screen.device_id,
            "events": events
        })
        assert res.status_code == 200
        assert res.json()["inserted"] == 5

        # Run rollups aggregator
        aggregate_play_logs_sync(db)

        # Check analytics media endpoint
        owner_user = models.User(organization_id=org.id, username=f"count_owner_{u}", email=f"count_owner_{u}@test.com", role="owner", is_active=True, hashed_password="dummy")
        db.add(owner_user)
        db.commit()

        token = create_access_token({"sub": owner_user.username})
        res_analytics = client.get(f"/api/analytics/media/{content.id}", headers={"Authorization": f"Bearer {token}"})
        assert res_analytics.status_code == 200
        data = res_analytics.json()
        assert data["today"]["completed_plays"] >= 5
    finally:
        db.query(models.PlayLog).filter(models.PlayLog.screen_id == screen.id).delete()
        db.query(models.PlayLogHourlyRollup).filter(models.PlayLogHourlyRollup.media_id == content.id).delete()
        db.query(models.User).filter(models.User.organization_id == org.id).delete()
        db.delete(screen)
        db.delete(content)
        db.delete(playlist)
        db.delete(org)
        db.commit()
        db.close()


def test_area_2_bring_to_front_command():
    """Area 2: Test Bring-to-Front remote command dispatch and heartbeat delivery."""
    db = SessionLocal()
    u = uuid.uuid4().hex[:6]
    org = models.Organization(name=f"BTF Org {u}", slug=f"btf-org-{u}", status="active")
    db.add(org)
    db.flush()
    grant_plan(db, org, remote_control=True)  # bring-to-front is sold behind this flag

    screen = models.Screen(organization_id=org.id, device_id=f"btf-dev-{u}", name="BTF Screen 1", status="online")
    owner_user = models.User(organization_id=org.id, username=f"btf_owner_{u}", email=f"btf_owner_{u}@test.com", role="owner", is_active=True, hashed_password="dummy")
    db.add_all([screen, owner_user])
    db.commit()

    try:
        token = create_access_token({"sub": owner_user.username})
        headers = {"Authorization": f"Bearer {token}"}

        # Trigger bring-to-front
        res = client.post(f"/api/screens/{screen.id}/bring-to-front", headers=headers)
        assert res.status_code == 200
        assert res.json()["status"] == "ok"

        # Test that next heartbeat receives the pending command
        res_hb = client.post("/api/screens/heartbeat", json={
            "device_id": screen.device_id,
            "app_version": "1.0",
            "device_version": "1.0"
        })
        assert res_hb.status_code == 200
        hb_data = res_hb.json()
        assert hb_data["pending_command"] == "bring_to_front"

        # Next heartbeat after delivery should have cleared the command
        res_hb2 = client.post("/api/screens/heartbeat", json={
            "device_id": screen.device_id,
            "app_version": "1.0"
        })
        assert res_hb2.status_code == 200
        assert res_hb2.json()["pending_command"] is None
    finally:
        db.delete(screen)
        db.delete(owner_user)
        db.delete(org)
        db.commit()
        db.close()


def test_area_3_reinstall_deduplication():
    """Area 3: Test 1 TV Reinstall Concept (hardware deduplication on re-pairing)."""
    db = SessionLocal()
    u = uuid.uuid4().hex[:6]
    org = models.Organization(name=f"Dedup Org {u}", slug=f"dedup-org-{u}", status="active")
    db.add(org)
    db.flush()

    # Step 1: Device first registered with installation_id
    install_id = f"hw-id-{u}"
    res_reg1 = client.post("/api/screens/register", json={
        "device_id": f"dev1-{u}",
        "installation_id": install_id,
        "device_model": "Lenovo TB-8505F"
    })
    assert res_reg1.status_code == 200
    pair_code = res_reg1.json()["pair_code"]

    # Step 2: Owner pairs screen
    owner_user = models.User(organization_id=org.id, username=f"dedup_owner_{u}", email=f"dedup_owner_{u}@test.com", role="owner", is_active=True, hashed_password="dummy")
    db.add(owner_user)
    db.commit()

    try:
        token = create_access_token({"sub": owner_user.username})
        headers = {"Authorization": f"Bearer {token}"}

        res_pair = client.post("/api/screens/pair", json={"pair_code": pair_code, "name": "Lobby Display Original"}, headers=headers)
        assert res_pair.status_code == 200
        original_screen_id = res_pair.json()["id"]

        # Verify 1 screen in org
        screens_org = db.query(models.Screen).filter(models.Screen.organization_id == org.id).all()
        assert len(screens_org) == 1

        # Step 3: App is uninstalled and reinstalled -> mints new device_id but same installation_id
        res_reg2 = client.post("/api/screens/register", json={
            "device_id": f"dev2-{u}",
            "installation_id": install_id,
            "device_model": "Lenovo TB-8505F"
        })
        assert res_reg2.status_code == 200

        # It auto-reclaims existing screen
        db.expire_all()
        screens_after = db.query(models.Screen).filter(models.Screen.organization_id == org.id).all()
        assert len(screens_after) == 1
        assert screens_after[0].id == original_screen_id
        assert screens_after[0].device_id == f"dev2-{u}"
    finally:
        db.query(models.Screen).filter(models.Screen.organization_id == org.id).update({"playlist_id": None}, synchronize_session=False)
        db.query(models.Playlist).filter(models.Playlist.organization_id == org.id).delete(synchronize_session=False)
        db.query(models.Screen).filter(models.Screen.organization_id == org.id).delete()
        db.query(models.Screen).filter(models.Screen.installation_id == install_id).delete()
        db.delete(owner_user)
        db.delete(org)
        db.commit()
        db.close()


def test_area_4_role_promotion_and_approval_lifecycle():
    """Area 4: Test pending approval on signup, super admin approval, and role promotion/demotion."""
    db = SessionLocal()
    u = uuid.uuid4().hex[:6]
    admin_token = create_access_token({"sub": SUPER_ADMIN_USERNAME})
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Step 1: New workspace registers
    new_org = models.Organization(name=f"Candidate Gym {u}", slug=f"candidate-gym-{u}", status="pending_approval")
    db.add(new_org)
    db.flush()

    new_user = models.User(organization_id=new_org.id, username=f"gym_owner_{u}@test.com", email=f"gym_owner_{u}@test.com", role="owner", is_active=True, hashed_password="dummy")
    db.add(new_user)
    db.commit()

    try:
        # Step 2: Check that user profile returns pending_approval
        user_token = create_access_token({"sub": new_user.username})
        user_headers = {"Authorization": f"Bearer {user_token}"}
        res_me = client.get("/api/auth/me", headers=user_headers)
        assert res_me.status_code == 200
        assert res_me.json()["organization_status"] == "pending_approval"

        # Step 3: Non-superadmin cannot access admin endpoints
        res_forbidden = client.get("/api/admin/tenants", headers=user_headers)
        assert res_forbidden.status_code == 403

        # Step 4: Super Admin approves the tenant
        res_approve = client.post(f"/api/admin/tenants/{new_org.id}/approve", json={"max_screens": 10}, headers=admin_headers)
        assert res_approve.status_code == 200
        assert res_approve.json()["status"] == "active"

        # Step 5: Super Admin promotes user to Super Admin
        res_promote = client.patch(f"/api/admin/users/{new_user.id}/role", json={"role": "super_admin"}, headers=admin_headers)
        assert res_promote.status_code == 200
        assert res_promote.json()["role"] == "super_admin"

        # Promoted user can now access admin endpoints
        res_admin_access = client.get("/api/admin/tenants", headers=user_headers)
        assert res_admin_access.status_code == 200

        # Step 6: Demote back to owner
        res_demote = client.patch(f"/api/admin/users/{new_user.id}/role", json={"role": "owner"}, headers=admin_headers)
        assert res_demote.status_code == 200
        assert res_demote.json()["role"] == "owner"
    finally:
        db.delete(new_user)
        db.delete(new_org)
        db.commit()
        db.close()


def test_area_5_hybrid_presence_and_system_health():
    """Area 5: Test hybrid presence calculation (Redis + last_seen timestamp)."""
    db = SessionLocal()
    u = uuid.uuid4().hex[:6]
    org = models.Organization(name=f"Presence Org {u}", slug=f"presence-org-{u}", status="active")
    db.add(org)
    db.flush()

    # Create screen with recent last_seen (5 seconds ago)
    recent_time = datetime.now(timezone.utc) - timedelta(seconds=5)
    screen_online = models.Screen(organization_id=org.id, device_id=f"dev-online-{u}", name="Online Screen", status="online", last_seen=recent_time)
    
    # Create screen with old last_seen (10 minutes ago)
    old_time = datetime.now(timezone.utc) - timedelta(minutes=10)
    screen_offline = models.Screen(organization_id=org.id, device_id=f"dev-offline-{u}", name="Offline Screen", status="offline", last_seen=old_time)

    owner_user = models.User(organization_id=org.id, username=f"pres_owner_{u}", email=f"pres_owner_{u}@test.com", role="owner", is_active=True, hashed_password="dummy")
    db.add_all([screen_online, screen_offline, owner_user])
    db.commit()

    try:
        token = create_access_token({"sub": owner_user.username})
        headers = {"Authorization": f"Bearer {token}"}

        res_screens = client.get("/api/screens/", headers=headers)
        assert res_screens.status_code == 200
        screens_list = res_screens.json()

        screen_online_res = next((s for s in screens_list if s["id"] == screen_online.id), None)
        screen_offline_res = next((s for s in screens_list if s["id"] == screen_offline.id), None)

        assert screen_online_res is not None
        assert screen_online_res["status"] == "online"
        assert screen_offline_res is not None
        assert screen_offline_res["status"] == "offline"
    finally:
        db.delete(screen_online)
        db.delete(screen_offline)
        db.delete(owner_user)
        db.delete(org)
        db.commit()
        db.close()


if __name__ == "__main__":
    # The schema is built by the app's lifespan, which only runs once a request is made.
    with TestClient(app):
        pass
    seed_super_admin()

    print("Running Area 1: Counting (Online & Offline)...")
    test_area_1_counting_online_and_offline()
    print("[PASS] Area 1 PASSED")

    print("Running Area 2: Bring-to-Front...")
    test_area_2_bring_to_front_command()
    print("[PASS] Area 2 PASSED")

    print("Running Area 3: Reinstall Deduplication...")
    test_area_3_reinstall_deduplication()
    print("[PASS] Area 3 PASSED")

    print("Running Area 4: Role Promotion & Approval Lifecycle...")
    test_area_4_role_promotion_and_approval_lifecycle()
    print("[PASS] Area 4 PASSED")

    print("Running Area 5: Hybrid Presence & System Health...")
    test_area_5_hybrid_presence_and_system_health()
    print("[PASS] Area 5 PASSED")

    print("==========================================")
    print("ALL 5 AREAS TESTED AND VERIFIED 100% PASS!")
    print("==========================================")
