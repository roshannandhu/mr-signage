"""The screen cap actually caps: python tests/test_screen_quota.py

A tenant on a 5-screen package could reach 6, two different ways, and neither raised.

  1. /enroll checked the quota only `if not screen:`. The player calls /register on boot,
     which mints a row with status "waiting_pairing" and no organisation -- so /enroll
     FOUND that row, skipped the check, and bound it anyway. Since the count excludes
     waiting_pairing, the screen was uncounted before and counted after: one free screen
     over the cap, repeatable, and reachable in normal operation rather than by a crafted
     request.

  2. The limit was read from Organization.max_screens with a fallback to the package --
     but the first branch returned early on a 0 override, and 0 is what every organisation
     has, because only the admin approve path ever wrote that column while three other
     paths set plan_id without it. The console displayed the same 0 as "unlimited".

Both are quota bypasses on a billed limit, so they are pinned here rather than left to a
manual count.
"""

import os
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEMP_DIR = tempfile.TemporaryDirectory(prefix="mr-quota-test-", ignore_cleanup_errors=True)
DB_PATH = Path(TEMP_DIR.name) / "quota.db"

import psycopg2  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

test_db_name = f"olrac_test_{DB_PATH.stem.replace('-', '_')}"
try:
    conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    conn.cursor().execute(f"DROP DATABASE IF EXISTS {test_db_name}")
    conn.cursor().execute(f"CREATE DATABASE {test_db_name} OWNER olrac")
    conn.close()
except Exception:
    pass
os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{test_db_name}"
os.environ["SECRET_KEY"] = "quota-test-secret-not-for-production"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


CAP = 3


def build_tenant(db, unique, *, on_package: bool):
    """A workspace capped at CAP screens, limited either by its package or by an override.

    Both shapes exist in production and they took different branches of the old code, so
    each is exercised.
    """
    plan = models.Plan(
        name=f"Cap{unique}", slug=f"cap-{unique}", monthly_price_paise=0, yearly_price_paise=0,
        max_screens=CAP, max_storage_bytes=10 ** 9, max_ad_slots=0, feature_flags_json="{}",
        is_active=True,
    )
    db.add(plan)
    db.flush()
    org = models.Organization(
        name=f"Capped {unique}", slug=f"capped-{unique}", status="active",
        plan_id=plan.id,
        # The production shape: a package is set and the override column is left at 0.
        max_screens=0 if on_package else CAP,
    )
    db.add(org)
    db.flush()
    owner = models.User(
        organization_id=org.id, username=f"owner-{unique}", email=f"owner-{unique}@x.com",
        role="owner", is_active=True, hashed_password=get_password_hash("ownerpass1"),
    )
    db.add(owner)
    db.flush()
    return org.id, owner.username


def fill_to_cap(db, org_id, unique):
    """CAP screens already claimed, so the next one is the one over the line."""
    for index in range(CAP):
        db.add(models.Screen(
            organization_id=org_id, device_id=f"existing-{unique}-{index}",
            name=f"Screen {index}", status="online", approved_at=models.utcnow(),
            last_seen=models.utcnow(),
        ))
    db.commit()


def test_effective_limit_prefers_the_override_then_the_package():
    """The derivation itself, before any endpoint uses it."""
    db = database.SessionLocal()
    try:
        unique = uuid.uuid4().hex[:8]
        org_id, _ = build_tenant(db, unique, on_package=True)
        org = db.query(models.Organization).filter(models.Organization.id == org_id).one()
        check(
            org.effective_max_screens == CAP,
            f"a tenant on a {CAP}-screen package reported {org.effective_max_screens}; "
            "max_screens is 0 for every real organisation, so reading it alone means no limit",
        )
        org.max_screens = 99
        db.flush()
        check(org.effective_max_screens == 99, "an explicit override must beat the package")
        org.max_screens = 0
        db.flush()

        # A separate organisation with no package at all, rather than clearing plan_id on
        # the one above: `plan` is a loaded relationship, and expiring it to make the
        # property re-read was order-dependent enough to pass alone and fail in the suite.
        # Building the case outright has no such coupling.
        unplanned = models.Organization(
            name=f"Unplanned {unique}", slug=f"unplanned-{unique}", status="active",
            plan_id=None, max_screens=0,
        )
        db.add(unplanned)
        db.flush()
        check(
            unplanned.effective_max_screens is None,
            "no package and no override must be unlimited (None), not a limit of "
            f"{unplanned.effective_max_screens}",
        )
    finally:
        db.rollback()
        db.close()


def test_pairing_stops_at_the_cap(client):
    for on_package in (True, False):
        db = database.SessionLocal()
        unique = uuid.uuid4().hex[:8]
        org_id, username = build_tenant(db, unique, on_package=on_package)
        fill_to_cap(db, org_id, unique)
        db.close()

        headers = {"Authorization": f"Bearer {create_access_token({'sub': username})}"}
        device = f"over-{unique}"
        registered = client.post("/api/screens/register", json={"device_id": device})
        code = registered.json().get("pair_code")
        paired = client.post("/api/screens/pair", headers=headers, json={"pair_code": code, "name": "One too many"})
        shape = "package" if on_package else "override"
        check(
            paired.status_code == 409,
            f"[{shape}] pairing screen {CAP + 1} of {CAP} returned {paired.status_code}, not 409",
        )


def test_enroll_cannot_be_walked_past_the_cap(client):
    """The bypass: /register first, then /enroll finds the row and skips the check."""
    db = database.SessionLocal()
    unique = uuid.uuid4().hex[:8]
    org_id, _ = build_tenant(db, unique, on_package=True)
    fill_to_cap(db, org_id, unique)
    token_value = f"tok-{unique}"
    db.add(models.EnrollmentToken(
        organization_id=org_id, token=token_value, description="quota test", is_active=True,
        expires_at=models.utcnow().replace(year=models.utcnow().year + 1),
        max_uses=10, use_count=0,
    ))
    db.commit()
    db.close()

    device = f"enroll-{unique}"
    # Exactly what the player does on boot, and what made the bypass reachable normally.
    first = client.post("/api/screens/register", json={"device_id": device})
    check(first.status_code == 200, f"register failed: {first.text}")

    enrolled = client.post("/api/screens/enroll", json={"device_id": device, "enrollment_token": token_value})
    check(
        enrolled.status_code == 409,
        f"/enroll bound screen {CAP + 1} of {CAP} with status {enrolled.status_code}: "
        "registering first walks the device past the cap",
    )

    db = database.SessionLocal()
    try:
        counted = db.query(models.Screen).filter(
            models.Screen.organization_id == org_id,
            models.Screen.deleted_at.is_(None),
            models.Screen.status != "waiting_pairing",
        ).count()
        check(counted == CAP, f"organisation holds {counted} screens against a cap of {CAP}")
    finally:
        db.close()


if __name__ == "__main__":
    client = TestClient(app)
    client.__enter__()
    try:
        test_effective_limit_prefers_the_override_then_the_package()
        test_pairing_stops_at_the_cap(client)
        test_enroll_cannot_be_walked_past_the_cap(client)
    finally:
        client.__exit__(None, None, None)

    if failures:
        print("SCREEN QUOTA FAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("screen quota: package and override limits both hold, and /enroll cannot be walked past")
