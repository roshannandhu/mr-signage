"""The number that says when legacy device login is safe to switch off.

Legacy auth lets a TV call the API knowing only its device id, which is not a secret. It stays
on so older players keep working; it should come off once no TV needs it. Pins:
  * the count is live, claimed screens with no device secret -- not archived ones, not ones
    still waiting to be paired, not ones that have a secret
  * /api/health and the admin Fleet response both report it while the path is open
  * with ALLOW_LEGACY_DEVICE_AUTH=false both say it is closed instead of reporting a count
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-legacycount-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-legacy-count")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")
os.environ.pop("ALLOW_LEGACY_DEVICE_AUTH", None)

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


class FakeRedis:
    async def ping(self):
        return True

    def __getattr__(self, _name):
        async def _noop(*_args, **_kwargs):
            return None

        return _noop


def run() -> None:
    database.get_redis = lambda: FakeRedis()

    db = database.SessionLocal()
    operator = models.Organization(name="MR Signage", slug=f"o-{uuid.uuid4().hex[:8]}", status="active")
    tenant = models.Organization(name="Venue", slug=f"v-{uuid.uuid4().hex[:8]}", status="active")
    db.add_all([operator, tenant])
    db.flush()
    db.add(models.User(organization_id=operator.id, username="root", email="root@x.t",
                       hashed_password=get_password_hash("x"), role="super_admin", is_active=True))
    has_secret = models.Screen(organization_id=tenant.id, device_id="with-secret", status="online")
    db.add(has_secret)
    db.flush()
    from backend.services import issue_device_secret
    issue_device_secret(has_secret)
    db.add_all([
        models.Screen(organization_id=tenant.id, device_id="old-one", status="online"),
        models.Screen(organization_id=tenant.id, device_id="old-two", status="offline"),
        # Not TVs an operator would strand: never paired, and archived.
        models.Screen(organization_id=None, device_id="waiting", status="waiting_pairing"),
        models.Screen(organization_id=tenant.id, device_id="removed:9:gone", status="offline",
                      deleted_at=models.utcnow()),
    ])
    db.commit()
    db.close()

    client = TestClient(app)
    admin = {"Authorization": f"Bearer {create_access_token({'sub': 'root'})}"}

    # --- the public health check no longer exposes it -----------------------------------------
    public = client.get("/api/health").json()
    check("legacy_screens_without_secret" not in public and "host" not in public and "warning" not in public,
          f"the unauthenticated health check should not carry operator details: {sorted(public)}")
    check(client.get("/api/admin/platform-health").status_code == 401,
          "the detailed health report must require a sign-in")

    # --- legacy open: the count is reported ---------------------------------------------------
    health = client.get("/api/admin/platform-health", headers=admin).json()
    check(health.get("legacy_screens_without_secret") == 2,
          f"health should count the 2 live claimed screens with no secret: {health.get('legacy_screens_without_secret')}")
    check("2 screen(s) still hold no device secret" in (health.get("warning") or ""),
          f"the warning should name the count: {health.get('warning')}")

    fleet = client.get("/api/admin/fleet", headers=admin).json()
    check(fleet.get("legacy_device_auth") is True, f"fleet should say legacy auth is open: {fleet.get('legacy_device_auth')}")
    check(fleet.get("screens_without_secret") == 2, f"fleet count wrong: {fleet.get('screens_without_secret')}")

    # --- legacy closed: nothing to report ------------------------------------------------------
    os.environ["ALLOW_LEGACY_DEVICE_AUTH"] = "false"
    try:
        health = client.get("/api/admin/platform-health", headers=admin).json()
        check(health.get("legacy_screens_without_secret") is None, "a closed legacy path should report no count")
        check("ALLOW_LEGACY_DEVICE_AUTH" not in (health.get("warning") or ""), "a closed legacy path should not warn")
        fleet = client.get("/api/admin/fleet", headers=admin).json()
        check(fleet.get("legacy_device_auth") is False, "fleet should say legacy auth is closed")
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
    print("OK - the legacy-login count says exactly when it is safe to switch it off")
