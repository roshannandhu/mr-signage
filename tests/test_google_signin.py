"""Signing a TV in with a Google account: who it lets in, and who it must not.

Google authenticates a person. It does not authorise them, and the gap between those two
is the whole of this file. An id_token proves somebody controls a Gmail address; it says
nothing about which workspace they may add a screen to, whether that screen already
belongs to another tenant, or whether their role permits it at all. Every check below
guards a way that gap could be crossed.

Google itself is stubbed. Nothing here needs the network: what is under test is what this
codebase does with an answer from Google, not Google.

Run directly:  python tests/test_google_signin.py
"""

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_DB = "olrac_test_google_signin"
TEMP_DIR = tempfile.TemporaryDirectory(prefix="olrac-google-signin-")


def _database_url() -> str:
    """Postgres when a server is there, SQLite otherwise -- as test_release_rollout does.

    Nothing here is dialect-specific: it is authorisation, a case-insensitive lookup and a
    tenant guard. Falling back to SQLite means these checks still run on a machine with no
    database server, which is exactly where a regression in them would go unnoticed.
    """
    try:
        import psycopg2
        from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

        admin = psycopg2.connect(
            "postgresql://postgres:postgres@localhost:5432/postgres", connect_timeout=3
        )
        admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        admin.cursor().execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        admin.cursor().execute(f"CREATE DATABASE {TEST_DB} OWNER olrac")
        admin.close()
        return f"postgresql://olrac:olrac_password@localhost:5432/{TEST_DB}"
    except Exception:
        return f"sqlite:///{Path(TEMP_DIR.name) / 'google_signin.db'}"


os.environ["DATABASE_URL"] = _database_url()
os.environ["SECRET_KEY"] = "testsecret"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
os.environ["GOOGLE_OAUTH_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
os.environ["GOOGLE_OAUTH_CLIENT_SECRET"] = "test-client-secret"

from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend import google_device  # noqa: E402
from backend.database import Base, get_db  # noqa: E402
from backend.limiter import limiter  # noqa: E402
from backend.main import app  # noqa: E402
from backend.models import Organization, Screen, User  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402

engine = create_engine(os.environ["DATABASE_URL"])
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)

STARTED = {
    "device_code": "GOOGLE-DEVICE-CODE",
    "user_code": "ABCD-EFGH",
    "verification_url": "https://www.google.com/device",
    "interval": 5,
    "expires_in": 1800,
}


def stub_google(monkey: dict):
    """Point the module's two network calls at canned answers."""
    google_device.start = lambda: dict(STARTED)
    google_device.poll = lambda device_code: dict(monkey)


def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()

    acme = Organization(name="Acme", slug="acme")
    other = Organization(name="Other", slug="other")
    db.add_all([acme, other])
    db.commit()
    db.refresh(acme)
    db.refresh(other)

    db.add_all([
        # Mixed case on purpose: Google lowercases what it returns, the profile field does
        # not, and a case-sensitive match would silently refuse a legitimate account.
        User(organization_id=acme.id, username="owner", role="owner", is_active=True,
             email="Owner@Acme.com", hashed_password=get_password_hash("password123")),
        User(organization_id=acme.id, username="viewer", role="viewer", is_active=True,
             email="viewer@acme.com", hashed_password=get_password_hash("password123")),
        User(organization_id=acme.id, username="dormant", role="owner", is_active=False,
             email="dormant@acme.com", hashed_password=get_password_hash("password123")),
        # No email at all, which is the default: UserCreate does not collect one.
        User(organization_id=acme.id, username="nomail", role="owner", is_active=True,
             hashed_password=get_password_hash("password123")),
        User(organization_id=other.id, username="rival", role="owner", is_active=True,
             email="rival@other.com", hashed_password=get_password_hash("password123")),
    ])
    db.commit()
    # Read before the session closes; the instances detach with it.
    ids = (acme.id, other.id)
    db.close()
    return ids


def start(device_id="tv-1", name=None) -> dict:
    # Every case below needs a fresh attempt, so this file makes far more starts in a
    # minute than any real installer would. The per-IP cap is asserted once, deliberately,
    # at the end of run(); here it is noise, so the counter is cleared each time.
    limiter.reset()
    response = client.post(
        "/api/screens/google/start", json={"device_id": device_id, "name": name}
    )
    assert response.status_code == 200, response.text
    return response.json()


def poll(token: str):
    return client.post("/api/screens/google/poll", json={"poll_token": token})


