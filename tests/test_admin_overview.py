"""Mission Control's numbers are correct, and the query count stays flat as data grows.

GET /api/admin/overview replaces nothing (it's new), but it is exactly the kind of
endpoint the original survey warned about -- a per-tenant-ish aggregate a naive version
would loop over. Pins:
  * every number matches hand-seeded data: tenant counts by status, online/offline
    screens, open alerts by severity, online vs offline plays, revenue, a renewal due
    inside 14 days (and one outside it, excluded), pending approvals/custom requests
  * the query count for one range is the same whether there are 2 tenants or 20 -- no
    per-tenant loop crept in
  * the in-process cache answers a second call within CACHE_SECONDS without hitting the
    database again, and reports cached=true
  * inbox-counts matches a hand-count of the same data
  * a non-admin is refused
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-adminoverview-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-admin-overview")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers import admin_overview  # noqa: E402
from backend.routers.auth import get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def seed_tenant(db, plan_id: int, name: str, status: str = "active") -> models.Organization:
    org = models.Organization(name=name, slug=f"ov-{uuid.uuid4().hex[:8]}", status=status,
                              plan_id=plan_id if status != "pending_approval" else None)
    db.add(org)
    db.flush()
    return org


def run() -> None:
    client = TestClient(app)
    db = database.SessionLocal()
    now = models.utcnow()

    plan = models.Plan(name="P", slug=f"p-{uuid.uuid4().hex[:6]}", feature_flags_json="{}",
                       is_active=True, max_storage_bytes=10 * 1024 ** 3,
                       monthly_price_paise=50000, yearly_price_paise=0, price_paise=0)
    db.add(plan)
    db.flush()

    house = seed_tenant(db, plan.id, "House")
    active1 = seed_tenant(db, plan.id, "Active One")
    active2 = seed_tenant(db, plan.id, "Active Two")
    pending = seed_tenant(db, plan.id, "Pending Co", status="pending_approval")
    suspended = seed_tenant(db, plan.id, "Suspended Co", status="suspended")
    db.flush()

    admin = models.User(organization_id=house.id, username="ov-admin", email="ov-admin@x.t",
                        hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    non_admin = models.User(organization_id=active1.id, username="ov-owner", email="ov-owner@x.t",
                            hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add_all([admin, non_admin])

    db.add_all([
        models.Screen(name="online", device_id=uuid.uuid4().hex, organization_id=active1.id,
                     status="online", last_seen=now),
        models.Screen(name="online-2", device_id=uuid.uuid4().hex, organization_id=active1.id,
                     status="online", last_seen=now - timedelta(seconds=10)),
        models.Screen(name="offline-25h", device_id=uuid.uuid4().hex, organization_id=active2.id,
                     status="online", last_seen=now - timedelta(hours=25)),
    ])

    db.add_all([
        models.Alert(organization_id=active1.id, kind="screen_offline", severity="critical",
                    title="x", dedupe_key=f"k1-{uuid.uuid4().hex}", raised_at=now),
        models.Alert(organization_id=active2.id, kind="screen_offline", severity="warning",
                    title="x", dedupe_key=f"k2-{uuid.uuid4().hex}", raised_at=now),
        # Resolved -- must NOT be counted as open.
        models.Alert(organization_id=active1.id, kind="screen_offline", severity="critical",
                    title="x", dedupe_key=f"k3-{uuid.uuid4().hex}", raised_at=now, resolved_at=now),
    ])

    db.add(models.PlayLogHourlyRollup(
        organization_id=active1.id, screen_id=1, date_hour=now.replace(minute=0, second=0, microsecond=0),
        total_plays=100, completed_plays=90, partial_plays=5, error_plays=5, duration_ms=0, offline_plays=30,
    ))

    db.add(models.PlatformPayment(
        organization_id=active1.id, kind="plan", amount_paise=50000, currency="INR",
        provider="mock", status="captured", paid_at=now,
    ))
    db.add(models.PlatformPayment(
        organization_id=active1.id, kind="plan", amount_paise=10000, currency="INR",
        provider="mock", status="failed", paid_at=now,
    ))

    db.add(models.Subscription(organization_id=active1.id, plan_id=plan.id, status="active",
                               current_period_end=now + timedelta(days=5)))  # due soon
    db.add(models.Subscription(organization_id=active2.id, plan_id=plan.id, status="active",
                               current_period_end=now + timedelta(days=40)))  # not due soon

    db.add(models.CustomPlanRequest(organization_id=active1.id, status="requested"))

    db.commit()
    plan_id = plan.id
    admin_auth = {"Authorization": f"Bearer {login_token(admin)}"}
    non_admin_auth = {"Authorization": f"Bearer {login_token(non_admin)}"}
    db.close()

    admin_overview.forget_overview_cache()

    refused = client.get("/api/admin/overview", headers=non_admin_auth)
    check(refused.status_code == 403, f"a non-admin should be refused, got {refused.status_code}")

    response = client.get("/api/admin/overview?range=30d", headers=admin_auth)
    check(response.status_code == 200, f"overview failed: {response.status_code} {response.text[:300]}")
    body = response.json()

    check(body.get("cached") is False, "the first call should not be served from cache")
    check(body.get("tenants_active") == 3, f"expected 3 active (house + 2), got {body.get('tenants_active')}")
    check(body.get("tenants_pending") == 1, f"expected 1 pending, got {body.get('tenants_pending')}")
    check(body.get("tenants_suspended") == 1, f"expected 1 suspended, got {body.get('tenants_suspended')}")
    check(body.get("screens_total") == 3, f"expected 3 real screens, got {body.get('screens_total')}")
    check(body.get("screens_online") == 2, f"expected 2 online (within the shared threshold), got {body.get('screens_online')}")
    check(body.get("screens_offline_24h") == 1, f"expected 1 offline >24h, got {body.get('screens_offline_24h')}")
    check(body.get("alerts_critical_open") == 1, f"a resolved critical alert must not count: got {body.get('alerts_critical_open')}")
    check(body.get("alerts_warning_open") == 1, f"expected 1 open warning, got {body.get('alerts_warning_open')}")
    check(body.get("revenue_paise_total") == 50000, f"only captured payments should count: got {body.get('revenue_paise_total')}")
    check(body.get("renewals_due_14d_count") == 1, f"only the 5-day-out renewal should count: got {body.get('renewals_due_14d_count')}")
    check(body.get("renewals_due_14d_value_paise") == 50000, f"renewal value should be the plan's monthly price: got {body.get('renewals_due_14d_value_paise')}")
    check(body.get("pending_approvals") == 1, f"expected 1 pending approval, got {body.get('pending_approvals')}")
    check(body.get("pending_custom_requests") == 1, f"expected 1 custom request, got {body.get('pending_custom_requests')}")

    online_total = sum(p["value"] for p in body.get("plays_online_by_day", []))
    offline_total = sum(p["value"] for p in body.get("plays_offline_by_day", []))
    check(online_total == 70, f"online plays should be total-offline (100-30): got {online_total}")
    check(offline_total == 30, f"offline plays wrong: got {offline_total}")

    # --- cache: a second call within CACHE_SECONDS must not re-hit the database ---------------
    statements = {"n": 0}

    def count(*_a, **_k):
        statements["n"] += 1

    event.listen(database.engine, "before_cursor_execute", count)
    try:
        cached_response = client.get("/api/admin/overview?range=30d", headers=admin_auth)
    finally:
        event.remove(database.engine, "before_cursor_execute", count)
    check(cached_response.json().get("cached") is True, "a second call within the cache window should say cached=true")
    # <=1: auth still runs its own user lookup. The thing under test is that it does not
    # rebuild the overview itself, which at the time this was written was ~15 queries.
    check(statements["n"] <= 1, f"a cached response should not rebuild the overview, ran {statements['n']} queries")

    # --- query budget: flat regardless of tenant count -----------------------------------------
    def queries_for_overview(range_key: str) -> int:
        admin_overview.forget_overview_cache()
        statements["n"] = 0
        event.listen(database.engine, "before_cursor_execute", count)
        try:
            r = client.get(f"/api/admin/overview?range={range_key}", headers=admin_auth)
        finally:
            event.remove(database.engine, "before_cursor_execute", count)
        check(r.status_code == 200, f"overview ({range_key}) failed: {r.status_code}")
        return statements["n"]

    small = queries_for_overview("7d")
    db = database.SessionLocal()
    for i in range(20):
        seed_tenant(db, plan_id, f"Extra {i}")
    db.commit()
    db.close()
    large = queries_for_overview("7d")
    check(large == small, f"overview query count must not grow with tenant count: {small} for ~5 tenants, {large} for ~25")

    # --- inbox counts ----------------------------------------------------------------------------
    inbox = client.get("/api/admin/inbox-counts", headers=admin_auth)
    check(inbox.status_code == 200, f"inbox-counts failed: {inbox.status_code} {inbox.text[:200]}")
    ibody = inbox.json()
    check(ibody.get("pending_approvals") == 1, f"inbox pending_approvals wrong: {ibody}")
    check(ibody.get("new_custom_requests") == 1, f"inbox new_custom_requests wrong: {ibody}")
    check(ibody.get("critical_alerts") == 1, f"inbox critical_alerts wrong: {ibody}")
    check(ibody.get("failed_payments_7d") == 1, f"inbox failed_payments_7d wrong: {ibody}")

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print(f"Admin overview: numbers correct, cache works, query budget flat ({small} queries) for 5 and 25 tenants")


if __name__ == "__main__":
    run()
