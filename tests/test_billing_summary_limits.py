"""Phase 3.7: /api/billing/summary must report the limit actually enforced, not the plan's
raw number.

billing_summary used to return summary.plan.max_screens / max_storage_bytes directly, so a
per-tenant override an admin set via POST /admin/tenants/{id}/grant never showed up on the
tenant's own billing page -- the bars there silently disagreed with what create_placement,
ensure_screen_quota and the upload route actually enforced. Ad slots and clients had no
usage figures at all. Pins:
  * on a plain plan (no override) the summary reports the plan's own numbers
  * a screens/ad-slots/clients/storage override on the tenant wins over the plan
  * ad_slots_used counts only bookings still running (effective_ends_at in the future),
    matching how the admin console and the quota itself count it
  * an unlimited tenant (plan and override both unset/zero) reports None/0, never crashes
"""
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-billingsummary-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-billing-summary-limits")
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


def run() -> None:
    db = database.SessionLocal()
    now = models.utcnow()

    plan = models.Plan(
        name="Growth", slug=f"growth-{uuid.uuid4().hex[:6]}", max_screens=5,
        max_ad_slots=3, max_clients=2, max_storage_bytes=5 * 1024 ** 3, is_active=True,
    )
    db.add(plan)
    db.flush()

    # storage_quota_bytes is a copied column, synced only when a plan is actually applied
    # or granted (_apply_plan) -- not derived live like max_screens/max_ad_slots/max_clients
    # are. Setting it here mirrors what a real purchase on this plan would leave behind.
    org = models.Organization(name="Plain Co", slug=f"plain-{uuid.uuid4().hex[:8]}",
                              status="active", plan_id=plan.id, storage_quota_bytes=plan.max_storage_bytes)
    db.add(org)
    db.flush()
    db.add(models.User(organization_id=org.id, username="plain-owner", email="plain@x.t",
                       hashed_password=get_password_hash("x"), role="owner", is_active=True))

    content = models.Content(organization_id=org.id, type="video", file_url="/uploads/1/a.mp4", name="Ad")
    db.add(content)
    db.flush()

    # One booking still running, one already finished -- only the running one should count.
    db.add(models.AdPlacement(organization_id=org.id, content_id=content.id, advertiser="Running Co",
                              price_paise=100000, starts_at=now, ends_at=now + timedelta(days=10)))
    db.add(models.AdPlacement(organization_id=org.id, content_id=content.id, advertiser="Finished Co",
                              price_paise=100000, starts_at=now - timedelta(days=20), ends_at=now - timedelta(days=1)))
    db.add(models.Client(organization_id=org.id, name="Only Client", client_code="C-1"))
    db.commit()
    org_id = org.id
    db.close()

    client = TestClient(app)
    auth = {"Authorization": f"Bearer {create_access_token({'sub': 'plain-owner'})}"}

    # --- no override: the summary reports the plan's own numbers ----------------------------
    summary = client.get("/api/billing/summary", headers=auth)
    check(summary.status_code == 200, f"billing summary failed: {summary.status_code} {summary.text}")
    body = summary.json()
    check(body["max_screens"] == 5, f"expected the plan's 5 screens, got {body['max_screens']!r}")
    check(body["max_ad_slots"] == 3, f"expected the plan's 3 ad slots, got {body['max_ad_slots']!r}")
    check(body["max_clients"] == 2, f"expected the plan's 2 clients, got {body['max_clients']!r}")
    check(body["storage_quota_bytes"] == 5 * 1024 ** 3, f"expected the plan's storage, got {body['storage_quota_bytes']!r}")
    check(body["ad_slots_used"] == 1, f"only the still-running booking should count, got {body['ad_slots_used']!r}")
    check(body["clients_used"] == 1, f"expected 1 client, got {body['clients_used']!r}")

    # --- an admin override wins over the plan ------------------------------------------------
    db = database.SessionLocal()
    db.query(models.Organization).filter(models.Organization.id == org_id).update({
        "max_screens": 50, "max_ad_slots": 40, "max_clients": 30, "storage_quota_bytes": 9 * 1024 ** 3,
    })
    db.commit()
    db.close()
    overridden = client.get("/api/billing/summary", headers=auth).json()
    check(overridden["max_screens"] == 50, f"an override should beat the plan, got {overridden['max_screens']!r}")
    check(overridden["max_ad_slots"] == 40, f"an ad-slot override should beat the plan, got {overridden['max_ad_slots']!r}")
    check(overridden["max_clients"] == 30, f"a client override should beat the plan, got {overridden['max_clients']!r}")
    check(overridden["storage_quota_bytes"] == 9 * 1024 ** 3,
          f"a storage override should beat the plan, got {overridden['storage_quota_bytes']!r}")

    # --- an unlimited plan (stored as NULL screens, 0 ad slots/clients/storage) reports
    # None/0, not a crash and not the raw stored zero -------------------------------------
    db = database.SessionLocal()
    unlimited_plan = models.Plan(
        name="Everything", slug=f"everything-{uuid.uuid4().hex[:6]}", max_screens=None,
        max_ad_slots=0, max_clients=0, max_storage_bytes=0, is_active=True,
    )
    db.add(unlimited_plan)
    db.flush()
    unlimited_org = models.Organization(name="Unlimited Co", slug=f"unlimited-{uuid.uuid4().hex[:8]}",
                                        status="active", plan_id=unlimited_plan.id, storage_quota_bytes=0)
    db.add(unlimited_org)
    db.flush()
    db.add(models.User(organization_id=unlimited_org.id, username="unlimited-owner", email="unlimited@x.t",
                       hashed_password=get_password_hash("x"), role="owner", is_active=True))
    db.commit()
    db.close()
    unlimited_auth = {"Authorization": f"Bearer {create_access_token({'sub': 'unlimited-owner'})}"}
    unlimited_summary = client.get("/api/billing/summary", headers=unlimited_auth)
    check(unlimited_summary.status_code == 200, f"unlimited-plan summary crashed: {unlimited_summary.status_code}")
    unlimited_body = unlimited_summary.json()
    check(unlimited_body["max_screens"] is None, f"an unlimited plan should report null screens, got {unlimited_body['max_screens']!r}")
    check(unlimited_body["max_ad_slots"] is None, f"an unlimited plan should report null ad slots, got {unlimited_body['max_ad_slots']!r}")
    check(unlimited_body["max_clients"] is None, f"an unlimited plan should report null clients, got {unlimited_body['max_clients']!r}")
    check(unlimited_body["storage_quota_bytes"] == 0, f"an unlimited plan should report 0 storage quota, got {unlimited_body['storage_quota_bytes']!r}")
    check(unlimited_body["ad_slots_used"] == 0, f"a fresh tenant should have no ad slots used, got {unlimited_body['ad_slots_used']!r}")

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Billing summary limits: plan numbers, admin overrides and unlimited all answer correctly")


if __name__ == "__main__":
    run()
