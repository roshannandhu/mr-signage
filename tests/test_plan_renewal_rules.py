"""What buying a plan does to a workspace that already has one.

Pins the rules the owner chose:
  * renewing the SAME plan while days remain adds the new period after the old one (no paid day lost)
  * switching to a DIFFERENT plan starts today, with no credit for the old one
  * buying a plan replaces limits an operator set by hand with the plan's own
  * paying lets a workspace in from the approval queue, but never lifts a suspension
  * the subscription path cannot hand over a package that is priced only for one-time purchase
  * extending a lapsed workspace reinstates it from every terminal status, not just two
  * an absurd access period is refused instead of overflowing inside the paid webhook
"""
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-renewal-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-plan-renewal")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")
os.environ["PAYMENT_PROVIDER"] = "mock"
os.environ.pop("RENDER_EXTERNAL_URL", None)

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.billing import ensure_billing_catalog  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402
from backend.routers.billing import activate_purchase  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []
DAY = 86400


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def seconds_apart(a, b) -> float:
    if a.tzinfo is None:
        a = a.replace(tzinfo=models.utcnow().tzinfo)
    if b.tzinfo is None:
        b = b.replace(tzinfo=models.utcnow().tzinfo)
    return abs((a - b).total_seconds())


def run() -> None:
    db = database.SessionLocal()
    ensure_billing_catalog(db)
    starter = db.query(models.Plan).filter(models.Plan.slug == "starter").one()
    business = db.query(models.Plan).filter(models.Plan.slug == "business").one()
    starter.price_paise = 99_900
    business.price_paise = 299_900
    payonce = models.Plan(name="Pay once", slug="payonce", monthly_price_paise=0, yearly_price_paise=0,
                          price_paise=50_000, duration_days=30, max_storage_bytes=10 * 1024 ** 3,
                          max_screens=5, is_active=True)
    db.add(payonce)
    operator = models.Organization(name="MR Signage", slug="ops", status="active")
    db.add(operator)
    db.flush()
    db.add(models.User(organization_id=operator.id, username="root", email="root@x.t",
                       hashed_password=get_password_hash("x"), role="super_admin", is_active=True))
    db.commit()
    ids = {"starter": starter.id, "business": business.id, "payonce": payonce.id}
    db.close()

    client = TestClient(app)
    admin = {"Authorization": f"Bearer {create_access_token({'sub': 'root'})}"}

    def tenant(name, plan_slug, days_left, status="active"):
        session = database.SessionLocal()
        plan = session.query(models.Plan).filter(models.Plan.slug == plan_slug).one()
        org = models.Organization(name=name, slug=f"{name}-{uuid.uuid4().hex[:6]}", status=status,
                                  plan_id=plan.id, max_screens=99, max_ad_slots=77)
        session.add(org)
        session.flush()
        user = models.User(organization_id=org.id, username=f"{name}-owner", email=f"{name}@x.t",
                           hashed_password=get_password_hash("x"), role="owner", is_active=True)
        session.add(user)
        now = models.utcnow()
        sub = models.Subscription(organization_id=org.id, plan_id=plan.id, status="active",
                                  billing_period="one_time",
                                  current_period_start=now - timedelta(days=30 - days_left),
                                  current_period_end=now + timedelta(days=days_left))
        session.add(sub)
        session.commit()
        info = (org.id, user.username, sub.current_period_start, sub.current_period_end)
        session.close()
        return info

    def headers(username):
        return {"Authorization": f"Bearer {create_access_token({'sub': username})}"}

    def buy(username, plan_id):
        order = client.post("/api/billing/purchase", headers=headers(username), json={"plan_id": plan_id})
        check(order.status_code == 200, f"purchase failed: {order.status_code} {order.text}")
        confirm = client.post("/api/billing/mock/confirm", headers=headers(username),
                              json={"order_id": order.json()["order_id"]})
        check(confirm.status_code == 200, f"confirm failed: {confirm.status_code} {confirm.text}")

    def subscription_of(org_id):
        session = database.SessionLocal()
        try:
            sub = session.query(models.Subscription).filter(models.Subscription.organization_id == org_id).one()
            org = session.query(models.Organization).filter(models.Organization.id == org_id).one()
            session.expunge_all()
            return sub, org
        finally:
            session.close()

    # --- renewing the same plan early keeps the days that are left ---------------------------
    org_id, username, old_start, old_end = tenant("renew", "starter", days_left=10)
    buy(username, ids["starter"])
    sub, org = subscription_of(org_id)
    check(seconds_apart(sub.current_period_end, old_end + timedelta(days=30)) < 60,
          f"renewing early should end 30 days after the old end: {sub.current_period_end} vs {old_end}")
    check(seconds_apart(sub.current_period_start, old_start) < 60,
          "renewing early should keep the original start, so the paid window stays one piece")

    # --- switching plan starts today, and replaces hand-set limits --------------------------------
    org_id, username, _start, _end = tenant("switch", "starter", days_left=10)
    buy(username, ids["business"])
    sub, org = subscription_of(org_id)
    check(org.plan_id == ids["business"], "the workspace did not move onto the new plan")
    check(seconds_apart(sub.current_period_end, models.utcnow() + timedelta(days=30)) < 120,
          f"a different plan should start today: ends {sub.current_period_end}")
    check(seconds_apart(sub.current_period_start, models.utcnow()) < 120,
          "a different plan should start its window today")
    check((org.max_screens, org.max_ad_slots) == (0, 0),
          f"hand-set limits should be replaced by the plan's: {(org.max_screens, org.max_ad_slots)}")

    # --- a lapsed workspace buying again starts today, whatever plan -----------------------------
    org_id, username, _s, _e = tenant("lapsed", "starter", days_left=-5)
    buy(username, ids["starter"])
    sub, _org = subscription_of(org_id)
    check(seconds_apart(sub.current_period_end, models.utcnow() + timedelta(days=30)) < 120,
          "a lapsed workspace should get a fresh 30 days from today, not from its old end")

    # --- paying never lifts a suspension -----------------------------------------------------------
    org_id, username, _s, _e = tenant("suspended", "starter", days_left=10, status="suspended")
    session = database.SessionLocal()
    sub_row = session.query(models.Subscription).filter(models.Subscription.organization_id == org_id).one()
    sub_row.status = "pending"
    sub_row.provider_subscription_id = "order_suspended_1"
    session.commit()
    activate_purchase(session, "order_suspended_1")
    session.commit()
    session.close()
    _sub, org = subscription_of(org_id)
    check(org.status == "suspended", f"a payment lifted a suspension: status is {org.status!r}")

    # --- a workspace waiting for approval is let in by paying ---------------------------------------
    session = database.SessionLocal()
    waiting = models.Organization(name="waiting", slug=f"w-{uuid.uuid4().hex[:6]}", status="pending_approval")
    session.add(waiting)
    session.flush()
    session.add(models.User(organization_id=waiting.id, username="waiting-owner", email="w@x.t",
                            hashed_password=get_password_hash("x"), role="owner", is_active=True))
    session.commit()
    waiting_id = waiting.id
    session.close()
    buy("waiting-owner", ids["starter"])
    _sub, org = subscription_of(waiting_id)
    check(org.status == "active", f"paying should admit a pending workspace, status {org.status!r}")

    # --- the subscription path cannot give away a pay-once package ---------------------------------
    org_id, username, _s, _e = tenant("giveaway", "starter", days_left=10)
    refused = client.post("/api/billing/checkout", headers=headers(username),
                          json={"plan_id": ids["payonce"], "billing_period": "monthly"})
    check(refused.status_code == 409, f"a pay-once package was handed over by /checkout ({refused.status_code})")
    _sub, org = subscription_of(org_id)
    check(org.plan_id == ids["starter"], "the refused checkout still moved the workspace onto the package")

    # --- extending a lapsed workspace reinstates it from every terminal status ---------------------
    for terminal in ("expired", "cancelled", "read_only", "completed"):
        org_id, _username, _s, _e = tenant(f"ext-{terminal}", "starter", days_left=-3)
        session = database.SessionLocal()
        session.query(models.Subscription).filter(models.Subscription.organization_id == org_id).update(
            {"status": terminal})
        session.commit()
        session.close()
        extended = client.patch(f"/api/admin/tenants/{org_id}/subscription", headers=admin, json={"extend_days": 10})
        check(extended.status_code == 200, f"extend failed for {terminal}: {extended.status_code} {extended.text}")
        sub, _org = subscription_of(org_id)
        check(sub.status == "active", f"extending a {terminal} workspace left it {sub.status!r}")

    # --- an absurd access period is refused ----------------------------------------------------------
    huge = client.post("/api/admin/plans", headers=admin, json={
        "name": "Forever", "slug": "forever", "duration_days": 4000})
    check(huge.status_code == 422, f"a 4000-day access period was accepted ({huge.status_code})")
    org_id, username, _s, _e = tenant("custom", "starter", days_left=10)
    request = client.post("/api/billing/custom-request", headers=headers(username), json={
        "max_screens": 5, "max_clients": 5, "max_ad_slots": 5, "max_storage_bytes": 1024 ** 3,
        "duration_days": 9_999_999})
    check(request.status_code == 422, f"an absurd custom-plan period was accepted ({request.status_code})")


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
    print("OK - renewal keeps paid days, a plan switch starts today, and payment never lifts a suspension")
