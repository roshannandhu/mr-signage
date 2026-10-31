"""A company from signup to paying customer: python tests/test_signup_lifecycle.py

The whole commercial path, in the order it actually happens:

    sign up  ->  wait  ->  Super Admin approves onto a package  ->  pay  ->  operate
                  |
                  +-- TVs already installed play the demo reel rather than sitting dark

and the two ways it ends: the operator blocks the workspace, or reinstates it.

What is deliberately asserted at each stage is BOTH halves -- what the tenant can now do,
and what they still cannot. A signup that is pending approval but can already upload media
is not a queue, it is a formality.
"""

import hashlib
import hmac
import json
import time
import os
import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEMP_DIR = tempfile.TemporaryDirectory(prefix="olrac-lifecycle-test-", ignore_cleanup_errors=True)
DB_PATH = Path(TEMP_DIR.name) / "lifecycle.db"
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
os.environ["SECRET_KEY"] = "lifecycle-test-secret-not-for-production"
os.environ["INITIAL_ADMIN_USERNAME"] = "platform-op"
os.environ["INITIAL_ADMIN_PASSWORD"] = "platform-pass-123"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "mock"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "lifecycle-webhook-secret"
# The browser half of Google must look configured, or /api/auth/google refuses outright --
# which is correct behaviour, and would stop this test before signup.
os.environ["GOOGLE_WEB_CLIENT_ID"] = "lifecycle-test-client-id.apps.googleusercontent.com"
os.environ["GOOGLE_WEB_CLIENT_SECRET"] = "lifecycle-test-client-secret"

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, google_device, models  # noqa: E402
from backend.limiter import limiter  # noqa: E402
from backend.main import app  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)
limiter.enabled = False

