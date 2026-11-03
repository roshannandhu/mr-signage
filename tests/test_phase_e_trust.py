"""Phase E & F Trust, Security, Platform Health, Settings, Attention Queue & Notes tests.
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-trust-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-trust-secret")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402
from datetime import timedelta  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run():
    client = TestClient(app)
    db = database.SessionLocal()

    # 1. Seed super admin and tenant
    admin_org = models.Organization(name="Admin HQ", slug="admin-hq", status="active")
    db.add(admin_org)
    db.flush()

    super_admin = models.User(
        organization_id=admin_org.id,
        username="trust-super-admin",
        email="superadmin@trust.example.com",
        hashed_password=get_password_hash("SuperAdminPass123!"),
        role="super_admin",
        is_active=True,
        token_version=0,
    )
    tenant_org = models.Organization(name="Test Tenant Org", slug="test-tenant-org", status="active")
    db.add(tenant_org)
    db.flush()

    tenant_user = models.User(
        organization_id=tenant_org.id,
        username="tenant-owner",
        email="owner@tenant.example.com",
        hashed_password=get_password_hash("TenantPass123!"),
        role="owner",
        is_active=True,
        token_version=0,
    )
    db.add_all([super_admin, tenant_user])
    db.commit()

    admin_token = login_token(super_admin)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    # Test 1: Sign in history recorded in auth_events
    # Wrong password attempt
    bad_login_res = client.post(
        "/api/auth/token",
        data={"username": "tenant-owner", "password": "WrongPassword!"},
    )
    check(bad_login_res.status_code == 401, f"Expected 401, got {bad_login_res.status_code}")

    failed_event = (
        db.query(models.AuthEvent)
        .filter(models.AuthEvent.attempted_username == "tenant-owner", models.AuthEvent.success == False)
        .first()
    )
    check(failed_event is not None, "Failed AuthEvent should be recorded")

    # Correct password attempt
    good_login_res = client.post(
        "/api/auth/token",
        data={"username": "tenant-owner", "password": "TenantPass123!"},
    )
    check(good_login_res.status_code == 200, f"Expected 200, got {good_login_res.status_code}")
    success_event = (
        db.query(models.AuthEvent)
        .filter(models.AuthEvent.attempted_username == "tenant-owner", models.AuthEvent.success == True)
        .first()
    )
    check(success_event is not None, "Successful AuthEvent should be recorded")

    # Test 2: Token versioning and "Sign out everywhere"
    # A token with tv=0 should be valid currently
    t0_token = create_access_token({"sub": tenant_user.username, "uid": tenant_user.id, "tv": 0})
    me_res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {t0_token}"})
    check(me_res.status_code == 200, f"Expected 200 for token tv=0, got {me_res.status_code}")

    # Sign out everywhere for tenant_user
    so_res = client.post(
        f"/api/admin/users/{tenant_user.id}/sign-out-everywhere",
        headers=admin_headers,
    )
    check(so_res.status_code == 200, f"Expected 200 for sign-out-everywhere, got {so_res.status_code}")
    so_body = so_res.json()
    check(so_body.get("token_version") == 1, f"Expected token_version=1, got {so_body.get('token_version')}")

    # Now old token with tv=0 should be rejected (401)!
    me_revoked_res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {t0_token}"})
    check(me_revoked_res.status_code == 401, f"Expected 401 for revoked token, got {me_revoked_res.status_code}")

    # New login token gets tv=1 and should be accepted
    db.refresh(tenant_user)
    t1_token = login_token(tenant_user)
    me_new_res = client.get("/api/auth/me", headers={"Authorization": f"Bearer {t1_token}"})
    check(me_new_res.status_code == 200, f"Expected 200 for new token, got {me_new_res.status_code}")

    # Test 3: Legacy token without "tv" claim is treated as version 0
    # Before revocation: tv=0, user tv=0 worked. Since user tv is now 1, token without tv claim (0 < 1) is revoked.
    # Reset user token_version to 0 to verify legacy token without tv claim works:
    tenant_user.token_version = 0
    db.commit()
    legacy_token = create_access_token({"sub": tenant_user.username, "uid": tenant_user.id})
    me_legacy = client.get("/api/auth/me", headers={"Authorization": f"Bearer {legacy_token}"})
    check(me_legacy.status_code == 200, "Legacy token without tv claim should be treated as version 0 and accepted")

    # Test 4: Super Admin Google SSO enforcement policy
    # Enable policy
    pol_res = client.put(
        "/api/admin/security/policy",
        headers=admin_headers,
        json={"require_super_admin_google_sso": True},
    )
    check(pol_res.status_code == 200, f"Expected 200 for policy update, got {pol_res.status_code}")

    # Password login for super admin should now be rejected with 403
    sso_blocked = client.post(
        "/api/auth/token",
        data={"username": "trust-super-admin", "password": "SuperAdminPass123!"},
    )
    check(sso_blocked.status_code == 403, f"Expected 403 for super admin password login when SSO enforced, got {sso_blocked.status_code}")

    # Tenant user can still login with password
    t_login = client.post(
        "/api/auth/token",
        data={"username": "tenant-owner", "password": "TenantPass123!"},
    )
    check(t_login.status_code == 200, f"Expected 200 for tenant password login, got {t_login.status_code}")

    # Turn off SSO enforcement
    client.put(
        "/api/admin/security/policy",
        headers=admin_headers,
        json={"require_super_admin_google_sso": False},
    )

    # Test 5: Security Overview and Maintenance PIN reveal
    sec_overview = client.get("/api/admin/security/overview", headers=admin_headers)
    check(sec_overview.status_code == 200, f"Expected 200 for security overview, got {sec_overview.status_code}")
    sec_data = sec_overview.json()
    check(len(sec_data.get("super_admins", [])) >= 1, "Should list super admins")
    check(len(sec_data.get("recent_sign_ins", [])) >= 1, "Should list sign ins")

    # Reveal PIN
    pin_res = client.post(
        "/api/admin/security/maintenance-pin/reveal",
        headers=admin_headers,
        json={"reason": "Audit verification testing"},
    )
    check(pin_res.status_code == 200, f"Expected 200 for pin reveal, got {pin_res.status_code}")
    check("pin" in pin_res.json(), "Response should contain pin")

    # Test 6: Platform Health endpoint
    health_res = client.get("/api/admin/platform-health", headers=admin_headers)
    check(health_res.status_code == 200, f"Expected 200 for platform health, got {health_res.status_code}")
    health_data = health_res.json()
    check(health_data.get("database") == "connected", f"Database should be connected, got {health_data}")
    check("cron_jobs" in health_data, f"cron_jobs should be in health_data, got {health_data}")

    # Test 7: System Settings GET and PUT
    get_sett = client.get("/api/admin/settings", headers=admin_headers)
    check(get_sett.status_code == 200, f"Expected 200 for get settings, got {get_sett.status_code}")

    put_sett = client.put(
        "/api/admin/settings",
        headers=admin_headers,
        json={
            "contact_email": "ops@mrsignage.com",
            "audit_retention_days": 180,
            "attention_offline_screen_hours": 12,
        },
    )
    check(put_sett.status_code == 200, f"Expected 200 for update settings, got {put_sett.status_code}")
    sett_body = put_sett.json()
    check(sett_body.get("contact_email") == "ops@mrsignage.com", f"Wrong contact_email {sett_body}")
    check(sett_body.get("audit_retention_days") == 180, f"Wrong audit_retention_days {sett_body}")

    # Test 8: Attention Queue & Dismissal
    # Seed an offline screen (>24h)
    old_time = models.utcnow() - timedelta(hours=36)
    off_screen = models.Screen(
        organization_id=tenant_org.id,
        name="Offline TV",
        status="offline",
        last_seen=old_time,
    )
    db.add(off_screen)
    db.commit()

    att_res = client.get("/api/admin/support/attention-queue", headers=admin_headers)
    check(att_res.status_code == 200, f"Expected 200 for attention queue, got {att_res.status_code}")
    att_items = att_res.json()
    check(any(it["rule"] == "offline_24h" for it in att_items), "Expected offline_24h item in attention queue")

    # Snooze the attention item
    dis_res = client.post(
        "/api/admin/support/attention-queue/dismiss",
        headers=admin_headers,
        json={
            "rule": "offline_24h",
            "organization_id": tenant_org.id,
            "target_id": str(off_screen.id),
            "action": "snooze",
            "snooze_hours": 48,
        },
    )
    check(dis_res.status_code == 200, f"Expected 200 for dismiss, got {dis_res.status_code}")

    # Now it should be excluded from queue
    att_after = client.get("/api/admin/support/attention-queue", headers=admin_headers).json()
    check(not any(it["id"] == f"offline_24h:{tenant_org.id}:{off_screen.id}" for it in att_after), "Snoozed item should be excluded from queue")

    # Test 9: Private Tenant Notes
    note_post = client.post(
        f"/api/admin/tenants/{tenant_org.id}/notes",
        headers=admin_headers,
        json={"body": "Customer requesting custom SLA next quarter", "pinned": True},
    )
    check(note_post.status_code == 200, f"Expected 200 for note creation, got {note_post.status_code}")
    note_id = note_post.json()["id"]

    notes_list = client.get(f"/api/admin/tenants/{tenant_org.id}/notes", headers=admin_headers)
    check(notes_list.status_code == 200, f"Expected 200 for notes list, got {notes_list.status_code}")
    check(len(notes_list.json()) == 1, "Should have 1 note")

    note_del = client.delete(f"/api/admin/tenants/{tenant_org.id}/notes/{note_id}", headers=admin_headers)
    check(note_del.status_code == 200, f"Expected 200 for note delete, got {note_del.status_code}")

    db.close()
    if _db_file.exists():
        try:
            _db_file.unlink()
        except Exception:
            pass


def test_phase_e_trust():
    run()
    if FAILURES:
        raise AssertionError("\n".join(FAILURES))


if __name__ == "__main__":
    run()
    if FAILURES:
        print("FAILURES:")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("ALL TESTS PASSED")