def approved(email: str, verified: bool = True) -> dict:
    return {"status": "ok", "email": email, "email_verified": verified,
            "sub": "1234567890", "name": "Test Person"}


def run() -> None:
    acme_id, other_id = setup_db()

    # --- the display half -------------------------------------------------------------
    stub_google(approved("owner@acme.com"))
    started = start()
    assert started["user_code"] == "ABCD-EFGH"
    assert started["verification_url"] == "https://www.google.com/device"
    assert started["interval"] == 5
    # The TV must never be handed the device_code or anything derived from the secret.
    assert "device_code" not in started
    assert STARTED["device_code"] not in started["poll_token"]

    # --- waiting states pass through, they are not failures ---------------------------
    for waiting in ("pending", "slow_down", "denied", "expired"):
        stub_google({"status": waiting})
        response = poll(start()["poll_token"])
        assert response.status_code == 200, response.text
        assert response.json()["status"] == waiting, waiting
        assert response.json()["screen"] is None

    # --- the happy path ---------------------------------------------------------------
    stub_google(approved("owner@acme.com"))
    token = start(device_id="tv-1", name="Lobby TV")["poll_token"]
    response = poll(token)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "bound"
    assert body["screen"]["name"] == "Lobby TV"
    # The player is told its name and its one-time secret, nothing else: not the maintenance
    # PIN, the hardware identity or the tenant's notes. It reads those from /sync once paired.
    assert set(body["screen"]) == {"name", "status", "device_secret"}, body["screen"]
    assert body["screen"]["device_secret"], "the first bind must hand over the device secret"

    db = TestingSessionLocal()
    bound = db.query(Screen).filter(Screen.device_id == "tv-1").one()
    assert bound.organization_id == acme_id
    # Bound screens go straight to "online". The screen-approval gate this used to
    # assert ("offline" until an owner approved) was dead code: approved_at was written
    # by three routes and read by none, so a "pending" screen synced and played anyway.
    assert bound.status == "online"
    # A code from an earlier /register must not survive the claim: it could still be
    # redeemed by somebody else afterwards.
    assert bound.pair_code is None
    db.close()

    # --- an unverified address proves nothing about who owns it ------------------------
    stub_google(approved("owner@acme.com", verified=False))
    response = poll(start(device_id="tv-2")["poll_token"])
    assert response.status_code == 403, response.text
    assert "verified" in response.json()["detail"]

    # --- Google authenticates; it does not enrol --------------------------------------
    # Any Google address on earth could otherwise bind a screen into somebody's tenant.
    stub_google(approved("stranger@gmail.com"))
    response = poll(start(device_id="tv-3")["poll_token"])
    assert response.status_code == 403, response.text
    assert "No MR Signage account" in response.json()["detail"]

    db = TestingSessionLocal()
    assert db.query(Screen).filter(Screen.device_id == "tv-3").first() is None
    db.close()

    # --- a disabled account is not an account -----------------------------------------
    stub_google(approved("dormant@acme.com"))
    assert poll(start(device_id="tv-4")["poll_token"]).status_code == 403

    # --- role still decides, exactly as it does for the password route -----------------
    stub_google(approved("viewer@acme.com"))
    response = poll(start(device_id="tv-5")["poll_token"])
    assert response.status_code == 403, response.text
    assert "cannot add screens" in response.json()["detail"]

    # --- an account with no email must never be matched by a blank ---------------------
    # `lower(NULL) = ''` is NULL in SQL rather than true, but that is worth pinning: most
    # users have no email, since UserCreate never asks for one.
    stub_google(approved(""))
    assert poll(start(device_id="tv-6")["poll_token"]).status_code == 403

    # --- case-insensitive match, because the stored address is mixed case --------------
    stub_google(approved("OWNER@ACME.COM".lower()))
    assert poll(start(device_id="tv-7")["poll_token"]).json()["status"] == "bound"

    # --- the tenant boundary ----------------------------------------------------------
    # tv-1 belongs to Acme. A rival owner approving on their own phone must not re-home
    # it: the screen would move tenants and go dark for the people who own it.
    stub_google(approved("rival@other.com"))
    response = poll(start(device_id="tv-1")["poll_token"])
    assert response.status_code == 403, response.text

    db = TestingSessionLocal()
    still_acme = db.query(Screen).filter(Screen.device_id == "tv-1").one()
    assert still_acme.organization_id == acme_id, "a screen was re-homed across tenants"
    db.close()

    # --- a binding made by another route is reported, not left pending ----------------
    # The panel signs in through its OWN browser. That is a separate OAuth grant which
    # never approves this device code, so Google says "pending" here forever while the
    # screen is already bound -- the TV showed "Display Connected" and the app kept
    # waiting. Rescuing it was left entirely to an olrac:// deep link the TV browser is
    # free to refuse ("App deeplink blocked"). The poll the player is already running has
    # to notice the binding by itself.
    db = TestingSessionLocal()
    db.add(Screen(device_id="tv-web", organization_id=acme_id, name="Foyer", status="online",
                  maintenance_pin="4321", installation_id="hw-secret-serial"))
    db.commit()
    db.close()

    stub_google({"status": "pending"})
    web = poll(start(device_id="tv-web")["poll_token"])
    assert web.status_code == 200, web.text
    assert web.json()["status"] == "bound", web.json()
    assert web.json()["screen"]["name"] == "Foyer", web.json()
    # This branch needs no Google identity at all -- only a poll token, which /google/start
    # gives to anyone -- so it must tell the caller nothing about the screen beyond its name.
    assert set(web.json()["screen"]) == {"name", "status", "device_secret"}, web.json()
    assert "4321" not in web.text and "hw-secret-serial" not in web.text, web.text
    assert web.json()["screen"]["device_secret"] is None

    # ...and a panel that genuinely is not bound still waits. Reporting every device as
    # bound would send each new TV straight to a player with no playlist.
    stub_google({"status": "pending"})
    assert poll(start(device_id="tv-unbound")["poll_token"]).json()["status"] == "pending"

    # --- poll tokens are not session tokens -------------------------------------------
    # Both are signed with the same key, so a login token would decode cleanly here. The
    # type marker is the only thing standing between them.
    stub_google(approved("owner@acme.com"))
    session_token = create_access_token({"sub": "owner"})
    assert poll(session_token).status_code == 401
    # ...and neither is a poll token carrying no device.
    assert poll(create_access_token({"typ": "google_poll", "dc": "x"})).status_code == 401
    assert poll("not-a-jwt").status_code == 401
    assert poll(jwt.encode({"sub": "device:tv-9", "typ": "google_poll", "dc": "x"},
                           "the-wrong-key", algorithm="HS256")).status_code == 401

    # --- identity token claims --------------------------------------------------------
    # A token minted for a different OAuth client must never bind a screen here.
    foreign = jwt.encode(
        {"iss": "https://accounts.google.com", "aud": "someone-elses-client-id",
         "email": "owner@acme.com", "email_verified": True, "sub": "1"},
        "irrelevant", algorithm="HS256",
    )
    try:
        # _claims now takes the audience to check against, because the dashboard's web
        # OAuth client is a separate Google client from the TV's device client.
        google_device._claims(foreign, audience=os.environ["GOOGLE_OAUTH_CLIENT_ID"])
        raise AssertionError("a token for another OAuth client was accepted")
    except google_device.GoogleError:
        pass

    impostor = jwt.encode(
        {"iss": "https://evil.example.com", "aud": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
         "email": "owner@acme.com", "email_verified": True, "sub": "1"},
        "irrelevant", algorithm="HS256",
    )
    try:
        google_device._claims(impostor, audience=os.environ["GOOGLE_OAUTH_CLIENT_ID"])
        raise AssertionError("a token from another issuer was accepted")
    except google_device.GoogleError:
        pass

    # --- the per-IP cap exists, and is loose enough for a real install -----------------
    # Twenty screens brought up from one NAT address must all get through; the cap is
    # there to stop a script, not an installer.
    limiter.reset()
    stub_google(approved("owner@acme.com"))
    codes = [
        client.post("/api/screens/google/start", json={"device_id": f"burst-{i}"}).status_code
        for i in range(32)
    ]
    assert codes[:20] == [200] * 20, "a twenty-screen install was rate limited"
    assert 429 in codes, "the per-IP cap never engages"

    # --- switched off is a supported state, not a broken one ---------------------------
    # The player reads 503 as "hide the button" rather than presenting one that fails.
    previous = os.environ.pop("GOOGLE_OAUTH_CLIENT_ID")
    try:
        # The cap is checked before the handler runs, so the burst above would otherwise
        # answer 429 here and this case would never be reached.
        limiter.reset()
        assert not google_device.is_configured()
        response = client.post("/api/screens/google/start", json={"device_id": "tv-8"})
        assert response.status_code == 503, response.text
    finally:
        os.environ["GOOGLE_OAUTH_CLIENT_ID"] = previous


if __name__ == "__main__":
    run()
    print("google sign-in: all checks passed")