client = TestClient(app)
client.__enter__()

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def token_for(username: str, password: str) -> str:
    r = client.post("/api/auth/token", data={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def stub_google(email: str, name: str) -> None:
    """Stand in for Google's token endpoint.

    Only the exchange is replaced. Everything downstream -- the email_verified check, the
    account lookup, org creation, the approval gate -- is the real code path.
    """
    google_device.exchange_code = lambda code, redirect_uri: {
        "email": email, "email_verified": True, "sub": f"google-sub-{email}", "name": name,
    }


def promote_platform_operator() -> None:
    """The bootstrap account is created as an ordinary owner; make it the operator.

    Deliberately done in the database, because that is now the ONLY way to mint a super
    admin -- every HTTP route refuses the role, and the hardcoded email allow-lists that
    used to grant it have been removed. Mirrors what
    `python -m backend.seed_admin <name> --role super_admin` does on a real deployment.
    """
    db = database.SessionLocal()
    try:
        operator = db.query(models.User).filter(models.User.username == "platform-op").one()
        operator.role = "super_admin"
        db.commit()
    finally:
        db.close()


def run() -> None:
    promote_platform_operator()
    admin = hdr(token_for("platform-op", "platform-pass-123"))

    # ================================================================= 1. SIGN UP
    # A brand-new company. Nobody has an MR Signage account, they arrive through Google.
    stub_google("owner@newshop.example", "New Shop")
    signup = client.post("/api/auth/google", json={
        "code": "google-auth-code", "redirect_uri": "http://localhost:3000/login",
    })
    check(signup.status_code == 200, f"signup failed: {signup.text}")
    if signup.status_code != 200:
        return

    session = signup.json()
    tenant = hdr(session["access_token"])
    user = session["user"]

    check(user["role"] == "owner", f"a new signup should own its workspace, got {user['role']}")
    check(
        user["organization_status"] == "pending_approval",
        f"a new signup must queue for approval, got {user['organization_status']}",
    )
    # This is what routes them to /dashboard/pending in the browser.
    me = client.get("/api/auth/me", headers=tenant)
    check(me.status_code == 200, f"/auth/me failed while pending: {me.text}")
    check(
        me.json()["organization_status"] == "pending_approval",
        "the pending state must be readable, or the dashboard cannot route to the waiting page",
    )

    # Signing in again must NOT create a second workspace.
    stub_google("owner@newshop.example", "New Shop")
    again = client.post("/api/auth/google", json={
        "code": "google-auth-code-2", "redirect_uri": "http://localhost:3000/login",
    })
    check(again.status_code == 200, f"repeat Google sign-in failed: {again.text}")
    if again.status_code == 200:
        check(
            again.json()["user"]["organization_id"] == user["organization_id"],
            "a repeat sign-in created a SECOND workspace for the same person",
        )

    org_id = user["organization_id"]

    # ================================================================= 2. WAITING
    # Nothing in the product is usable yet. If any of these succeed the queue is decorative.
    for label, response in (
        ("list screens", client.get("/api/screens/", headers=tenant)),
        ("list content", client.get("/api/content/", headers=tenant)),
        ("create playlist", client.post("/api/playlists/", headers=tenant, json={"name": "x"})),
        ("create group", client.post("/api/groups/", headers=tenant, json={"name": "x"})),
        ("read billing", client.get("/api/billing/summary", headers=tenant)),
        ("read team", client.get("/api/users/", headers=tenant)),
    ):
        check(
            response.status_code == 403,
            f"PENDING TENANT reached '{label}' -> {response.status_code} (expected 403)",
        )

    # ...and they certainly cannot approve themselves.
    check(
        client.post(f"/api/admin/tenants/{org_id}/approve", headers=tenant, json={}).status_code == 403,
        "a pending tenant approved its own workspace",
    )

    # A TV installed early still shows something: the platform demo reel, not a black panel
    # and not the tenant's own (empty) library.
    client.post("/api/screens/register", json={
        "device_id": "newshop-tv-1", "installation_id": "sn_NEWSHOP01",
        "hardware_name": "Realtek TV", "device_model": "Realtek TV", "manufacturer": "Realtek",
    })
    db = database.SessionLocal()
    try:
        screen = db.query(models.Screen).filter(models.Screen.device_id == "newshop-tv-1").one()
        screen.organization_id = org_id
        screen.status = "online"
        db.commit()
    finally:
        db.close()

    demo = client.get("/api/screens/newshop-tv-1/sync")
    check(demo.status_code == 200, f"a pending tenant's TV could not sync: {demo.text}")
    if demo.status_code == 200:
        body = demo.json()
        check(body.get("playlist") is not None, "a pending tenant's TV was given nothing to play")
        if body.get("playlist"):
            check(
                "Demo" in body["playlist"]["name"],
                f"expected the demo reel while pending, got '{body['playlist']['name']}'",
            )

    # ================================================================= 3. THE QUEUE
    queue = client.get("/api/admin/tenants", params={"status": "pending_approval"}, headers=admin)
    check(queue.status_code == 200, f"approvals queue failed: {queue.text}")
    if queue.status_code == 200:
        waiting = [t for t in queue.json() if t["id"] == org_id]
        check(bool(waiting), "the new signup did not appear in the approvals queue")
        if waiting:
            check(
                waiting[0]["owner_email"] == "owner@newshop.example",
                "the queue does not show who is asking to be let in",
            )

    # ================================================================= 4. PACKAGE + APPROVE
    package = client.post("/api/admin/plans", headers=admin, json={
        "name": "Starter", "slug": "starter-lifecycle", "monthly_price_paise": 99900,
        "yearly_price_paise": 999000, "max_screens": 2,
        "max_storage_bytes": 5 * 1024 ** 3, "max_ad_slots": 1, "is_active": True,
    })
    check(package.status_code == 201, f"package creation failed: {package.text}")
    package_id = package.json()["id"] if package.status_code == 201 else None

    approved = client.post(f"/api/admin/tenants/{org_id}/approve", headers=admin,
                           json={"plan_id": package_id})
    check(approved.status_code == 200, f"approval failed: {approved.text}")
    if approved.status_code == 200:
        summary = approved.json()
        check(summary["status"] == "active", "approval did not activate the workspace")
        check(summary["max_screens"] == 2, f"package screen limit not applied: {summary['max_screens']}")
        check(summary["max_ad_slots"] == 1, f"package ad limit not applied: {summary['max_ad_slots']}")

    # ================================================================= 5. OPERATING
    check(
        client.get("/api/screens/", headers=tenant).status_code == 200,
        "an approved tenant still cannot reach its own fleet",
    )
    playlist = client.post("/api/playlists/", headers=tenant, json={"name": "Shop Loop"})
    check(playlist.status_code in (200, 201), f"approved tenant cannot create a playlist: {playlist.text}")

    # The TV switches off the demo reel by itself on the next sync.
    after = client.get("/api/screens/newshop-tv-1/sync")
    if after.status_code == 200 and after.json().get("playlist"):
        check(
            "Demo" not in after.json()["playlist"]["name"],
            "an approved tenant's TV is still stuck on the demo reel",
        )

    # ================================================================= 6. QUOTA BITES
    # The package said two screens. The third must be refused, or the limit is theatre.
    for index in (2, 3):
        client.post("/api/screens/register", json={
            "device_id": f"newshop-tv-{index}", "installation_id": f"sn_NEWSHOP0{index}",
            "hardware_name": "Realtek TV", "device_model": "Realtek TV", "manufacturer": "Realtek",
        })
    db = database.SessionLocal()
    try:
        db.query(models.Screen).filter(models.Screen.device_id == "newshop-tv-2").update(
            {"organization_id": org_id, "status": "online"}
        )
        db.commit()
    finally:
        db.close()

    third = client.post("/api/screens/sign-in", json={
        "device_id": "newshop-tv-3", "installation_id": "sn_NEWSHOP03",
        "username": "owner@newshop.example", "password": "unused-google-account",
        "name": "Third TV",
    })
    # That account has no usable password (Google signup), so this is 401 rather than 409 --
    # the quota path is proven directly below instead.
    check(third.status_code in (401, 409), f"unexpected third-screen result: {third.status_code}")

    from backend.routers.screens import ensure_screen_quota
    db = database.SessionLocal()
    try:
        try:
            ensure_screen_quota(db, org_id, "add another screen")
            check(False, "QUOTA NOT ENFORCED: a third screen was allowed on a 2-screen package")
        except Exception as exc:
            check("quota" in str(exc).lower() or "409" in str(exc),
                  f"screen quota raised the wrong error: {exc}")
    finally:
        db.close()

    # ================================================================= 7. PAYMENT
    billing = client.get("/api/billing/summary", headers=tenant)
    check(billing.status_code == 200, f"billing summary failed: {billing.text}")

    checkout = client.post("/api/billing/checkout", headers=tenant,
                           json={"plan_id": package_id, "billing_period": "monthly"})
    check(checkout.status_code == 200, f"checkout failed: {checkout.text}")
    provider_subscription_id = checkout.json().get("provider_subscription_id") if checkout.status_code == 200 else None

    db = database.SessionLocal()
    try:
        subscription = db.query(models.Subscription).filter(
            models.Subscription.organization_id == org_id
        ).first()
        check(subscription is not None,
              f"no subscription row exists for the org (summary={billing.status_code} "
              f"{billing.text[:120]}; checkout={checkout.status_code} {checkout.text[:120]})")
        if subscription is not None:
            check(
                subscription.status == "pending",
                f"a paid plan should await the provider, got '{subscription.status}'",
            )
    finally:
        db.close()

    # The provider confirms. Only a correctly signed webhook may activate a subscription.
    def webhook(event_type: str, event_id: str, *, valid: bool = True):
        # Relative to now, not absolute. These were hardcoded epochs chosen while they were
        # still in the future; by September 2026 `current_end` had drifted into the past, so
        # "paying again" was handing the workspace a window that had already closed. That
        # went unnoticed for as long as nothing compared the period to the clock -- the
        # moment expiry became real, this test started failing for a reason that had nothing
        # to do with the behaviour it covers.
        now_epoch = int(time.time())
        raw = json.dumps({
            "event": event_type,
            "payload": {"subscription": {"entity": {
                "id": provider_subscription_id,
                "current_start": now_epoch - 86_400,
                "current_end": now_epoch + 30 * 86_400,
            }}},
        }, separators=(",", ":")).encode()
        signature = hmac.new(
            os.environ["RAZORPAY_WEBHOOK_SECRET"].encode(), raw, hashlib.sha256
        ).hexdigest()
        return client.post(
            "/api/billing/webhooks/razorpay", content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Signature": signature if valid else "forged",
                "X-Razorpay-Event-Id": event_id,
            },
        )

    forged = webhook("subscription.activated", "evt-forged", valid=False)
    check(forged.status_code == 401, f"a FORGED payment webhook was accepted ({forged.status_code})")

    activated = webhook("subscription.activated", "evt-1")
    check(activated.status_code == 200, f"payment webhook failed: {activated.text}")

    db = database.SessionLocal()
    try:
        subscription = db.query(models.Subscription).filter(
            models.Subscription.organization_id == org_id
        ).first()
        check(
            subscription is not None and subscription.status == "active",
            f"payment did not activate: {getattr(subscription, 'status', 'NO SUBSCRIPTION ROW')}",
        )
    finally:
        db.close()

    # Replayed webhooks must not double-apply.
    replay = webhook("subscription.activated", "evt-1")
    check(replay.status_code == 200, f"webhook replay errored: {replay.status_code}")

    # --- dunning: a failed payment does NOT cut the customer off immediately ------------
    # subscription.halted opens a grace period. Writes must KEEP working through it -- that
    # is what the grace period is for, and a shop whose card expired should not have its
    # screens frozen the same afternoon.
    halted = webhook("subscription.halted", "evt-2")
    check(halted.status_code == 200, f"halted webhook failed: {halted.text}")

    db = database.SessionLocal()
    try:
        subscription = db.query(models.Subscription).filter(
            models.Subscription.organization_id == org_id
        ).first()
        check(
            subscription is not None and subscription.status == "grace",
            f"a failed payment should open a grace period, got "
            f"{getattr(subscription, 'status', 'NO ROW')}",
        )
        check(
            subscription is not None and subscription.grace_period_end is not None,
            "a grace period was opened with no end date, so it would never expire",
        )
    finally:
        db.close()

    in_grace = client.post("/api/playlists/", headers=tenant, json={"name": "Grace Loop"})
    check(
        in_grace.status_code in (200, 201),
        f"a workspace inside its grace period was frozen early ({in_grace.status_code})",
    )

    # Once the grace period lapses, writes stop.
    db = database.SessionLocal()
    try:
        subscription = db.query(models.Subscription).filter(
            models.Subscription.organization_id == org_id
        ).first()
        if subscription is not None:
            subscription.grace_period_end = models.utcnow() - timedelta(days=1)
            db.commit()
    finally:
        db.close()

    lapsed = client.post("/api/playlists/", headers=tenant, json={"name": "Lapsed Loop"})
    check(
        lapsed.status_code == 403,
        f"an expired grace period still allowed writes ({lapsed.status_code})",
    )
    check(
        client.get("/api/screens/", headers=tenant).status_code == 200,
        "an unpaid workspace lost READ access; it should be read-only, not cut off",
    )

    # An outright cancellation goes straight to read-only, with no grace.
    cancelled = webhook("subscription.cancelled", "evt-3")
    check(cancelled.status_code == 200, f"cancellation webhook failed: {cancelled.text}")
    db = database.SessionLocal()
    try:
        subscription = db.query(models.Subscription).filter(
            models.Subscription.organization_id == org_id
        ).first()
        check(
            subscription is not None and subscription.status == "read_only",
            f"cancellation should be read-only, got {getattr(subscription, 'status', 'NO ROW')}",
        )
    finally:
        db.close()
    check(
        client.post("/api/playlists/", headers=tenant, json={"name": "Cancelled"}).status_code == 403,
        "a cancelled workspace could still write",
    )

    # Paying again restores everything.
    webhook("subscription.activated", "evt-4")
    check(
        client.post("/api/playlists/", headers=tenant,
                    json={"name": "Restored Loop"}).status_code in (200, 201),
        "paying again did not restore write access",
    )

    # ================================================================= 8. CONTROL
    blocked = client.post(f"/api/admin/tenants/{org_id}/suspend", headers=admin)
    check(blocked.status_code == 200, f"suspend failed: {blocked.text}")
    check(
        client.get("/api/screens/", headers=tenant).status_code == 403,
        "a blocked workspace still has API access",
    )

    restored = client.post(f"/api/admin/tenants/{org_id}/reinstate", headers=admin)
    check(restored.status_code == 200, f"reinstate failed: {restored.text}")
    check(
        client.get("/api/screens/", headers=tenant).status_code == 200,
        "a reinstated workspace is still locked out",
    )

    # Raising one tenant's ceiling without touching the package everyone else is on.
    raised = client.patch(f"/api/admin/tenants/{org_id}/quota", headers=admin,
                          json={"max_screens": 50})
    check(raised.status_code == 200, f"quota override failed: {raised.text}")
    if raised.status_code == 200:
        check(raised.json()["max_screens"] == 50, "the quota override did not take")
        if package_id:
            pkg = client.get("/api/admin/plans", headers=admin).json()
            starter = [p for p in pkg if p["id"] == package_id]
            check(
                bool(starter) and starter[0]["max_screens"] == 2,
                "overriding one tenant edited the package for everyone",
            )

    if failures:
        print("SIGNUP LIFECYCLE FAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("signup -> approval -> payment -> operation -> block/reinstate: verified")


if __name__ == "__main__":
    try:
        run()
    finally:
        client.__exit__(None, None, None)
