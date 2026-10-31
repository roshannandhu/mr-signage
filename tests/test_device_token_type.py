"""A token minted for something else must never act as a screen -- or as a user.

One key signs every token the server issues. The device check looked only at `sub`, and the
public Google sign-in routes mint tokens whose subject is `device:<id>` for anyone who asks.
Used as a Bearer on /sync, one of those returned the screen's maintenance PIN and the
operator's master PIN. This pins:
  * a screen's own token still works
  * the Google poll and OAuth-state tokens are refused as device credentials
  * a token with no `typ` (minted before this fix) still works until REQUIRE_DEVICE_TOKEN_TYPE
    is switched on, and is refused after
  * a screen's token or a Google-flow token cannot authenticate as a web user
  * a username cannot contain ':' so it can never shadow `device:<id>`
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-devtoken-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-device-token-type")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")
os.environ.pop("REQUIRE_DEVICE_TOKEN_TYPE", None)

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
    tenant = models.Organization(name="Venue", slug=f"v-{uuid.uuid4().hex[:8]}", status="active")
    db.add(tenant)
    db.flush()
    db.add(models.User(organization_id=tenant.id, username="owner", email="ow@x.t",
                       hashed_password=get_password_hash("x"), role="owner", is_active=True))
    # A legacy account whose name collides with a screen's token subject. The API refuses to
    # create one now, but a database that predates that rule may hold one.
    db.add(models.User(organization_id=tenant.id, username="device:tv-a", email="dv@x.t",
                       hashed_password=get_password_hash("x"), role="owner", is_active=True))
    screen = models.Screen(organization_id=tenant.id, device_id="tv-a", status="online",
                           maintenance_pin="1234")
    db.add(screen)
    db.flush()
    from backend.services import issue_device_secret
    secret = issue_device_secret(screen)
    db.commit()
    db.close()
    return secret


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def sync_status(client, token: str) -> int:
    return client.get("/api/screens/tv-a/sync", headers=bearer(token)).status_code


def run() -> None:
    secret = setup()
    client = TestClient(app)

    # --- a screen's own token ------------------------------------------------------------
    own = client.post("/api/screens/auth", json={"device_id": "tv-a", "device_secret": secret})
    check(own.status_code == 200, f"device auth failed: {own.text}")
    device_token = own.json()["access_token"]
    check(sync_status(client, device_token) == 200, "a screen's own token must sync")

    # --- tokens minted for the Google sign-in flow ---------------------------------------
    for kind in ("google_poll", "tv_oauth"):
        flow = create_access_token({"sub": "device:tv-a", "typ": kind})
        check(sync_status(client, flow) == 401, f"a {kind} token was accepted as a device credential")

    # --- the transition: a token with no typ ---------------------------------------------
    untyped = create_access_token({"sub": "device:tv-a"})
    check(sync_status(client, untyped) == 200, "an untyped (pre-fix) device token should still work")
    os.environ["REQUIRE_DEVICE_TOKEN_TYPE"] = "true"
    try:
        check(sync_status(client, untyped) == 401, "an untyped token must be refused once required")
        check(sync_status(client, device_token) == 200, "a typed token must keep working once required")
    finally:
        os.environ.pop("REQUIRE_DEVICE_TOKEN_TYPE", None)

    # --- a web user's token is not a device credential -----------------------------------
    owner_token = create_access_token({"sub": "owner"})
    check(sync_status(client, owner_token) == 401, "a web login token was accepted as a device")

    # --- and none of those tokens is a web login -----------------------------------------
    check(client.get("/api/auth/me", headers=bearer(owner_token)).status_code == 200,
          "a normal login token must still work")
    for label, token in (
        ("a screen's token", device_token),
        ("a google_poll token", create_access_token({"sub": "device:tv-a", "typ": "google_poll"})),
        ("a tv_oauth token", create_access_token({"sub": "device:tv-a", "typ": "tv_oauth"})),
        ("an untyped token for a user named device:tv-a", create_access_token({"sub": "device:tv-a"})),
    ):
        check(client.get("/api/auth/me", headers=bearer(token)).status_code == 401,
              f"{label} authenticated as a web user")

    # --- a username cannot shadow a screen -----------------------------------------------
    created = client.post(
        "/api/users/", headers=bearer(owner_token),
        json={"username": "device:tv-b", "password": "a-long-enough-pass", "role": "viewer"},
    )
    check(created.status_code == 422, f"a username containing ':' was accepted ({created.status_code})")


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
    print("OK - only a screen's own token acts as that screen, and only a login acts as a user")
