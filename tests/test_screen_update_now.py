"""An operator can make one TV look for its update now.

A screen that failed an update, was offline when the build went out, or had its prompt
dismissed has already saved a sync marker newer than the release -- so it is told 204 on
every sync and never offered that build again. "Update now" has to break that for exactly
one screen, on every player version, and only a platform operator may do it.
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-update-now-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-update-now")
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
    screen = models.Screen(organization_id=tenant.id, device_id="tv-upd", status="online",
                           app_version="1.0.4", update_status="failed", update_failure_count=2)
    db.add(screen)
    for code in (5, 6):
        db.add(models.AppRelease(version_code=code, version_name=f"1.0.{code}", rollout_state="released",
                                 apk_url=f"https://example.test/{code}.apk", sha256="a" * 64))
    db.flush()
    from backend.services import issue_device_secret
    secret = issue_device_secret(screen)
    screen_id = screen.id
    db.commit()
    db.close()
    return screen_id, secret


def run() -> None:
    screen_id, secret = setup()
    client = TestClient(app)
    admin = {"Authorization": f"Bearer {create_access_token({'sub': 'operator'})}"}
    owner = {"Authorization": f"Bearer {create_access_token({'sub': 'owner'})}"}
    token = client.post("/api/screens/auth", json={"device_id": "tv-upd", "device_secret": secret}).json()["access_token"]
    device = {"Authorization": f"Bearer {token}"}

    # The stuck state: the screen has synced since the release and is now told nothing.
    first = client.get("/api/screens/tv-upd/sync", headers=device)
    marker = first.json()["playlist_updated_at"]
    stuck = client.get("/api/screens/tv-upd/sync", params={"since": marker}, headers=device)
    check(stuck.status_code == 204, f"precondition: expected a settled 204, got {stuck.status_code}")

    # --- only the platform operator -------------------------------------------------------
    check(client.post(f"/api/releases/screens/{screen_id}/update", headers=owner).status_code == 403,
          "a tenant owner could push an update to a screen")
    check(client.post("/api/releases/screens/999999/update", headers=admin).status_code == 404,
          "an unknown screen was not a 404")
    check(client.post(f"/api/releases/screens/{screen_id}/update", headers=admin,
                      json={"version_code": 42}).status_code == 422,
          "an unknown build was accepted as a pin")

    # --- update to the latest --------------------------------------------------------------
    pressed = client.post(f"/api/releases/screens/{screen_id}/update", headers=admin)
    check(pressed.status_code == 200, f"update now failed: {pressed.status_code} {pressed.text}")
    body = pressed.json()
    check(body["offered_version_code"] == 6, f"did not offer the latest build: {body}")
    check(body["already_current"] is False, f"a 1.0.4 screen was reported current: {body}")

    freed = client.get("/api/screens/tv-upd/sync", params={"since": marker}, headers=device)
    check(freed.status_code == 200, f"the screen was still told 204 after Update now ({freed.status_code})")
    if freed.status_code == 200:
        sync = freed.json()
        check((sync.get("app_version") or {}).get("version_code") == 6,
              f"the sync did not carry the offer: {sync.get('app_version')}")
        check(sync.get("pending_command") == "check_update",
              f"the player was not told to check now: {sync.get('pending_command')!r}")

    db = database.SessionLocal()
    row = db.get(models.Screen, screen_id)
    check(row.update_failure_count == 0 and row.update_status is None,
          f"a screen that had given up was not allowed to try again: {row.update_status} x{row.update_failure_count}")
    db.close()

    # --- pin a specific build --------------------------------------------------------------
    pinned = client.post(f"/api/releases/screens/{screen_id}/update", headers=admin, json={"version_code": 5})
    check(pinned.status_code == 200 and pinned.json()["offered_version_code"] == 5,
          f"pinning to a build did not offer that build: {pinned.text}")
    latest_again = client.post(f"/api/releases/screens/{screen_id}/update", headers=admin)
    check(latest_again.json()["target_version_code"] is None,
          "updating to latest left the old pin in place")


if __name__ == "__main__":
    try:
        run()
        if FAILURES:
            print("UPDATE NOW FAILURES:")
            for failure in FAILURES:
                print("  -", failure)
            raise SystemExit(1)
        print("OK - Update now breaks a settled screen's 204 and only an operator can press it")
    finally:
        database.engine.dispose()
        _db_file.unlink(missing_ok=True)
