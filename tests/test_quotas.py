"""Plan-limit enforcement check: python tests/test_quotas.py

Covers GOAL.md T6.1 (screen limit) and T6.2 (storage quota). Both limits are
enforced at the action that consumes them, so this drives the real endpoints
rather than asserting on the plan rows.
"""

import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEMP_DIR = tempfile.TemporaryDirectory(prefix="mr-quota-test-", ignore_cleanup_errors=True)
DB_PATH = Path(TEMP_DIR.name) / "quota.db"
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
test_db_name = f"olrac_test_{DB_PATH.stem.replace('-', '_')}"
try:
    conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    conn.cursor().execute(f"DROP DATABASE IF EXISTS {test_db_name}")
    conn.cursor().execute(f"CREATE DATABASE {test_db_name} OWNER olrac")
    conn.close()
except Exception as e:
    pass
os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{test_db_name}"
os.environ["SECRET_KEY"] = "quota-test-secret-key-not-for-production"
os.environ["INITIAL_ADMIN_USERNAME"] = "quota-owner"
os.environ["INITIAL_ADMIN_PASSWORD"] = "quota-password-123"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402

# Build the schema explicitly. backend.main used to do this as an import side effect,
# so importing the app silently wrote to whatever DATABASE_URL pointed at; it now runs
# in the lifespan, which a bare TestClient(app) never starts. Each isolated script owns
# its own database anyway, so creating it here is the honest version of what was
# happening implicitly before.
from backend import database as _bootstrap_db, models as _bootstrap_models
_bootstrap_models.Base.metadata.create_all(bind=_bootstrap_db.engine)


def auth_header(client: TestClient) -> dict[str, str]:
    response = client.post(
        "/api/auth/token",
        data={"username": "quota-owner", "password": "quota-password-123"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def pair_one_screen(client: TestClient, headers: dict, device_id: str):
    """Register a TV then pair it as the admin. Returns the pair response."""
    registered = client.post("/api/screens/register", json={"device_id": device_id})
    assert registered.status_code == 200, registered.text
    code = registered.json()["pair_code"]
    return client.post("/api/screens/pair", headers=headers, json={"pair_code": code})


def run() -> None:
    failures: list[str] = []

    with TestClient(app) as client:
        headers = auth_header(client)

        db = database.SessionLocal()
        try:
            owner = db.query(models.User).filter(models.User.username == "quota-owner").one()
            org = db.query(models.Organization).filter(
                models.Organization.id == owner.organization_id
            ).one()
            plan = db.query(models.Plan).filter(models.Plan.id == org.plan_id).first()
            assert plan is not None, "organisation has no plan; billing bootstrap did not run"
            max_screens = plan.max_screens
            # Shrink the storage quota so the upload test does not need a huge file.
            org.storage_quota_bytes = 1024
            db.commit()
        finally:
            db.close()

        # --- T6.1: pairing beyond max_screens is refused -------------------
        for index in range(max_screens):
            response = pair_one_screen(client, headers, f"quota-device-{index}")
            if response.status_code != 200:
                failures.append(
                    f"pairing screen {index + 1}/{max_screens} failed early: "
                    f"{response.status_code} {response.text[:120]}"
                )

        over = pair_one_screen(client, headers, "quota-device-overflow")
        if over.status_code != 409:
            failures.append(
                f"T6.1: pairing screen {max_screens + 1} returned {over.status_code}, expected 409"
            )
        elif "Upgrade" not in over.json().get("detail", ""):
            failures.append("T6.1: limit message does not mention upgrading")

        # Signing in on the TV binds a screen too, so it has to respect the same ceiling —
        # otherwise it is a quota bypass: unlimited screens on a limited plan.
        client.post("/api/screens/register", json={"device_id": "quota-signin-overflow"})
        signin_over = client.post(
            "/api/screens/sign-in",
            json={
                "username": "quota-owner",
                "password": "quota-password-123",
                "device_id": "quota-signin-overflow",
            },
        )
        if signin_over.status_code != 409:
            failures.append(
                f"T6.1: TV sign-in past the screen limit returned {signin_over.status_code}, expected 409"
            )
        elif "Upgrade" not in signin_over.json().get("detail", ""):
            failures.append("T6.1: sign-in limit message does not mention upgrading")

        # --- T6.2: upload beyond storage quota is refused ------------------
        oversized = io.BytesIO(b"x" * 4096)  # quota was set to 1024 bytes
        response = client.post(
            "/api/content/upload",
            headers=headers,
            files={"file": ("big.png", oversized, "image/png")},
            data={"name": "Oversized asset"},
        )
        if response.status_code != 413:
            failures.append(
                f"T6.2: oversized upload returned {response.status_code}, expected 413 "
                f"body={response.text[:120]}"
            )

        # A file that fits must still succeed — the guard must not block everything.
        small = io.BytesIO(b"x" * 64)
        ok = client.post(
            "/api/content/upload",
            headers=headers,
            files={"file": ("small.png", small, "image/png")},
            data={"name": "Small asset"},
        )
        if ok.status_code != 201:
            failures.append(
                f"T6.2: in-quota upload was wrongly rejected: {ok.status_code} {ok.text[:120]}"
            )

        # --- Raising the quota must immediately allow the upload (T6.3) ----
        db = database.SessionLocal()
        try:
            owner = db.query(models.User).filter(models.User.username == "quota-owner").one()
            org = db.query(models.Organization).filter(
                models.Organization.id == owner.organization_id
            ).one()
            org.storage_quota_bytes = 10 * 1024 * 1024
            db.commit()
        finally:
            db.close()

        retry = client.post(
            "/api/content/upload",
            headers=headers,
            files={"file": ("big.png", io.BytesIO(b"x" * 4096), "image/png")},
            data={"name": "Now allowed"},
        )
        if retry.status_code != 201:
            failures.append(
                f"T6.3: upload still refused after quota increase: {retry.status_code} {retry.text[:120]}"
            )

    if failures:
        print("QUOTA ENFORCEMENT FAILURES:")
        for line in failures:
            print("  -", line)
        raise SystemExit(1)
    print("Quota enforcement passed: screen limit and storage quota both enforced")


# No test_* wrapper here on purpose — see conftest.py. This file is collected
# as a subprocess so it owns its database engine.
if __name__ == "__main__":
    run()
