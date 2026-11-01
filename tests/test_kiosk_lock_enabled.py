"""The tenant-controlled TV lock redesign: a plan selling kiosk_lock no longer locks a
fleet by itself -- the tenant has to turn it on, and a super admin has a logged recovery
lever for a tenant genuinely stuck.

Before this, kiosk_lock_enabled() read only the plan flag: the moment an admin granted a
tenant the feature (or moved them to a plan that includes it), every already-paired screen
would lock itself at its very next sync with no warning. Pins:
  * a plan with kiosk_lock still reports unlocked until the tenant explicitly enables it
  * enabling it is owner-only and refused without the plan feature (hide, don't lock, holds
    on the backend too)
  * a TV's local PIN attempt (right or wrong) is logged for the admin audit view
  * a super admin can queue a remote unlock for one screen; it is refused for a screen in a
    different tenant's workspace, and it writes its own audit row under the admin's identity
  * the audit list never lets a non-admin see it, and the remote-unlock route is
    super-admin-only
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-kiosklockena-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-kiosk-lock-enabled")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run() -> None:
    client = TestClient(app)
    db = database.SessionLocal()

    locked_plan = models.Plan(
        name="Locked Plan", slug=f"locked-{uuid.uuid4().hex[:6]}",
        feature_flags_json=json.dumps({"kiosk_lock": True}), is_active=True, max_storage_bytes=10 * 1024 ** 3,
    )
    bare_plan = models.Plan(
        name="Bare Plan", slug=f"bare-{uuid.uuid4().hex[:6]}",
        feature_flags_json=json.dumps({}), is_active=True, max_storage_bytes=10 * 1024 ** 3,
    )
    db.add_all([locked_plan, bare_plan])
    db.flush()

    org = models.Organization(name="Org A", slug=f"org-a-{uuid.uuid4().hex[:8]}", status="active", plan_id=locked_plan.id)
    other_org = models.Organization(name="Org B", slug=f"org-b-{uuid.uuid4().hex[:8]}", status="active", plan_id=locked_plan.id)
    bare_org = models.Organization(name="Bare Co", slug=f"bare-{uuid.uuid4().hex[:8]}", status="active", plan_id=bare_plan.id)
    db.add_all([org, other_org, bare_org])
    db.flush()

    super_admin = models.User(organization_id=org.id, username="super", email="super@x.t",
                              hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    owner = models.User(organization_id=org.id, username="kle-owner", email="kle-owner@x.t",
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    editor = models.User(organization_id=org.id, username="kle-editor", email="kle-editor@x.t",
                         hashed_password=get_password_hash("x"), role="editor", is_active=True)
    bare_owner = models.User(organization_id=bare_org.id, username="bare-owner", email="bare-owner@x.t",
                             hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add_all([super_admin, owner, editor, bare_owner])

    device_id = f"kle-device-{uuid.uuid4().hex[:8]}"
    other_device_id = f"kle-other-{uuid.uuid4().hex[:8]}"
    screen = models.Screen(name="Lobby TV", device_id=device_id, organization_id=org.id, status="online")
    other_org_screen = models.Screen(name="Other Org TV", device_id=other_device_id, organization_id=other_org.id, status="online")
    db.add_all([screen, other_org_screen])
    db.flush()

    from backend.services import issue_device_secret
    secret = issue_device_secret(screen)
    db.commit()
    org_id, screen_id, other_org_screen_id = org.id, screen.id, other_org_screen.id
    super_token, owner_token, editor_token, bare_owner_token = (
        login_token(super_admin), login_token(owner), login_token(editor), login_token(bare_owner)
    )
    db.close()

    super_auth = {"Authorization": f"Bearer {super_token}"}
    owner_auth = {"Authorization": f"Bearer {owner_token}"}
    editor_auth = {"Authorization": f"Bearer {editor_token}"}
    bare_owner_auth = {"Authorization": f"Bearer {bare_owner_token}"}
    device_token = client.post("/api/screens/auth", json={"device_id": device_id, "device_secret": secret}).json()["access_token"]
    device_auth = {"Authorization": f"Bearer {device_token}"}

    # --- a plan with the feature is still UNLOCKED until the tenant turns it on -----------
    sync_before = client.get(f"/api/screens/{device_id}/sync", headers=device_auth).json()
    check(sync_before.get("kiosk_lock") is False,
          f"a plan with kiosk_lock should still report unlocked until enabled, got {sync_before.get('kiosk_lock')!r}")

    # --- enabling it is owner-only --------------------------------------------------------
    editor_enable = client.put("/api/screens/lock-enabled", headers=editor_auth, json={"lock_enabled": True})
    check(editor_enable.status_code == 403, f"an editor should not be able to enable lock, got {editor_enable.status_code}")

    bare_enable = client.put("/api/screens/lock-enabled", headers=bare_owner_auth, json={"lock_enabled": True})
    check(bare_enable.status_code == 403, f"a plan without kiosk_lock should refuse enabling it, got {bare_enable.status_code}")

    enabled = client.put("/api/screens/lock-enabled", headers=owner_auth, json={"lock_enabled": True})
    check(enabled.status_code == 200 and enabled.json()["lock_enabled"] is True,
          f"enabling lock failed: {enabled.status_code} {enabled.text}")

    sync_after = client.get(f"/api/screens/{device_id}/sync", headers=device_auth).json()
    check(sync_after.get("kiosk_lock") is True, f"expected kiosk_lock true once enabled, got {sync_after.get('kiosk_lock')!r}")

    # --- a TV's local PIN attempt is logged for the admin audit view -----------------------
    attempt_ok = client.post(f"/api/screens/{device_id}/kiosk-attempt", headers=device_auth, json={"success": False})
    check(attempt_ok.status_code == 200, f"reporting a kiosk attempt failed: {attempt_ok.status_code} {attempt_ok.text}")

    events_forbidden = client.get(f"/api/admin/tenants/{org_id}/kiosk-events", headers=owner_auth)
    check(events_forbidden.status_code == 403, f"a tenant owner should not see the admin audit log, got {events_forbidden.status_code}")

    events = client.get(f"/api/admin/tenants/{org_id}/kiosk-events", headers=super_auth)
    check(events.status_code == 200, f"admin audit log failed: {events.status_code} {events.text}")
    body = events.json()
    check(len(body) == 1 and body[0]["method"] == "pin" and body[0]["success"] is False,
          f"expected the one failed PIN attempt logged, got {body}")

    # --- remote unlock is super-admin-only, and scoped to the right tenant ------------------
    owner_remote = client.post(f"/api/admin/tenants/{org_id}/screens/{screen_id}/remote-unlock", headers=owner_auth)
    check(owner_remote.status_code == 403, f"a tenant owner should not be able to remote-unlock, got {owner_remote.status_code}")

    cross_tenant = client.post(f"/api/admin/tenants/{org_id}/screens/{other_org_screen_id}/remote-unlock", headers=super_auth)
    check(cross_tenant.status_code == 404,
          f"SECURITY: remote-unlock should refuse a screen from a different tenant, got {cross_tenant.status_code}")

    remote = client.post(f"/api/admin/tenants/{org_id}/screens/{screen_id}/remote-unlock", headers=super_auth)
    check(remote.status_code == 200 and remote.json()["queued"] is True,
          f"remote unlock failed: {remote.status_code} {remote.text}")

    events_after = client.get(f"/api/admin/tenants/{org_id}/kiosk-events", headers=super_auth).json()
    check(len(events_after) == 2, f"expected the remote unlock to add a second audit row, got {len(events_after)}")
    remote_row = next((e for e in events_after if e["method"] == "remote"), None)
    check(remote_row is not None and remote_row["actor_username"] == "super",
          f"expected the remote-unlock row to record the admin who queued it, got {remote_row}")

    # --- the queued command actually reaches the TV's next sync -----------------------------
    sync_commands = client.get(f"/api/screens/{device_id}/sync", headers=device_auth).json()
    check("remote_unlock" in (sync_commands.get("pending_commands") or []),
          f"expected remote_unlock queued for the next sync, got {sync_commands.get('pending_commands')!r}")

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Kiosk lock enabled: plan+tenant-switch resolution, PIN audit log, scoped remote unlock all correct")


if __name__ == "__main__":
    run()
