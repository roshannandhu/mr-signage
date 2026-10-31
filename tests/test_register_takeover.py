"""A device id alone must not let a stranger take over a claimed screen.

/register is unauthenticated, and a device id is not a secret: the route echoes it back and the
dashboard shows it. It used to
  * overwrite the screen's installation id (the hardware proof) for any caller, and commit that
    even when the credential check then failed,
  * hand a pending device secret to whoever asked for it first, with no proof of identity.
So a stranger could stamp their own installation id on a claimed screen, call again to pass the
check against it, and be issued a fresh secret -- locking the real panel out.

What this pins:
  * a stranger learns nothing, changes nothing, and does not disturb the real credential
  * the real screen still gets its parked secret and is still re-issued one after a reinstall
  * a screen enrolled before installation ids existed is still backfilled while legacy is open
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-register-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-register-takeover")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")
os.environ.pop("ALLOW_LEGACY_DEVICE_AUTH", None)

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import get_password_hash  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)


class FakeRedis:
    """Just enough of Redis for the parked-secret handoff, and instant instead of timing out."""

    def __init__(self):
        self.store: dict[str, str] = {}

    async def setex(self, key, _ttl, value):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)

    async def getdel(self, key):
        return self.store.pop(key, None)

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)

    def __getattr__(self, _name):
        async def _noop(*_args, **_kwargs):
            return None

        return _noop


FAKE = FakeRedis()
database.get_redis = lambda: FAKE

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def setup():
    db = database.SessionLocal()
    tenant = models.Organization(name="Venue", slug=f"v-{uuid.uuid4().hex[:8]}", status="active")
    db.add(tenant)
    db.flush()
    claimed = models.Screen(organization_id=tenant.id, device_id="tv-a", status="online",
                            installation_id="hw-real", model="Real Model", identity_source="serial",
                            maintenance_pin="1234")
    # A screen enrolled before installation ids were recorded.
    legacy = models.Screen(organization_id=tenant.id, device_id="tv-old", status="online")
    db.add_all([claimed, legacy])
    db.flush()
    from backend.services import issue_device_secret
    secret = issue_device_secret(claimed)
    db.commit()
    db.close()
    return secret


def screen_row(device_id: str):
    db = database.SessionLocal()
    try:
        row = db.query(models.Screen).filter(models.Screen.device_id == device_id).one()
        db.expunge(row)
        return row
    finally:
        db.close()


def register(client, **body):
    return client.post("/api/screens/register", json=body)


def secret_works(client, device_id: str, secret: str) -> bool:
    return client.post(
        "/api/screens/auth", json={"device_id": device_id, "device_secret": secret}
    ).status_code == 200


def run() -> None:
    secret = setup()
    client = TestClient(app)
    hash_before = screen_row("tv-a").device_secret_hash

    # --- a stranger who knows only the device id --------------------------------------------
    for attempt, body in enumerate((
        {"device_id": "tv-a"},
        {"device_id": "tv-a", "installation_id": "hw-attacker", "device_model": "Pwned", "identity_source": "random"},
        {"device_id": "tv-a", "installation_id": "hw-attacker"},  # the second call that used to win
    ), start=1):
        response = register(client, **body)
        check(response.status_code == 200, f"stranger call {attempt} failed: {response.text}")
        check(response.json().get("device_secret") is None, f"stranger call {attempt} was issued a secret")

    row = screen_row("tv-a")
    check(row.installation_id == "hw-real", f"the hardware proof was overwritten: {row.installation_id!r}")
    check(row.model == "Real Model", f"the model was overwritten: {row.model!r}")
    check(row.identity_source == "serial", f"the identity source was overwritten: {row.identity_source!r}")
    check(row.device_secret_hash == hash_before, "the stored credential was replaced by a stranger")
    check(secret_works(client, "tv-a", secret), "the real secret stopped working")

    # --- a parked secret is not for whoever asks -------------------------------------------
    FAKE.store["screen_pending_secret:tv-a"] = "parked-secret"
    for body in ({"device_id": "tv-a"}, {"device_id": "tv-a", "installation_id": "hw-attacker"}):
        stolen = register(client, **body)
        check(stolen.json().get("device_secret") is None, "a stranger collected the parked secret")
    check(FAKE.store.get("screen_pending_secret:tv-a") == "parked-secret",
          "the parked secret was consumed by a stranger")

    # --- the real screen still gets it -----------------------------------------------------
    mine = register(client, device_id="tv-a", installation_id="hw-real")
    check(mine.json().get("device_secret") == "parked-secret", f"the real screen was refused: {mine.text}")
    check("screen_pending_secret:tv-a" not in FAKE.store, "the parked secret was not consumed")
    check(register(client, device_id="tv-a", installation_id="hw-real").status_code == 200, "re-register failed")

    # --- reinstall: same hardware, no token, no parked secret -> a new credential -----------
    FAKE.store.clear()
    reinstalled = register(client, device_id="tv-a", installation_id="hw-real", device_model="Real Model")
    fresh = reinstalled.json().get("device_secret")
    check(bool(fresh), "a reinstalled screen was not re-issued a secret")
    check(bool(fresh) and secret_works(client, "tv-a", fresh), "the re-issued secret does not work")

    # --- legacy: a screen with no installation id on file is still backfilled ---------------
    first = register(client, device_id="tv-old", installation_id="hw-old", device_model="Old TV")
    check(first.status_code == 200, f"legacy register failed: {first.text}")
    old = screen_row("tv-old")
    check(old.installation_id == "hw-old", "the legacy screen was not backfilled")
    check(bool(first.json().get("device_secret")), "the legacy screen was not issued a secret")

    # ...but not once the legacy path is closed: then a stranger cannot claim the blank.
    db = database.SessionLocal()
    blank = models.Screen(organization_id=screen_row("tv-a").organization_id, device_id="tv-blank", status="online")
    db.add(blank)
    db.commit()
    db.close()
    os.environ["ALLOW_LEGACY_DEVICE_AUTH"] = "false"
    try:
        closed = register(client, device_id="tv-blank", installation_id="hw-grab")
        check(closed.json().get("device_secret") is None, "legacy closed: a stranger was issued a secret")
        check(screen_row("tv-blank").installation_id is None, "legacy closed: a stranger claimed the blank identity")
    finally:
        os.environ.pop("ALLOW_LEGACY_DEVICE_AUTH", None)


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
    print("OK - a device id alone neither reads, changes nor takes over a claimed screen")
