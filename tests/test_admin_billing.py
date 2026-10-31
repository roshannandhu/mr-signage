"""Phase 3.2: what a tenant paid the platform is recorded once, and the admin Billing page
has real data and working settings to read.

Pins:
  * a mock plan purchase records one platform_payments row, amount from the plan actually
    bought, and GET /admin/payments (super admin only) lists it with the org's name
  * a mock CUSTOM request purchase records a row with kind="custom"
  * confirming the same order twice does not record the payment twice (idempotent, same as
    the activation it rides on)
  * the contact-email setting round-trips through admin PUT/GET and the tenant-facing
    GET /billing/contact reads the same value; an invalid address is refused
  * the test-email route validates the address and surfaces "not configured" as 503 rather
    than a generic 500, since no SMTP is set in tests
  * an ordinary tenant owner is refused on every admin route here
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-admin-billing-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-admin-billing")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")
os.environ["PAYMENT_PROVIDER"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def make_user(db, org, role, **kw):
    user = models.User(organization_id=org.id, username=f"{role}-{uuid.uuid4().hex[:6]}",
                       hashed_password=get_password_hash("x"), role=role, is_active=True, **kw)
    db.add(user)
    db.flush()
    return user


def run() -> None:
    client = TestClient(app)
    db = database.SessionLocal()

    admin_org = models.Organization(name="Platform", slug=f"platform-{uuid.uuid4().hex[:6]}", status="active")
    db.add(admin_org)
    db.flush()
    admin = make_user(db, admin_org, "super_admin")

    plan = models.Plan(name="Starter", slug=f"starter-{uuid.uuid4().hex[:6]}", monthly_price_paise=99_900,
                       price_paise=50_000, duration_days=30, is_active=True, max_storage_bytes=10 * 1024 ** 3)
    db.add(plan)
    db.flush()
    tenant_org = models.Organization(name="Acme", slug=f"acme-{uuid.uuid4().hex[:6]}", status="pending_approval")
    db.add(tenant_org)
    db.flush()
    owner = make_user(db, tenant_org, "owner")
    db.commit()

    admin_headers = {"Authorization": f"Bearer {login_token(admin)}"}
    owner_headers = {"Authorization": f"Bearer {login_token(owner)}"}

    # --- a plan purchase, confirmed in mock mode, records exactly one payment --------------
    checkout = client.post("/api/billing/purchase", headers=owner_headers, json={"plan_id": plan.id})
    check(checkout.status_code == 200, f"checkout should succeed: {checkout.status_code} {checkout.text}")
    order_id = checkout.json()["order_id"]

    confirm = client.post("/api/billing/mock/confirm", headers=owner_headers, json={"order_id": order_id})
    check(confirm.status_code == 200, f"mock confirm should succeed: {confirm.status_code} {confirm.text}")

    # Confirming again (a retried click, a replayed webhook) must not double the payment.
    client.post("/api/billing/mock/confirm", headers=owner_headers, json={"order_id": order_id})

    db.expire_all()
    rows = db.query(models.PlatformPayment).filter(models.PlatformPayment.organization_id == tenant_org.id).all()
    check(len(rows) == 1, f"expected exactly one payment row after a confirm and a retry, got {len(rows)}")
    if rows:
        row = rows[0]
        check(row.amount_paise == 50_000, f"the payment should record the plan's price_paise, got {row.amount_paise}")
        check(row.kind == "plan", f"expected kind='plan', got {row.kind!r}")
        check(row.provider == "mock", f"expected provider='mock', got {row.provider!r}")

    # --- the admin payments list shows it, with the org name, and refuses an ordinary owner --
    listed = client.get("/api/admin/payments", headers=admin_headers)
    check(listed.status_code == 200, listed.text)
    names = [p["organization_name"] for p in listed.json()]
    check("Acme" in names, f"the payment should be listed with its org's name: {names}")

    refused = client.get("/api/admin/payments", headers=owner_headers)
    check(refused.status_code == 403, f"an ordinary owner should be refused the admin payments list, got {refused.status_code}")

    # --- a custom request purchase records kind='custom' ------------------------------------
    custom = models.CustomPlanRequest(
        organization_id=tenant_org.id, max_screens=20, max_clients=20, max_ad_slots=20,
        max_storage_bytes=20 * 1024 ** 3, duration_days=30, price_paise=75_000,
        feature_flags_json=json.dumps({"scheduling": True}), status="priced",
    )
    db.add(custom)
    db.commit()

    custom_checkout = client.post(f"/api/billing/custom-request/{custom.id}/purchase", headers=owner_headers)
    check(custom_checkout.status_code == 200, f"custom checkout should succeed: {custom_checkout.status_code} {custom_checkout.text}")
    custom_order_id = custom_checkout.json()["order_id"]
    custom_confirm = client.post("/api/billing/mock/confirm", headers=owner_headers, json={"order_id": custom_order_id})
    check(custom_confirm.status_code == 200, custom_confirm.text)

    db.expire_all()
    custom_row = db.query(models.PlatformPayment).filter(
        models.PlatformPayment.organization_id == tenant_org.id, models.PlatformPayment.kind == "custom"
    ).first()
    check(custom_row is not None, "the custom request purchase should have recorded a payment")
    if custom_row:
        check(custom_row.amount_paise == 75_000, f"expected the request's own price, got {custom_row.amount_paise}")
        check(custom_row.custom_request_id == custom.id, "the row should point at the custom request it paid")

    # --- contact email: round-trips, validates, and the tenant-facing read agrees -----------
    bad = client.put("/api/admin/settings/contact-email", headers=admin_headers, json={"email": "not-an-email"})
    check(bad.status_code == 422, f"an invalid address should be refused, got {bad.status_code}")

    saved = client.put("/api/admin/settings/contact-email", headers=admin_headers, json={"email": "sales@mrsignage.test"})
    check(saved.status_code == 200 and saved.json()["email"] == "sales@mrsignage.test", saved.text)

    fetched = client.get("/api/admin/settings/contact-email", headers=admin_headers)
    check(fetched.json()["email"] == "sales@mrsignage.test", fetched.json())

    tenant_read = client.get("/api/billing/contact", headers=owner_headers)
    check(tenant_read.status_code == 200 and tenant_read.json()["email"] == "sales@mrsignage.test",
         f"a signed-in tenant should read the same contact address: {tenant_read.text}")

    refused_setting = client.put("/api/admin/settings/contact-email", headers=owner_headers, json={"email": "x@y.com"})
    check(refused_setting.status_code == 403, "an ordinary owner should not set the platform contact email")

    # --- email status and the test-email route -----------------------------------------------
    status = client.get("/api/admin/settings/email-status", headers=admin_headers)
    check(status.status_code == 200 and status.json()["is_configured"] is False,
         f"no SMTP is configured in tests, expected is_configured=False: {status.text}")

    bad_test = client.post("/api/admin/settings/test-email", headers=admin_headers, json={"to": "not-an-email"})
    check(bad_test.status_code == 422, f"an invalid test-email address should be refused, got {bad_test.status_code}")

    unconfigured = client.post("/api/admin/settings/test-email", headers=admin_headers, json={"to": "ops@mrsignage.test"})
    check(unconfigured.status_code == 503, f"sending with no SMTP configured should be 503, got {unconfigured.status_code}: {unconfigured.text}")

    refused_test = client.post("/api/admin/settings/test-email", headers=owner_headers, json={"to": "ops@mrsignage.test"})
    check(refused_test.status_code == 403, "an ordinary owner should not be able to send a platform test email")

    db.close()


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
    print("OK - platform payments are recorded once per purchase, and the admin billing settings work")
