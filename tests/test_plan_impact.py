"""Plan impact pre-save check.

POST /api/admin/plans/{id}/impact counts:
* Active tenants on this plan
* Tenants exceeding proposed limits (unless they have an override)
* Features being removed from the plan that tenants currently have
Pure read, zero writes.
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-planimpact-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-plan-impact")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402
import json  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run():
    client = TestClient(app)
    db = database.SessionLocal()

    # Seed super admin
    admin_org = models.Organization(name="Platform Ops", slug="platform-ops", status="active")
    db.add(admin_org)
    db.flush()
    admin_user = models.User(
        organization_id=admin_org.id,
        username="impact-admin",
        hashed_password=get_password_hash("pw"),
        role="super_admin",
        is_active=True,
    )
    db.add(admin_user)
    db.flush()

    # Seed plan with 5 max screens and 2 features
    plan = models.Plan(
        name="Pro Network",
        slug=f"pro-{uuid.uuid4().hex[:6]}",
        monthly_price_paise=100000,
        yearly_price_paise=1000000,
        price_paise=100000,
        duration_days=30,
        max_screens=5,
        max_clients=10,
        max_ad_slots=20,
        max_storage_bytes=10 * 1024 * 1024 * 1024,
        feature_flags_json=json.dumps({"scheduling": True, "kiosk_lock": True}),
        is_active=True,
    )
    db.add(plan)
    db.flush()

    # Seed Tenant 1 on plan: 3 screens (within 2, within 5)
    t1 = models.Organization(name="Tenant One", slug="t1", status="active", plan_id=plan.id)
    db.add(t1)
    db.flush()
    for i in range(3):
        db.add(models.Screen(name=f"s1-{i}", device_id=f"dev1-{i}", organization_id=t1.id))

    # Seed Tenant 2 on plan: 4 screens (exceeds proposed 2)
    t2 = models.Organization(name="Tenant Two", slug="t2", status="active", plan_id=plan.id)
    db.add(t2)
    db.flush()
    for i in range(4):
        db.add(models.Screen(name=f"s2-{i}", device_id=f"dev2-{i}", organization_id=t2.id))

    # Seed Tenant 3 on plan: 6 screens but has max_screens override of 10!
    t3 = models.Organization(name="Tenant Three", slug="t3", status="active", plan_id=plan.id, max_screens=10)
    db.add(t3)
    db.flush()
    for i in range(6):
        db.add(models.Screen(name=f"s3-{i}", device_id=f"dev3-{i}", organization_id=t3.id))

    db.commit()

    token = login_token(admin_user)
    headers = {"Authorization": f"Bearer {token}"}

    # Test 1: Check canonical features list
    res = client.get("/api/admin/features", headers=headers)
    check(res.status_code == 200, f"Expected 200 from /features, got {res.status_code}")
    features_list = res.json()
    keys = [f["key"] for f in features_list]
    check("scheduling" in keys, "scheduling feature missing")
    check("kiosk_lock" in keys, "kiosk_lock feature missing")

    # Test 2: Check plan impact proposing lowering max_screens to 2, and removing kiosk_lock
    impact_payload = {
        "max_screens": 2,
        "max_ad_slots": 20,
        "feature_flags": {"scheduling": True, "kiosk_lock": False},
    }
    res_impact = client.post(f"/api/admin/plans/{plan.id}/impact", json=impact_payload, headers=headers)
    check(res_impact.status_code == 200, f"Expected 200 from /impact, got {res_impact.status_code}: {res_impact.text}")
    data = res_impact.json()

    # Tenants count: all 3 tenants on plan
    check(data["tenants_count"] == 3, f"Expected tenants_count=3, got {data.get('tenants_count')}")

    # Exceeding screens:
    # T1 has 3 screens > 2 (exceeds)
    # T2 has 4 screens > 2 (exceeds)
    # T3 has 6 screens, BUT org.max_screens=10 override -> exempt!
    # Total exceeding = 2
    check(data["exceeding_screens"] == 2, f"Expected exceeding_screens=2, got {data.get('exceeding_screens')}")

    # Lost features: kiosk_lock removed
    check("kiosk_lock" in data["lost_features"], f"Expected kiosk_lock in lost_features, got {data.get('lost_features')}")
    check("scheduling" not in data["lost_features"], f"scheduling should not be in lost_features")

    db.close()
    if FAILURES:
        print("FAILURES:\n" + "\n".join(FAILURES), file=sys.stderr)
        sys.exit(1)
    print("Plan impact pre-save check: tenants counted, overrides respected, lost features identified")


if __name__ == "__main__":
    run()

