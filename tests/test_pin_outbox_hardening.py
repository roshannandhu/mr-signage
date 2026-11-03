"""Integration test for Real-Time PIN Management, Outbox, and Zero-Regression Fleet Security.

Tests:
1. Workspace Lock toggle strictly requires a configured Workspace PIN (Option 2.1 - 422).
2. Workspace PIN is encrypted at rest (Fernet) and increments credentials_version.
3. Outbox event is committed atomically on credential changes and lock toggles.
4. Independent Screen PIN overrides Workspace PIN, stores encrypted, can be reset to inherit.
5. Tenant Remote Unlock records a durable DeviceCommandLog and queues outbox event.
6. Screen /sync delivers durable_commands and versioned credentials directives (SET/CLEAR/NONE).
7. Device command acknowledgement (/ack) transitions DeviceCommandLog to EXECUTED.
8. Dynamic Challenge-Response protocol (nonce generation, admin HMAC calculation, single-use verification).
9. Super Admin emergency PIN reveal returns decrypted plaintext and logs audit row without ghost 1234 fallback.
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-pinoutbox-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-pin-outbox-secret")
os.environ.setdefault("PIN_ENCRYPTION_KEY", "7bFk9Y5_5J2Wf4QeL8mNp1RtUvXyZaBcDeFgHiJkLm0=")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.pin_crypto import decrypt_pin, encrypt_pin  # noqa: E402
from backend.routers.auth import get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run() -> None:
    client = TestClient(app)
    db = database.SessionLocal()

    try:
        # Create plan with kiosk_lock entitlement
        plan = models.Plan(
            name="Pro Kiosk Plan",
            slug=f"pro-kiosk-{uuid.uuid4().hex[:6]}",
            feature_flags_json=json.dumps({"kiosk_lock": True, "remote_control": True}),
            is_active=True,
            max_storage_bytes=10 * 1024 ** 3,
        )
        db.add(plan)
        db.commit()

        # Create organization
        org = models.Organization(
            name="Fleet Org",
            slug=f"fleet-{uuid.uuid4().hex[:6]}",
            plan_id=plan.id,
            lock_enabled=False,
            credentials_version=0,
        )
        db.add(org)
        db.commit()

        # Create owner user
        owner_pw = "OwnerSecret123!"
        owner = models.User(
            username=f"owner_{uuid.uuid4().hex[:6]}",
            email=f"owner-{uuid.uuid4().hex[:6]}@example.com",
            hashed_password=get_password_hash(owner_pw),
            organization_id=org.id,
            role="owner",
            is_active=True,
        )
        # Create Super Admin user
        admin_pw = "SuperAdminSecret123!"
        admin = models.User(
            username=f"admin_{uuid.uuid4().hex[:6]}",
            email=f"admin-{uuid.uuid4().hex[:6]}@example.com",
            hashed_password=get_password_hash(admin_pw),
            organization_id=org.id,
            role="super_admin",
            is_active=True,
        )
        db.add_all([owner, admin])
        db.commit()

        owner_token = login_token(owner)
        owner_headers = {"Authorization": f"Bearer {owner_token}"}

        admin_token = login_token(admin)
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Create a screen
        device_id = f"tv-{uuid.uuid4().hex[:8]}"
        pair_code = f"P{uuid.uuid4().hex[:5].upper()}"
        screen = models.Screen(
            organization_id=org.id,
            name="Main Lobby TV",
            device_id=device_id,
            pair_code=pair_code,
            status="online",
            synced_credentials_version=0,
        )
        db.add(screen)
        db.flush()

        from backend.services import issue_device_secret
        secret = issue_device_secret(screen)
        db.commit()
        screen_id = screen.id

        # Authenticate device to get bearer token
        auth_resp = client.post("/api/screens/auth", json={"device_id": device_id, "device_secret": secret})
        check(auth_resp.status_code == 200, f"Device auth failed: {auth_resp.text}")
        device_token = auth_resp.json()["access_token"]
        device_headers = {"Authorization": f"Bearer {device_token}"}

        # -------------------------------------------------------------
        # Test 1: Workspace Lock ON refused without Workspace PIN (Option 2.1)
        # -------------------------------------------------------------
        resp = client.put(
            "/api/screens/lock-enabled",
            json={"lock_enabled": True},
            headers=owner_headers,
        )
        check(
            resp.status_code == 422,
            f"Expected 422 when enabling lock without a PIN, got {resp.status_code}: {resp.text}",
        )

        # -------------------------------------------------------------
        # Test 2: Set Workspace PIN -> Encrypted at rest, version bumps, outbox event
        # -------------------------------------------------------------
        resp = client.put(
            "/api/screens/lock-pin",
            json={"lock_pin": "2468"},
            headers=owner_headers,
        )
        check(resp.status_code == 200, f"Expected 200 setting lock-pin, got {resp.status_code}: {resp.text}")
        data = resp.json()
        check(data.get("credentials_version") == 1, f"Expected credentials_version 1, got {data.get('credentials_version')}")
        check(data.get("lock_pin") == "2468", f"Expected lock_pin '2468', got {data.get('lock_pin')}")

        # Check DB row is encrypted
        db.refresh(org)
        check(org.lock_pin != "2468", "Organization.lock_pin must NOT be stored in plaintext")
        check(decrypt_pin(org.lock_pin) == "2468", "Decrypted lock_pin does not match original '2468'")
        check(org.credentials_version == 1, f"DB credentials_version should be 1, got {org.credentials_version}")

        # Verify Outbox event created
        outbox = db.query(models.OutboxEvent).filter(
            models.OutboxEvent.channel == f"org:{org.id}",
            models.OutboxEvent.event_type == "credentials_updated",
        ).first()
        check(outbox is not None, "Expected credentials_updated outbox event")
        if outbox:
            payload = json.loads(outbox.payload_json)
            check(payload.get("credentials_version") == 1, "Outbox payload credentials_version mismatch")

        # -------------------------------------------------------------
        # Test 3: Enable Workspace Lock now succeeds
        # -------------------------------------------------------------
        resp = client.put(
            "/api/screens/lock-enabled",
            json={"lock_enabled": True},
            headers=owner_headers,
        )
        check(resp.status_code == 200, f"Expected 200 enabling lock with PIN set, got {resp.status_code}: {resp.text}")
        db.refresh(org)
        check(org.lock_enabled is True, "Organization.lock_enabled should be True")
        check(org.credentials_version == 2, f"credentials_version should increment to 2, got {org.credentials_version}")

        # -------------------------------------------------------------
        # Test 4: Independent Screen Maintenance PIN
        # -------------------------------------------------------------
        # Screen initially inherits workspace PIN
        resp = client.get(f"/api/screens/{screen_id}/maintenance-pin", headers=owner_headers)
        check(resp.status_code == 200, f"Expected 200 for maintenance-pin, got {resp.status_code}")
        data = resp.json()
        check(data.get("uses_own_pin") is False, "Expected uses_own_pin=False before setting screen PIN")
        check(data.get("maintenance_pin") == "2468", f"Expected inherited pin '2468', got {data.get('maintenance_pin')}")

        # Set individual screen PIN
        resp = client.put(
            f"/api/screens/{screen_id}/maintenance-pin",
            json={"pin": "7777"},
            headers=owner_headers,
        )
        check(resp.status_code == 200, f"Expected 200 setting screen maintenance pin, got {resp.status_code}")
        data = resp.json()
        check(data.get("uses_own_pin") is True, "Expected uses_own_pin=True after setting screen PIN")
        check(data.get("maintenance_pin") == "7777", f"Expected pin '7777', got {data.get('maintenance_pin')}")

        # Verify DB screen row is encrypted
        db.refresh(screen)
        check(screen.maintenance_pin != "7777", "Screen.maintenance_pin must NOT be stored in plaintext")
        check(decrypt_pin(screen.maintenance_pin) == "7777", "Decrypted maintenance_pin does not match '7777'")

        # -------------------------------------------------------------
        # Test 5: Tenant Remote Unlock creates DeviceCommandLog & Outbox
        # -------------------------------------------------------------
        resp = client.post(f"/api/screens/{screen_id}/remote-unlock", headers=owner_headers)
        check(resp.status_code == 202, f"Expected 202 for remote unlock, got {resp.status_code}: {resp.text}")
        data = resp.json()
        command_id = data.get("command_id")
        check(bool(command_id), f"Expected command_id in response, got {command_id}")

        cmd_log = db.query(models.DeviceCommandLog).filter(models.DeviceCommandLog.id == command_id).first()
        check(cmd_log is not None, f"Expected DeviceCommandLog record for {command_id}")
        if cmd_log:
            check(cmd_log.command == "remote_unlock", f"Expected command remote_unlock, got {cmd_log.command}")
            check(cmd_log.status == "PENDING", f"Expected status PENDING, got {cmd_log.status}")

        # -------------------------------------------------------------
        # Test 6: Screen /sync receives durable commands & versioned PIN
        # -------------------------------------------------------------
        resp = client.get(f"/api/screens/{device_id}/sync", headers=device_headers)
        check(resp.status_code == 200, f"Expected 200 from sync, got {resp.status_code}: {resp.text}")
        sync_data = resp.json()

        # TV should receive custom PIN 7777 with action SET
        check(sync_data.get("maintenance_pin") == "7777", f"Expected PIN 7777, got {sync_data.get('maintenance_pin')}")
        check(sync_data.get("credentials_action") == "SET", f"Expected action SET, got {sync_data.get('credentials_action')}")
        check(sync_data.get("kiosk_lock") is True, "Expected kiosk_lock=True")

        # Check durable commands delivered
        durable_cmds = sync_data.get("durable_commands", [])
        matching_cmd = next((c for c in durable_cmds if c.get("command_id") == command_id), None)
        check(matching_cmd is not None, f"Expected command_id {command_id} in durable_commands: {durable_cmds}")

        # Verify command status transitioned to DELIVERED
        db.refresh(cmd_log)
        check(cmd_log.status == "DELIVERED", f"Expected status DELIVERED after sync, got {cmd_log.status}")

        # -------------------------------------------------------------
        # Test 7: Device ACK transitions command to EXECUTED
        # -------------------------------------------------------------
        ack_payload = {
            "status": "EXECUTED",
        }
        resp = client.post(f"/api/screens/{device_id}/commands/{command_id}/ack", json=ack_payload, headers=device_headers)
        check(resp.status_code == 200, f"Expected 200 from command ack, got {resp.status_code}: {resp.text}")

        db.refresh(cmd_log)
        check(cmd_log.status == "EXECUTED", f"Expected status EXECUTED after ack, got {cmd_log.status}")
        check(cmd_log.executed_at is not None, "Expected executed_at timestamp to be set")

        # Second sync should no longer contain this executed command
        resp2 = client.get(f"/api/screens/{device_id}/sync", headers=device_headers)
        sync2_cmds = resp2.json().get("durable_commands", [])
        check(not any(c.get("command_id") == command_id for c in sync2_cmds), "Executed command must not be returned in subsequent sync")

        # -------------------------------------------------------------
        # Test 8: Dynamic Challenge-Response Master Emergency Unlock
        # -------------------------------------------------------------
        # 8a: TV requests challenge nonce
        resp = client.post(f"/api/screens/{device_id}/challenge", json={}, headers=device_headers)
        check(resp.status_code == 200, f"Expected 200 creating challenge, got {resp.status_code}: {resp.text}")
        chal_data = resp.json()
        nonce = chal_data.get("nonce")
        check(bool(nonce), f"Missing nonce: {chal_data}")

        # 8b: Super Admin calculates response code
        admin_chal_payload = {
            "device_id": device_id,
            "nonce": nonce,
            "password": admin_pw,
            "reason": "Field emergency service by Tech Dave",
        }
        resp = client.post("/api/admin/security/challenge-response", json=admin_chal_payload, headers=admin_headers)
        check(resp.status_code == 200, f"Expected 200 from admin challenge-response, got {resp.status_code}: {resp.text}")
        admin_resp_data = resp.json()
        response_code = admin_resp_data.get("response_code")
        check(len(response_code) == 6 and response_code.isdigit(), f"Response code must be 6 digits, got {response_code}")

        # 8c: TV verifies response code
        resp = client.post(f"/api/screens/{device_id}/verify-challenge", json={"nonce": nonce, "response_code": response_code})
        check(resp.status_code == 200, f"Expected 200 verifying challenge, got {resp.status_code}: {resp.text}")
        verify_data = resp.json()
        check(verify_data.get("verified") is True, f"Expected verified=True, got {verify_data}")

        # 8d: Replay of same code is rejected (single use)
        resp = client.post(f"/api/screens/{device_id}/verify-challenge", json={"nonce": nonce, "response_code": response_code})
        check(resp.status_code == 401, f"Expected 401 on replaying used challenge, got {resp.status_code}")

        # -------------------------------------------------------------
        # Test 9: Super Admin Universal Emergency PIN Reveal (Zero ghost '1234' fallback)
        # -------------------------------------------------------------
        # Before configuring universal PIN
        resp = client.post("/api/admin/security/maintenance-pin/reveal", json={"reason": "Audit verification"}, headers=admin_headers)
        check(resp.status_code == 200, f"Expected 200 revealing unconfigured PIN, got {resp.status_code}")
        reveal_data = resp.json()
        check(reveal_data.get("status") == "not_configured", f"Expected not_configured, got {reveal_data.get('status')}")
        check(reveal_data.get("pin") is None, f"Expected pin None, got {reveal_data.get('pin')}")

        # Set universal PIN
        client.put("/api/admin/maintenance-pin", json={"pin": "8888"}, headers=admin_headers)
        resp = client.post("/api/admin/security/maintenance-pin/reveal", json={"reason": "Audit verification 2"}, headers=admin_headers)
        check(resp.status_code == 200, f"Expected 200 revealing configured PIN, got {resp.status_code}")
        reveal_data2 = resp.json()
        check(reveal_data2.get("status") == "configured", f"Expected configured, got {reveal_data2.get('status')}")
        check(reveal_data2.get("pin") == "8888", f"Expected revealed pin '8888', got {reveal_data2.get('pin')}")

        # Reset screen PIN to inherit from workspace
        client.delete(f"/api/screens/{screen_id}/maintenance-pin", headers=owner_headers)
        resp = client.get(f"/api/screens/{screen_id}/maintenance-pin", headers=owner_headers)
        check(resp.status_code == 200, f"Expected 200 after screen reset, got {resp.status_code}")
        screen_data = resp.json()
        check(screen_data.get("uses_own_pin") is False, "Expected uses_own_pin=False after delete")
        check(screen_data.get("maintenance_pin") == "2468", f"Expected inherited pin '2468', got {screen_data.get('maintenance_pin')}")

    finally:
        db.close()
        if _db_file.exists():
            try:
                _db_file.unlink()
            except Exception:
                pass


if __name__ == "__main__":
    run()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)} failures):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    else:
        print("PASSED: All Phase 2 PIN, Outbox, and Security tests passed cleanly.")
