"""Signing a TV in must never delete a screen that belongs to someone else.

When the hardware came back under a new device id, bind_screen_to_org removed "the transient
placeholder" holding that id -- but it removed whichever row held it, in ANY workspace, before
the tenant check. Signing in with your own screen's installation id and another workspace's
device id therefore deleted THEIR screen. This pins:
  * the victim's screen survives, untouched, and the attacker's is not moved onto its id
  * the real case still works: an unclaimed placeholder holding the new id is replaced
  * a claimed duplicate in the caller's own workspace is refused, not deleted
  * /pair is rate limited, so a signed-in user cannot grind through six-digit codes
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-xtenant-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-cross-tenant-binding")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402
from backend.services.screen_pairing_service import generate_pair_code  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def setup():
    db = database.SessionLocal()
    org_a = models.Organization(name="Victim Co", slug=f"a-{uuid.uuid4().hex[:8]}", status="active")
    org_b = models.Organization(name="Attacker Co", slug=f"b-{uuid.uuid4().hex[:8]}", status="active")
    db.add_all([org_a, org_b])
    db.flush()
    db.add(models.User(organization_id=org_b.id, username="attacker", email="at@x.t",
                       hashed_password=get_password_hash("hunter2hunter2"), role="owner", is_active=True))
    db.add_all([
        models.Screen(organization_id=org_a.id, device_id="dev-victim", installation_id="hw-A",
                      name="Victim Lobby", status="online"),
        models.Screen(organization_id=org_b.id, device_id="dev-b-old", installation_id="hw-B",
                      name="Mine", status="online"),
        models.Screen(organization_id=org_b.id, device_id="dev-dup", installation_id="hw-dup",
                      name="Mine too", status="online"),
        # What /register leaves behind for a panel that has not been claimed by anyone.
        models.Screen(organization_id=None, device_id="dev-new", status="waiting_pairing"),
    ])
    db.commit()
    db.close()


def screen(device_id: str):
    db = database.SessionLocal()
    try:
        row = db.query(models.Screen).filter(models.Screen.device_id == device_id).first()
        if row is not None:
            db.expunge(row)
        return row
    finally:
        db.close()


def sign_in(client, **body):
    payload = {"username": "attacker", "password": "hunter2hunter2", **body}
    return client.post("/api/screens/sign-in", json=payload)


def run() -> None:
    setup()
    client = TestClient(app)

    # --- the attack: my installation id, their device id ------------------------------------
    stolen = sign_in(client, device_id="dev-victim", installation_id="hw-B", name="Gotcha")
    check(stolen.status_code in (401, 403, 409), f"cross-tenant bind was not refused ({stolen.status_code})")
    victim = screen("dev-victim")
    check(victim is not None, "the other workspace's screen was DELETED")
    if victim is not None:
        check(victim.name == "Victim Lobby", f"the other workspace's screen was changed: {victim.name!r}")
    mine = screen("dev-b-old")
    check(mine is not None, "the attacker's own screen was moved onto the victim's device id")

    # --- the legitimate case: an unclaimed placeholder holds the new id ---------------------
    moved = sign_in(client, device_id="dev-new", installation_id="hw-B", name="Mine")
    check(moved.status_code == 200, f"re-binding hardware that came back failed: {moved.text}")
    check(screen("dev-b-old") is None, "the screen kept its old device id")
    check(screen("dev-new") is not None, "the screen did not take the new device id")
    db = database.SessionLocal()
    try:
        count = db.query(models.Screen).filter(models.Screen.device_id == "dev-new").count()
    finally:
        db.close()
    check(count == 1, f"{count} rows hold the new device id; the placeholder was not replaced")

    # --- a claimed duplicate in the caller's own workspace is refused, not deleted ----------
    clash = sign_in(client, device_id="dev-dup", installation_id="hw-B")
    check(clash.status_code == 409, f"a claimed same-workspace duplicate was not refused ({clash.status_code})")
    check(screen("dev-dup") is not None, "the claimed duplicate was deleted")

    # --- /pair is rate limited ----------------------------------------------------------------
    token = create_access_token({"sub": "attacker"})
    statuses = [
        client.post("/api/screens/pair", json={"pair_code": "000000"},
                    headers={"Authorization": f"Bearer {token}"}).status_code
        for _ in range(40)
    ]
    check(429 in statuses, f"40 pairing guesses in a row were never limited: {sorted(set(statuses))}")

    # --- codes are still six digits ------------------------------------------------------------
    codes = {generate_pair_code() for _ in range(200)}
    check(all(len(code) == 6 and code.isdigit() for code in codes), "a pairing code was not six digits")
    check(len(codes) > 150, "pairing codes are not varied enough")


if __name__ == "__main__":
    try:
        run()
    finally:
        database.engine.dispose()
        _db_file.unlink(missing_ok=True)
    if FAILURES:
        print("FAIL")
        for failure in FAILURES:
            print("  -", failure)
        sys.exit(1)
    print("OK - signing a TV in cannot delete or take over another workspace's screen")
