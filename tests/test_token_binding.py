"""A login token belongs to one user row, not to a username.

The token's subject was the username, users are hard-deleted, and a username can be registered
again. So when a user was removed, the next person to register that name -- in ANY tenant --
inherited the old token for the rest of its seven days. Tokens now also carry the user's id.
Pins:
  * a fresh login token carries the user id
  * deleting a user, then registering the same name elsewhere, does not hand over the old token
  * the new user's own token works
  * a token minted before the claim existed is still accepted, until it expires
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-tokenbind-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-token-binding")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import ALGORITHM, create_access_token, get_password_hash, get_secret_key  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def run() -> None:
    db = database.SessionLocal()
    org_a = models.Organization(name="First Co", slug=f"a-{uuid.uuid4().hex[:8]}", status="active")
    org_b = models.Organization(name="Second Co", slug=f"b-{uuid.uuid4().hex[:8]}", status="active")
    db.add_all([org_a, org_b])
    db.flush()
    db.add(models.User(organization_id=org_a.id, username="boss", email="boss@x.t",
                       hashed_password=get_password_hash("boss-password-1"), role="owner", is_active=True))
    alice = models.User(organization_id=org_a.id, username="alice", email="alice@x.t",
                        hashed_password=get_password_hash("alice-password-1"), role="viewer", is_active=True)
    db.add(alice)
    db.flush()
    # SQLite hands the highest row id out again after a delete, so without a later row the
    # re-registered "alice" would get the old alice's id and look like the same user. Postgres
    # sequences never repeat an id; this keeps the test honest about that on SQLite.
    db.add(models.User(organization_id=org_a.id, username="zed", email="zed@x.t",
                       hashed_password=get_password_hash("zed-password-1"), role="viewer", is_active=True))
    db.commit()
    alice_id = alice.id
    org_b_id = org_b.id
    db.close()

    client = TestClient(app)

    def login(username: str, password: str) -> str:
        response = client.post("/api/auth/token", data={"username": username, "password": password})
        assert response.status_code == 200, response.text
        return response.json()["access_token"]

    boss = login("boss", "boss-password-1")
    alice_token = login("alice", "alice-password-1")

    claims = jwt.decode(alice_token, get_secret_key(), algorithms=[ALGORITHM])
    check(claims.get("uid") == alice_id, f"a login token should carry the user id, got {claims}")
    check(client.get("/api/auth/me", headers=bearer(alice_token)).status_code == 200,
          "a fresh login token must work")

    # --- the owner removes alice; someone registers "alice" in another tenant ----------------
    removed = client.delete(f"/api/users/{alice_id}", headers=bearer(boss))
    check(removed.status_code in (200, 204), f"could not remove the user: {removed.status_code} {removed.text}")
    check(client.get("/api/auth/me", headers=bearer(alice_token)).status_code == 401,
          "a removed user's token still worked")

    db = database.SessionLocal()
    db.add(models.User(organization_id=org_b_id, username="alice", email="alice2@x.t",
                       hashed_password=get_password_hash("different-pass-2"), role="owner", is_active=True))
    db.commit()
    db.close()

    check(client.get("/api/auth/me", headers=bearer(alice_token)).status_code == 401,
          "SECURITY: the old alice's token authenticated as the NEW alice in another tenant")
    new_token = login("alice", "different-pass-2")
    me = client.get("/api/auth/me", headers=bearer(new_token))
    check(me.status_code == 200, "the new user's own token should work")

    # --- a token minted before the claim existed keeps working until it expires --------------
    legacy = create_access_token({"sub": "alice"})
    check(client.get("/api/auth/me", headers=bearer(legacy)).status_code == 200,
          "a pre-claim token should still be accepted during the transition")


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
    print("OK - a login token belongs to one user row, not to a reusable username")
