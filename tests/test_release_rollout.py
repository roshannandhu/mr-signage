"""Player releases: who may publish one, and who it reaches.

Covers the defect that made this file necessary. `POST /api/releases/` carried no
authentication dependency at all, and `current_app_version` offers the highest
version_code in that table to every screen in every tenant, so an unauthenticated caller
could publish a build that installed itself across the whole fleet -- silently, on any TV
provisioned as device owner. The digest that should have stopped it was never sent to the
device and was skipped when absent.

Run directly:  python tests/test_release_rollout.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import tempfile

TEST_DB = "olrac_test_release_rollout"
TEMP_DIR = tempfile.TemporaryDirectory(prefix="olrac-release-rollout-")


def _database_url() -> str:
    """Postgres when a server is there, SQLite otherwise.

    Production is Postgres and that is what this should exercise, so it is tried first.
    But nothing under test here is dialect-specific -- it is authorisation, a version
    lookup and a counter -- and the models already run on SQLite in test_feature_parity
    and test_sqlite_utc. Falling back means these checks still run on a machine with no
    database server, which is exactly where a regression in them would otherwise go
    unnoticed until CI.
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
        return f"sqlite:///{Path(TEMP_DIR.name) / 'release_rollout.db'}"


os.environ["DATABASE_URL"] = _database_url()
# Redis is not required: every publish in the request path is wrapped and degrades to a
# warning, which is the same behaviour a screen sees when Redis is briefly unavailable.
os.environ["REDIS_URL"] = "redis://localhost:6379/1"
os.environ["SECRET_KEY"] = "testsecret"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402
from jose import jwt  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend import rollout  # noqa: E402
from backend.database import Base, get_db  # noqa: E402
from backend.main import app  # noqa: E402
from backend.models import AppRelease, Organization, Screen, User, utcnow  # noqa: E402
from backend.routers.auth import get_password_hash  # noqa: E402

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

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
APK = "https://cdn.example.com/mr.apk"


def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    org = Organization(name="Test Org", slug="test-org")
    db.add(org)
    db.commit()
    db.refresh(org)

    db.add_all([
        User(organization_id=org.id, username="owner", role="owner",
             hashed_password=get_password_hash("password123"), is_active=True),
        User(organization_id=org.id, username="root", role="super_admin",
             hashed_password=get_password_hash("password123"), is_active=True),
    ])
    # Already live in the field; screens default to pending. See test_screen_approval.py.
    screen = Screen(name="Panel", organization_id=org.id,
                    device_id="testdev", device_secret_hash="hash", status="online",
                    approved_at=utcnow())
    db.add(screen)
    db.commit()
    db.refresh(screen)
    screen_id = screen.id
    db.close()
    return screen_id


def bearer(username):
    res = client.post("/api/auth/token",
                      data={"username": username, "password": "password123"})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def screen_row(screen_id):
    """Read the screen straight from the database.

    There is no GET /api/screens/{id}; the list endpoint would work but returns the whole
    fleet, and target_version_code is the only field under test here.
    """
    db = TestingSessionLocal()
    try:
        row = db.query(Screen).filter(Screen.id == screen_id).first()
        return {
            "target_version_code": row.target_version_code,
            "update_status": row.update_status,
            "update_failure_count": row.update_failure_count,
        }
    finally:
        db.close()


