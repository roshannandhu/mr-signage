"""Phase 3.10: TV lock becomes a plan feature, with one workspace PIN and a per-screen
opt-out.

kiosk_lock existed as a flag name since Phase 3.1 but gated nothing and reached no TV.
There was also no way to set one PIN for a whole fleet -- every screen carried only its own.
Pins:
  * a tenant without the kiosk_lock feature gets 403 on both lock-pin routes (hide, don't
    lock -- the backend half of the rule, not just the frontend's)
  * only an owner may set the workspace PIN; an editor is refused
  * setting a workspace PIN changes what the TV's sync response offers as maintenance_pin
    for every screen that has not opted out
  * a screen with uses_own_pin=True keeps its own PIN regardless of the workspace one
  * the sync response's kiosk_lock flag matches the plan, not a hardcoded value
  * clearing the workspace PIN (null) restores every screen to its own

Whether the lock is actually armed (organizations.lock_enabled, tenant-controlled, default
off) is covered separately in test_kiosk_lock_enabled.py -- this file only covers PIN
resolution, so a locked plan with lock_enabled still unset reports kiosk_lock: false here.
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-kiosklock-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-kiosk-lock")
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

    org = models.Organization(name="Lockable Co", slug=f"lockable-{uuid.uuid4().hex[:8]}", status="active", plan_id=locked_plan.id)
    bare_org = models.Organization(name="Bare Co", slug=f"bare-{uuid.uuid4().hex[:8]}", status="active", plan_id=bare_plan.id)
    db.add_all([org, bare_org])
    db.flush()

    owner = models.User(organization_id=org.id, username="kl-owner", email="kl-owner@x.t",
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    editor = models.User(organization_id=org.id, username="kl-editor", email="kl-editor@x.t",
                         hashed_password=get_password_hash("x"), role="editor", is_active=True)
    bare_owner = models.User(organization_id=bare_org.id, username="bare-owner", email="bare-owner@x.t",
                             hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add_all([owner, editor, bare_owner])

    shared_device = f"shared-{uuid.uuid4().hex[:8]}"
    own_device = f"own-{uuid.uuid4().hex[:8]}"
    bare_device = f"bare-{uuid.uuid4().hex[:8]}"
    shared_screen = models.Screen(name="Shared PIN TV", device_id=shared_device, organization_id=org.id,
                                  status="online", maintenance_pin="1111")
    own_screen = models.Screen(name="Own PIN TV", device_id=own_device, organization_id=org.id,
                               status="online", maintenance_pin="2222", uses_own_pin=True)
    bare_screen = models.Screen(name="Bare Plan TV", device_id=bare_device, organization_id=bare_org.id,
                                status="online", maintenance_pin="3333")
    db.add_all([shared_screen, own_screen, bare_screen])
    db.flush()

    # maintenance_pin is only returned to a DEVICE that authenticated with its own secret
    # (screen.authenticated), never to the legacy no-credential path -- same guard Phase 1.7
    # added everywhere else this PIN is read.
    from backend.services import issue_device_secret

    shared_secret = issue_device_secret(shared_screen)
    own_secret = issue_device_secret(own_screen)
    bare_secret = issue_device_secret(bare_screen)
    db.commit()
    owner_token, editor_token, bare_owner_token = login_token(owner), login_token(editor), login_token(bare_owner)
    db.close()

    owner_auth = {"Authorization": f"Bearer {owner_token}"}
    editor_auth = {"Authorization": f"Bearer {editor_token}"}
    bare_owner_auth = {"Authorization": f"Bearer {bare_owner_token}"}

    def device_auth(device_id: str, secret: str) -> dict:
        token = client.post("/api/screens/auth", json={"device_id": device_id, "device_secret": secret}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    shared_device_auth = device_auth(shared_device, shared_secret)
    own_device_auth = device_auth(own_device, own_secret)
    bare_device_auth = device_auth(bare_device, bare_secret)

    # --- a plan without kiosk_lock gets 403 on both routes (hide, don't lock) ---------------
    get_bare = client.get("/api/screens/lock-pin", headers=bare_owner_auth)
    check(get_bare.status_code == 403, f"GET lock-pin without the feature should 403, got {get_bare.status_code}")
    put_bare = client.put("/api/screens/lock-pin", headers=bare_owner_auth, json={"lock_pin": "9999"})
    check(put_bare.status_code == 403, f"PUT lock-pin without the feature should 403, got {put_bare.status_code}")

    # --- the bare plan's sync response reports kiosk_lock: false ----------------------------
    bare_sync = client.get(f"/api/screens/{bare_device}/sync", headers=bare_device_auth)
    check(bare_sync.status_code == 200, f"sync failed: {bare_sync.status_code} {bare_sync.text}")
    check(bare_sync.json().get("kiosk_lock") is False, f"expected kiosk_lock false for the bare plan, got {bare_sync.json().get('kiosk_lock')!r}")

    # --- only an owner may set the workspace PIN ---------------------------------------------
    editor_put = client.put("/api/screens/lock-pin", headers=editor_auth, json={"lock_pin": "4444"})
    check(editor_put.status_code == 403, f"an editor should not be able to set the workspace PIN, got {editor_put.status_code}")

    # --- before any workspace PIN: every screen's sync offers its own ------------------------
    shared_sync_before = client.get(f"/api/screens/{shared_device}/sync", headers=shared_device_auth).json()
    check(shared_sync_before.get("maintenance_pin") == "1111", f"expected the screen's own PIN before any workspace PIN, got {shared_sync_before.get('maintenance_pin')!r}")
    check(shared_sync_before.get("kiosk_lock") is False, f"a locked plan should stay unlocked until the tenant enables it, got {shared_sync_before.get('kiosk_lock')!r}")

    # --- an owner sets the workspace PIN ------------------------------------------------------
    get_initial = client.get("/api/screens/lock-pin", headers=owner_auth)
    check(get_initial.status_code == 200 and get_initial.json()["lock_pin"] is None,
          f"expected no workspace PIN set yet, got {get_initial.status_code} {get_initial.text}")
    put_ok = client.put("/api/screens/lock-pin", headers=owner_auth, json={"lock_pin": "5678"})
    check(put_ok.status_code == 200 and put_ok.json()["lock_pin"] == "5678",
          f"setting the workspace PIN failed: {put_ok.status_code} {put_ok.text}")

    # --- the screen with no override now offers the WORKSPACE pin ---------------------------
    shared_sync_after = client.get(f"/api/screens/{shared_device}/sync", headers=shared_device_auth).json()
    check(shared_sync_after.get("maintenance_pin") == "5678", f"expected the workspace PIN, got {shared_sync_after.get('maintenance_pin')!r}")

    # --- the screen that opted out keeps its OWN pin regardless ------------------------------
    own_sync = client.get(f"/api/screens/{own_device}/sync", headers=own_device_auth).json()
    check(own_sync.get("maintenance_pin") == "2222", f"an opted-out screen should keep its own PIN, got {own_sync.get('maintenance_pin')!r}")

    # --- toggling uses_own_pin off brings that screen onto the workspace PIN too ------------
    screens = client.get("/api/screens", headers=owner_auth).json()
    own_screen_id = next(s["id"] for s in screens if s["device_id"] == own_device)
    patched = client.patch(f"/api/screens/{own_screen_id}", headers=owner_auth, json={"uses_own_pin": False})
    check(patched.status_code == 200, f"patching uses_own_pin failed: {patched.status_code} {patched.text}")
    own_sync_after = client.get(f"/api/screens/{own_device}/sync", headers=own_device_auth).json()
    check(own_sync_after.get("maintenance_pin") == "5678", f"after opting back in the screen should offer the workspace PIN, got {own_sync_after.get('maintenance_pin')!r}")

    # --- clearing the workspace PIN restores every screen to its own ------------------------
    cleared = client.put("/api/screens/lock-pin", headers=owner_auth, json={"lock_pin": None})
    check(cleared.status_code == 200 and cleared.json()["lock_pin"] is None,
          f"clearing the workspace PIN failed: {cleared.status_code} {cleared.text}")
    shared_sync_cleared = client.get(f"/api/screens/{shared_device}/sync", headers=shared_device_auth).json()
    check(shared_sync_cleared.get("maintenance_pin") == "1111", f"after clearing, expected the screen's own PIN again, got {shared_sync_cleared.get('maintenance_pin')!r}")

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Kiosk lock PIN: workspace PIN resolution, per-screen opt-out, feature gate all correct")


if __name__ == "__main__":
    run()
