"""A kiosked screen must always have a way in.

Kiosk swallows Home and Back on purpose. A screen in that state with no maintenance PIN
cannot be reached at all, and the only remaining move is a factory reset -- which unpairs the
panel and loses its playlist. The per-screen PIN is optional and usually skipped, so without a
platform-wide fallback that state is the default rather than the exception.

What this pins:
  * a screen's own PIN wins, and the universal one fills in when it has none
  * the TV is actually SENT the effective PIN, not just the screen's own
  * the tenant can see which code opens their screen
  * only a platform operator can set the universal one
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-pin-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-maintenance-pin")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def setup():
    db = database.SessionLocal()
    operator_org = models.Organization(name="MR Signage", slug=f"o-{uuid.uuid4().hex[:8]}", status="active")
    tenant = models.Organization(name="Venue", slug=f"v-{uuid.uuid4().hex[:8]}", status="active")
    db.add_all([operator_org, tenant])
    db.flush()
    db.add(models.User(organization_id=operator_org.id, username="operator", email="op@x.t",
                       hashed_password=get_password_hash("x"), role="super_admin", is_active=True))
    db.add(models.User(organization_id=tenant.id, username="owner", email="ow@x.t",
                       hashed_password=get_password_hash("x"), role="owner", is_active=True))
    db.add(models.User(organization_id=tenant.id, username="watcher", email="wa@x.t",
                       hashed_password=get_password_hash("x"), role="viewer", is_active=True))
    # Two screens with DIFFERENT own PINs: the master has to open both without replacing
    # either, which is the whole point of it being a second code.
    first = models.Screen(organization_id=tenant.id, device_id="tv-a", status="online",
                          maintenance_pin="1234", installation_id="hw-serial-a")
    second = models.Screen(organization_id=tenant.id, device_id="tv-b", status="online",
                           maintenance_pin="5678", installation_id="hw-serial-b")
    db.add_all([first, second])
    db.flush()
    from backend.services import issue_device_secret
    secrets_by_device = {
        "tv-a": issue_device_secret(first),
        "tv-b": issue_device_secret(second),
    }
    db.commit()
    db.close()
    return secrets_by_device



def device_token(client, device_id: str, secret: str) -> str:
    """Exchange the device secret for the token /sync actually accepts."""
    response = client.post(
        "/api/screens/auth", json={"device_id": device_id, "device_secret": secret}
    )
    assert response.status_code == 200, f"device auth failed: {response.text}"
    return response.json()["access_token"]


def pins_sent_to(client, device_id: str, secret: str):
    """What the player is given. Authenticated, because an unauthenticated device call is
    deliberately told nothing -- the PIN would otherwise be readable by anyone who knows a
    device id, which is echoed back by /register."""
    token = device_token(client, device_id, secret)
    response = client.get(
        f"/api/screens/{device_id}/sync", headers={"Authorization": f"Bearer {token}"}
    )
    if response.status_code != 200:
        return (None, None)
    body = response.json()
    return (body.get("maintenance_pin"), body.get("master_pin"))


def run() -> None:
    device_secrets = setup()
    client = TestClient(app)
    admin = {"Authorization": f"Bearer {create_access_token({'sub': 'operator'})}"}
    owner = {"Authorization": f"Bearer {create_access_token({'sub': 'owner'})}"}

    # --- before any master is set ---------------------------------------------------------
    own, master = pins_sent_to(client, "tv-a", device_secrets["tv-a"])
    check(own == "1234", f"a screen's own PIN should reach it, got {own!r}")
    check(master is None, "there is no master code yet, so none should be sent")

    # --- the operator sets a master --------------------------------------------------------
    saved = client.put("/api/admin/maintenance-pin", headers=admin, json={"pin": "4821"})
    check(saved.status_code == 200, f"setting the master PIN failed: {saved.text}")
    check(saved.json()["pin"] == "4821", f"unexpected response: {saved.json()}")

    for device, expected_own in (("tv-a", "1234"), ("tv-b", "5678")):
        own, master = pins_sent_to(client, device, device_secrets[device])
        check(own == expected_own,
              f"{device} lost its own PIN when a master was set: {own!r}")
        check(master == "4821",
              f"{device} was not given the master code: {master!r}")

    # --- the tenant sees THEIR code, never the operator's master ---------------------------
    screens = client.get("/api/screens/", headers=owner).json()
    by_device = {s["device_id"]: s for s in screens}
    check(by_device["tv-a"]["effective_maintenance_pin"] == "1234",
          "the tenant cannot see the code that opens their own screen")
    check("4821" not in client.get("/api/screens/", headers=owner).text,
          "SECURITY: the operator's master code was handed to a tenant")

    # --- a viewer can see the fleet but not unlock it -------------------------------------
    viewer = {"Authorization": f"Bearer {create_access_token({'sub': 'watcher'})}"}
    seen = client.get("/api/screens/", headers=viewer)
    check(seen.status_code == 200, f"a viewer cannot list screens: {seen.status_code}")
    for screen in seen.json():
        check(screen["maintenance_pin"] is None and screen["effective_maintenance_pin"] is None,
              f"SECURITY: a viewer was shown the PIN of {screen['device_id']}")
        check(screen["installation_id"] is None,
              f"SECURITY: a viewer was shown the hardware identity of {screen['device_id']}")
    check("1234" not in seen.text and "5678" not in seen.text and "4821" not in seen.text,
          "SECURITY: a PIN appears somewhere in a viewer's screen list")
    mine = {s["device_id"]: s for s in client.get("/api/screens/", headers=owner).json()}
    check(mine["tv-a"]["maintenance_pin"] == "1234" and mine["tv-a"]["installation_id"] == "hw-serial-a",
          "an owner must still see their own screen's PIN and hardware identity")

    # --- a blank own PIN never exposes the master ------------------------------------------
    blank_db = database.SessionLocal()
    blank_db.query(models.Screen).filter(models.Screen.device_id == "tv-b").update({"maintenance_pin": ""})
    blank_db.commit()
    blank_db.close()
    owner_view = client.get("/api/screens/", headers=owner)
    check("4821" not in owner_view.text,
          "SECURITY: a screen with a blank PIN showed the operator's master code to its owner")
    check({s["device_id"]: s for s in owner_view.json()}["tv-b"]["effective_maintenance_pin"] is None,
          "a blank own PIN should report no effective PIN to a tenant")

    # --- validation and clearing ----------------------------------------------------------
    for bad in ("12", "abcd", "123456"):
        refused = client.put("/api/admin/maintenance-pin", headers=admin, json={"pin": bad})
        check(refused.status_code == 422, f"accepted a bad PIN {bad!r}: {refused.status_code}")

    cleared = client.put("/api/admin/maintenance-pin", headers=admin, json={"pin": ""})
    check(cleared.status_code == 200 and cleared.json()["pin"] is None,
          f"clearing failed: {cleared.text}")
    own, master = pins_sent_to(client, "tv-a", device_secrets["tv-a"])
    check(master is None, "clearing must take the master code off the panels")
    check(own == "1234", "clearing the master must not touch a screen's own PIN")

    # --- only a platform operator ----------------------------------------------------------
    check(client.get("/api/admin/maintenance-pin", headers=owner).status_code == 403,
          "a tenant owner could read the universal PIN")
    check(client.put("/api/admin/maintenance-pin", headers=owner, json={"pin": "9999"}).status_code == 403,
          "a tenant owner could SET the universal PIN for every screen on the platform")
    check(client.get("/api/admin/maintenance-pin").status_code in (401, 403),
          "an anonymous caller could read the universal PIN")


if __name__ == "__main__":
    try:
        run()
        if FAILURES:
            print("MAINTENANCE PIN FAILURES:")
            for failure in FAILURES:
                print("  -", failure)
            raise SystemExit(1)
        print("OK - the master opens any screen, tenants keep their own, and only the operator sets it")
    finally:
        database.engine.dispose()
        _db_file.unlink(missing_ok=True)