def device_headers():
    token = jwt.encode({"sub": "device:testdev"}, os.environ["SECRET_KEY"], algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


def publish(headers, version_code, digest=DIGEST_A, url=APK, state=None):
    body = {"version_code": version_code, "version_name": f"1.{version_code}.0",
            "apk_url": url, "sha256": digest}
    if state:
        body["rollout_state"] = state
    return client.post("/api/releases/", json=body, headers=headers)


def run():
    screen_id = setup_db()
    owner = bearer("owner")
    root = bearer("root")
    device = device_headers()

    # ---- 1. Publishing is not open to the internet -------------------------------
    anon = publish({}, 900)
    assert anon.status_code in (401, 403), (
        f"unauthenticated publish returned {anon.status_code}: anyone on the internet "
        "could push an APK to every screen in every tenant"
    )
    assert client.get("/api/releases/").status_code in (401, 403), \
        "the release list must require authentication too"

    # ---- 2. A tenant owner is not a platform operator ----------------------------
    # A release is fleet-wide: one organisation's owner must not be able to publish a
    # build that installs on another organisation's screens.
    denied = publish(owner, 901)
    assert denied.status_code == 403, f"owner publish returned {denied.status_code}"

    # ---- 3. An unpinnable build is rejected outright ------------------------------
    no_digest = client.post("/api/releases/", json={
        "version_code": 902, "version_name": "1.0", "apk_url": APK}, headers=root)
    assert no_digest.status_code == 422, "a release with no sha256 must be refused"

    cleartext = publish(root, 903, url="http://cdn.example.com/mr.apk")
    assert cleartext.status_code == 422, "an http:// apk url must be refused"

    bad_hex = publish(root, 904, digest="nothex" * 10)
    assert bad_hex.status_code == 422, "a malformed digest must be refused"

    # ---- 4. The super admin publishes, and it starts as a draft -------------------
    created = publish(root, 10)
    assert created.status_code == 201, created.text
    assert created.json()["rollout_state"] == "draft", (
        "a new build must not be live on creation, or a canary ring is impossible"
    )

    duplicate = publish(root, 10)
    assert duplicate.status_code == 409, "version_code must stay unique"

    # ---- 5. A draft reaches nobody ------------------------------------------------
    version = client.get("/api/screens/player-version")
    assert version.status_code == 200, (
        f"player-version returned {version.status_code}; it called current_app_version() "
        "with no db argument and raised TypeError on every request"
    )
    assert version.json()["version_code"] != 10, "a draft must not be offered to the fleet"

    sync = client.get("/api/screens/testdev/sync", headers=device)
    assert sync.status_code == 200, sync.text
    assert sync.json()["app_version"]["version_code"] != 10

    # ---- 6. Pinning one screen builds the canary ring -----------------------------
    pin = client.patch(f"/api/screens/{screen_id}",
                       json={"target_version_code": 10}, headers=owner)
    assert pin.status_code == 200, pin.text

    sync = client.get("/api/screens/testdev/sync", headers=device)
    offered = sync.json()["app_version"]
    assert offered["version_code"] == 10, "a pinned screen must receive its canary build"
    assert offered["sha256"] == DIGEST_A, (
        "the digest must reach the device; it was stored but never sent, so the "
        "player's integrity check could never run"
    )

    # ---- 7. Three failed installs abandon the build -------------------------------
    for attempt in (1, 2):
        beat = client.post("/api/screens/heartbeat", headers=device, json={
            "device_id": "testdev", "update_status": "failed", "version_code": 10})
        assert beat.status_code == 200, beat.text
        after = screen_row(screen_id)
        assert after["target_version_code"] == 10, \
            f"rolled back after only {attempt} failure(s)"
        assert after["update_failure_count"] == attempt

    client.post("/api/screens/heartbeat", headers=device, json={
        "device_id": "testdev", "update_status": "failed", "version_code": 10})
    after = screen_row(screen_id)
    assert after["target_version_code"] is None, (
        f"after {rollout.ROLLBACK_THRESHOLD} failures the pin must be dropped, or the "
        "screen re-downloads an APK that cannot install on every heartbeat forever"
    )
    assert after["update_status"] == "rolled_back"

    # The bad build never escaped the ring: it is still a draft, so every unpinned
    # screen is untouched by it.
    assert client.get("/api/screens/player-version").json()["version_code"] != 10

    # ---- 8. Re-pinning starts a fresh count ---------------------------------------
    assert publish(root, 11, digest=DIGEST_B).status_code == 201
    client.patch(f"/api/screens/{screen_id}",
                 json={"target_version_code": 11}, headers=owner)
    client.post("/api/screens/heartbeat", headers=device, json={
        "device_id": "testdev", "update_status": "failed", "version_code": 11})
    after = screen_row(screen_id)
    assert after["target_version_code"] == 11, (
        "the failure count must reset on re-pin, or a new build inherits the previous "
        "build's failures and is abandoned after one attempt"
    )

    # ---- 9. Promotion is what makes a build live ----------------------------------
    assert client.patch("/api/releases/11", json={"rollout_state": "released"},
                        headers=owner).status_code == 403, \
        "promoting to released is as powerful as publishing; owners must not do it"

    promoted = client.patch("/api/releases/11", json={"rollout_state": "released"},
                            headers=root)
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["rollout_state"] == "released"

    version = client.get("/api/screens/player-version").json()
    assert version["version_code"] == 11, "a promoted build must reach unpinned screens"
    assert version["sha256"] == DIGEST_B

    # A legacy row with no digest cannot be promoted: the player would refuse every
    # install, so this would only produce a fleet of failures.
    db = TestingSessionLocal()
    db.add(AppRelease(version_code=12, version_name="1.12.0", apk_url=APK,
                      sha256=None, rollout_state="draft"))
    db.commit()
    db.close()
    legacy = client.patch("/api/releases/12", json={"rollout_state": "released"},
                          headers=root)
    assert legacy.status_code == 422, "an unpinned build must not be promotable"

    assert client.patch("/api/releases/999", json={"rollout_state": "released"},
                        headers=root).status_code == 404


if __name__ == "__main__":
    run()
    print("release rollout: all checks passed")
