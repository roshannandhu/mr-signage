"""-1 means "unlimited" on every plan limit, and is stored in the way each column reads it.

create_plan and update_plan wrote the request straight onto the row, so a package saved with
"unlimited" screens held -1 and `screen_count >= -1` refused every TV on it; -1 storage became a
quota of -1 bytes that refused every upload. A tenant on an unlimited package also made
/api/billing/summary answer 500 (max_screens was typed int but is NULL for unlimited). Pins:
  * saving a plan with -1 stores NULL screens and 0 for ad slots / clients / storage
  * 0 screens stays a real limit of zero, and a numeric limit still binds
  * a tenant on an unlimited plan can add screens, and the billing summary answers
  * a grant that touches only clients does not turn a tenant with no package into zero screens
  * a -1 left on a row by older code reads as unlimited rather than refusing every screen
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-planlimits-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-plan-limits")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402
from backend.routers.screens import ensure_screen_quota  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def refuses(org_id: int) -> bool:
    db = database.SessionLocal()
    try:
        ensure_screen_quota(db, org_id, "add another screen")
        return False
    except HTTPException:
        return True
    finally:
        db.close()


def run() -> None:
    db = database.SessionLocal()
    operator = models.Organization(name="MR Signage", slug=f"o-{uuid.uuid4().hex[:8]}", status="active")
    db.add(operator)
    db.flush()
    db.add(models.User(organization_id=operator.id, username="root", email="root@x.t",
                       hashed_password=get_password_hash("x"), role="super_admin", is_active=True))
    db.commit()
    db.close()

    client = TestClient(app)
    admin = {"Authorization": f"Bearer {create_access_token({'sub': 'root'})}"}

    def new_tenant(name, plan_id=None, screens=0):
        session = database.SessionLocal()
        org = models.Organization(name=name, slug=f"t-{uuid.uuid4().hex[:8]}", status="active", plan_id=plan_id)
        session.add(org)
        session.flush()
        session.add(models.User(organization_id=org.id, username=f"owner-{org.id}", email=f"o{org.id}@x.t",
                                hashed_password=get_password_hash("x"), role="owner", is_active=True))
        for index in range(screens):
            session.add(models.Screen(organization_id=org.id, device_id=f"{name}-{index}", status="online"))
        session.commit()
        org_id = org.id
        session.close()
        return org_id

    # --- saving "unlimited" stores NULL / 0, never -1 ---------------------------------------
    created = client.post("/api/admin/plans", headers=admin, json={
        "name": "Everything", "slug": f"all-{uuid.uuid4().hex[:6]}", "max_screens": -1,
        "max_clients": -1, "max_storage_bytes": -1, "max_ad_slots": -1,
    })
    check(created.status_code == 201, f"creating an unlimited plan failed: {created.status_code} {created.text}")
    unlimited_id = created.json()["id"]
    body = created.json()
    check(body["max_screens"] is None, f"unlimited screens should be reported as null, got {body['max_screens']!r}")
    session = database.SessionLocal()
    row = session.query(models.Plan).filter(models.Plan.id == unlimited_id).one()
    check(row.max_screens is None, f"unlimited screens stored as {row.max_screens!r}")
    check((row.max_clients, row.max_storage_bytes, row.max_ad_slots) == (0, 0, 0),
          f"unlimited clients/storage/ads stored as {(row.max_clients, row.max_storage_bytes, row.max_ad_slots)}")
    session.close()

    # --- zero is still a real limit of zero, and a number still binds -----------------------
    zero = client.post("/api/admin/plans", headers=admin, json={
        "name": "Nothing", "slug": f"zero-{uuid.uuid4().hex[:6]}", "max_screens": 0}).json()["id"]
    five = client.post("/api/admin/plans", headers=admin, json={
        "name": "Five", "slug": f"five-{uuid.uuid4().hex[:6]}", "max_screens": 5}).json()["id"]
    check(refuses(new_tenant("zero-co", zero)), "a package granting zero screens should refuse a screen")
    check(refuses(new_tenant("five-co", five, screens=5)), "a tenant at its 5-screen cap should be refused")
    check(not refuses(new_tenant("four-co", five, screens=4)), "a tenant under its cap should be allowed")

    # --- an unlimited package never refuses ------------------------------------------------------
    big = new_tenant("big-co", unlimited_id, screens=40)
    check(not refuses(big), "SECURITY/BILLING: a tenant on an unlimited package was refused a screen")

    # --- editing a plan to unlimited ----------------------------------------------------------------
    patched = client.patch(f"/api/admin/plans/{five}", headers=admin, json={"max_screens": -1})
    check(patched.status_code == 200 and patched.json()["max_screens"] is None,
          f"patching a plan to unlimited failed: {patched.status_code} {patched.text}")
    check(not refuses(new_tenant("grown-co", five, screens=12)), "a plan edited to unlimited still refused a screen")

    # --- the billing summary answers for a tenant on an unlimited package -------------------------
    summary = client.get("/api/billing/summary", headers={
        "Authorization": f"Bearer {create_access_token({'sub': f'owner-{big}'})}"})
    check(summary.status_code == 200, f"billing summary crashed for an unlimited tenant: {summary.status_code}")

    # --- a grant touching only clients must not zero a tenant that has no package ------------------
    bare = new_tenant("bare-co", None, screens=3)
    granted = client.post(f"/api/admin/tenants/{bare}/grant", headers=admin, json={"max_clients": 5})
    check(granted.status_code == 200, f"grant failed: {granted.status_code} {granted.text}")
    check(not refuses(bare), "a grant on clients turned a tenant with no package into zero screens")

    # --- a -1 left on a row by older code is read as unlimited --------------------------------------
    session = database.SessionLocal()
    legacy = models.Plan(name="Legacy", slug=f"legacy-{uuid.uuid4().hex[:6]}", max_screens=-1,
                         max_storage_bytes=10 * 1024 ** 3, is_active=True)
    session.add(legacy)
    session.commit()
    legacy_id = legacy.id
    session.close()
    check(not refuses(new_tenant("legacy-co", legacy_id, screens=9)), "a stored -1 refused every screen")


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
    print("OK - -1 means unlimited on every plan limit, and no unlimited tenant is refused")
