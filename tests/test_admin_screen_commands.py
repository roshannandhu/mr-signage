"""Screen remote commands audit and execution test.

POST /api/admin/screens/{screen_id}/commands
POST /api/admin/screens/bulk-commands
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-screen-cmd-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-screen-cmd")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402
from unittest.mock import AsyncMock, patch  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run():
    client = TestClient(app)
    db = database.SessionLocal()

    # Seed super admin
    admin_org = models.Organization(name="Admin Corp", slug="admin-corp", status="active")
    db.add(admin_org)
    db.flush()
    admin_user = models.User(
        organization_id=admin_org.id,
        username="screen-admin",
        email="screen-admin@example.com",
        hashed_password=get_password_hash("AdminPass123!"),
        role="super_admin",
        is_active=True,
    )
    # Seed regular tenant & user
    tenant_org = models.Organization(name="Acme Screens", slug="acme-screens", status="active")
    db.add(tenant_org)
    db.flush()
    reg_user = models.User(
        organization_id=tenant_org.id,
        username="reg-user",
        email="reg-user@example.com",
        hashed_password=get_password_hash("RegPass123!"),
        role="admin",
        is_active=True,
    )

    # Seed screens
    # 1. Paired screen
    screen1 = models.Screen(
        organization_id=tenant_org.id,
        name="Lobby Display",
        device_id="dev-acme-001",
        status="online",
    )
    # 2. Unpaired screen (no device_id)
    screen2 = models.Screen(
        organization_id=tenant_org.id,
        name="Unpaired Screen",
        device_id=None,
        status="pending",
    )
    db.add_all([admin_user, reg_user, screen1, screen2])
    db.commit()

    admin_token = login_token(admin_user)
    reg_token = login_token(reg_user)
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    reg_headers = {"Authorization": f"Bearer {reg_token}"}

    with patch("backend.services.queue_device_command", new_callable=AsyncMock) as mock_queue:
        mock_queue.return_value = True

        # 1. Non-admin forbidden
        res = client.post(
            f"/api/admin/screens/{screen1.id}/commands",
            headers=reg_headers,
            json={"command": "check_update"},
        )
        check(res.status_code == 403, f"Expected 403 for non-admin, got {res.status_code}")

        # 2. Invalid command returns 422
        res = client.post(
            f"/api/admin/screens/{screen1.id}/commands",
            headers=admin_headers,
            json={"command": "reboot_system"},
        )
        check(res.status_code == 422, f"Expected 422 for disallowed command, got {res.status_code}")

        # 3. Unpaired screen returns 422
        res = client.post(
            f"/api/admin/screens/{screen2.id}/commands",
            headers=admin_headers,
            json={"command": "bring_to_front"},
        )
        check(res.status_code == 422, f"Expected 422 for unpaired screen, got {res.status_code}")

        # 4. Non-existent screen returns 404
        res = client.post(
            "/api/admin/screens/99999/commands",
            headers=admin_headers,
            json={"command": "bring_to_front"},
        )
        check(res.status_code == 404, f"Expected 404 for non-existent screen, got {res.status_code}")

        # 5. Successful single command (check_update)
        res = client.post(
            f"/api/admin/screens/{screen1.id}/commands",
            headers=admin_headers,
            json={"command": "check_update", "reason": "Testing update rollout"},
        )
        check(res.status_code == 200, f"Expected 200 for check_update, got {res.status_code}")
        body = res.json()
        check(body.get("queued") is True, f"Expected queued=True, got {body}")
        mock_queue.assert_called_with(screen1.device_id, "check_update", ttl_seconds=None)

        # 6. Verify audit log entry was written
        audit_entry = (
            db.query(models.AdminAuditEvent)
            .filter(models.AdminAuditEvent.target_id == screen1.id)
            .order_by(models.AdminAuditEvent.id.desc())
            .first()
        )
        check(audit_entry is not None, "Audit log entry should exist for command")
        if audit_entry:
            check("screen.command.check_update" == audit_entry.action, f"Wrong action {audit_entry.action}")

        # 7. Test remote_unlock records KioskUnlockEvent
        res = client.post(
            f"/api/admin/screens/{screen1.id}/commands",
            headers=admin_headers,
            json={"command": "remote_unlock", "reason": "Support unlock"},
        )
        check(res.status_code == 200, f"Expected 200 for remote_unlock, got {res.status_code}")
        kiosk_event = (
            db.query(models.KioskUnlockEvent)
            .filter(models.KioskUnlockEvent.screen_id == screen1.id)
            .first()
        )
        check(kiosk_event is not None, "KioskUnlockEvent should be created for remote_unlock")

        # 8. Test bulk commands
        res = client.post(
            "/api/admin/screens/bulk-commands",
            headers=admin_headers,
            json={
                "screen_ids": [screen1.id, screen2.id],
                "command": "bring_to_front",
                "reason": "Bulk focus test",
            },
        )
        check(res.status_code == 200, f"Expected 200 for bulk-commands, got {res.status_code}")
        bulk_body = res.json()
        check(bulk_body.get("total") == 2, f"Expected total=2, got {bulk_body.get('total')}")
        check(bulk_body.get("queued_count") == 1, f"Expected queued_count=1 (screen2 is unpaired), got {bulk_body.get('queued_count')}")

    db.close()
    if _db_file.exists():
        try:
            _db_file.unlink()
        except Exception:
            pass


def test_admin_screen_commands():
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
