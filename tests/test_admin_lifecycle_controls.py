"""The operator's two missing levers: the paid window, and limits of their own choosing.

Before these, the only control over a workspace's lifecycle was suspend/reinstate -- which
is a judgement about the customer, not a fact about the calendar -- so "they paid me offline,
give them another month" meant editing the database by hand. And because feature flags live
on a Plan, granting one workspace emergency alerts meant editing the package every other
workspace was on.

Runs on SQLite; nothing here is dialect-specific.
"""
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-adminctl-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-admin-lifecycle")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.billing import ensure_billing_catalog, plan_features, subscription_state  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def setup() -> tuple[TestClient, dict, int]:
    db = database.SessionLocal()
    ensure_billing_catalog(db)

    operator_org = models.Organization(name="MR Signage", slug="olrac", status="active")
    tenant_org = models.Organization(
        name="Tenant Co", slug="tenant-co", status="pending_approval"
    )
    db.add_all([operator_org, tenant_org])
    db.flush()

    db.add(models.User(
        organization_id=operator_org.id, username="operator", email="op@olrac.test",
        hashed_password=get_password_hash("x"), role="super_admin", is_active=True,
    ))
    db.commit()
    tenant_id = tenant_org.id
    db.close()

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'operator'})}"}
    return client, headers, tenant_id


def run() -> None:
    client, headers, tenant_id = setup()

    # ---------------------------------------------------------------- grant
    granted = client.post(f"/api/admin/tenants/{tenant_id}/grant", headers=headers, json={
        "max_screens": 42,
        "max_clients": 7,
        "features": {"emergency_alert": True, "transitions": True},
        "days": 90,
        "name": "Negotiated deal",
    })
    check(granted.status_code == 200, f"grant failed: {granted.status_code} {granted.text}")
    body = granted.json()
    check(body["max_screens"] == 42, f"granted screen cap not effective: {body['max_screens']}")
    # Granting decides about a workspace, so it is also let in.
    check(body["status"] == "active", f"granted workspace still {body['status']}")

    db = database.SessionLocal()
    org = db.query(models.Organization).filter(models.Organization.id == tenant_id).one()
    check(org.plan.slug == f"custom-org-{tenant_id}", f"unexpected package {org.plan.slug}")
    check(org.plan.is_active is False, "a granted package must not be sellable in the catalogue")
    check(plan_features(org.plan).get("emergency_alert") is True, "granted feature missing")
    check(org.effective_max_clients == 7, f"clients cap not granted: {org.effective_max_clients}")
    db.close()

    # The catalogue must not show it: it is one workspace's deal, not a package.
    catalogue = client.get("/api/admin/plans", headers=headers)
    slugs = [p["slug"] for p in catalogue.json()]
    check(f"custom-org-{tenant_id}" not in slugs, "a granted package leaked into the catalogue")

    # Re-granting edits the same package instead of minting a second one.
    again = client.post(f"/api/admin/tenants/{tenant_id}/grant", headers=headers,
                        json={"max_screens": 50})
    check(again.status_code == 200, f"re-grant failed: {again.text}")
    check(again.json()["max_screens"] == 50, "re-grant did not take")
    db = database.SessionLocal()
    count = db.query(models.Plan).filter(
        models.Plan.slug == f"custom-org-{tenant_id}"
    ).count()
    check(count == 1, f"re-granting minted {count} packages; it must edit the one")
    # A field left out of the second grant keeps its value.
    org = db.query(models.Organization).filter(models.Organization.id == tenant_id).one()
    check(org.effective_max_clients == 7, "re-grant wiped a field it was not given")
    db.close()

    # ---------------------------------------------------------------- unlimited
    # -1 means "no limit" on all four. Screens is the one that had no way to say it at all:
    # 0 there is a real cap of zero, so an operator granting "unlimited" would have taken
    # every television off the tenant instead of giving them more.
    unlimited = client.post(f"/api/admin/tenants/{tenant_id}/grant", headers=headers, json={
        "max_screens": -1, "max_ad_slots": -1, "max_clients": -1, "max_storage_bytes": -1,
    })
    check(unlimited.status_code == 200, f"unlimited grant failed: {unlimited.text}")
    body = unlimited.json()
    check(body["max_screens"] is None, f"unlimited screens reported as {body['max_screens']}")
    check(body["max_ad_slots"] is None, f"unlimited ad slots reported as {body['max_ad_slots']}")
    check(body["max_clients"] is None, f"unlimited clients reported as {body['max_clients']}")

    db = database.SessionLocal()
    org = db.query(models.Organization).filter(models.Organization.id == tenant_id).one()
    check(org.plan.max_screens is None, "unlimited screens must store NULL, not 0")
    check(org.effective_max_screens is None,
          f"effective screen limit is {org.effective_max_screens}, expected no limit")
    check(org.storage_quota_bytes == 0, "unlimited storage must store 0")
    db.close()

    # And the enforcement actually lets them through, which is the whole point.
    from backend.routers.screens import ensure_screen_quota
    db = database.SessionLocal()
    try:
        ensure_screen_quota(db, tenant_id, "add another screen")
    except Exception as exc:  # noqa: BLE001
        check(False, f"unlimited screens still refused a screen: {exc}")
    finally:
        db.close()

    # Zero must still mean zero, or a package granting no screens becomes ungrantable.
    zeroed = client.post(f"/api/admin/tenants/{tenant_id}/grant", headers=headers,
                         json={"max_screens": 0})
    check(zeroed.json()["max_screens"] == 0, "a zero screen cap was read as unlimited")
    db = database.SessionLocal()
    try:
        ensure_screen_quota(db, tenant_id, "add another screen")
        check(False, "a zero screen cap allowed a screen")
    except Exception:
        pass
    finally:
        db.close()

    # Put it back so the subscription checks below run on a sane workspace.
    client.post(f"/api/admin/tenants/{tenant_id}/grant", headers=headers,
                json={"max_screens": 50, "max_storage_bytes": 10_000_000})

    # ---------------------------------------------------------------- features visible
    listed = client.get("/api/admin/tenants", headers=headers).json()
    row = next((t for t in listed if t["id"] == tenant_id), None)
    check(row is not None, "granted tenant missing from the admin list")
    check(row is not None and row.get("feature_flags", {}).get("emergency_alert") is True,
          f"the console cannot see this tenant's features: {row and row.get('feature_flags')}")
    check(row is not None and "clients_used" in row, "clients usage not reported to the console")

    # ---------------------------------------------------------------- subscription
    extended = client.patch(f"/api/admin/tenants/{tenant_id}/subscription", headers=headers,
                            json={"extend_days": 30, "billing_period": "monthly"})
    check(extended.status_code == 200, f"extend failed: {extended.status_code} {extended.text}")
    check(extended.json()["subscription_state"] == "active",
          f"extended workspace is {extended.json()['subscription_state']}")

    db = database.SessionLocal()
    sub = db.query(models.Subscription).filter(
        models.Subscription.organization_id == tenant_id
    ).one()
    first_end = sub.current_period_end
    db.close()
    check(first_end is not None, "extending set no period end")

    # Extending again adds to the window rather than restarting it from today.
    client.patch(f"/api/admin/tenants/{tenant_id}/subscription", headers=headers,
                 json={"extend_days": 30})
    db = database.SessionLocal()
    sub = db.query(models.Subscription).filter(
        models.Subscription.organization_id == tenant_id
    ).one()
    second_end = sub.current_period_end
    db.close()
    check(second_end > first_end + timedelta(days=25),
          f"a second extension did not add to the window: {first_end} -> {second_end}")

    # Cutting a workspace off now, and putting it back.
    ended = client.patch(f"/api/admin/tenants/{tenant_id}/subscription", headers=headers,
                         json={"status": "expired"})
    check(ended.json()["subscription_state"] == "expired",
          f"ending the plan left it {ended.json()['subscription_state']}")

    db = database.SessionLocal()
    org = db.query(models.Organization).filter(models.Organization.id == tenant_id).one()
    from backend.billing import serving_block_reason
    check(serving_block_reason(org, org.subscription) == "expired",
          "an operator-ended plan does not stop the adverts")
    db.close()

    revived = client.patch(f"/api/admin/tenants/{tenant_id}/subscription", headers=headers,
                           json={"extend_days": 15})
    check(revived.json()["subscription_state"] == "active",
          "granting a future window to an expired workspace did not reinstate it")

    # ---------------------------------------------------------------- authorisation
    db = database.SessionLocal()
    db.add(models.User(
        organization_id=tenant_id, username="tenant-owner", email="owner@tenant.test",
        hashed_password=get_password_hash("x"), role="owner", is_active=True,
    ))
    db.commit()
    db.close()
    owner_headers = {"Authorization": f"Bearer {create_access_token({'sub': 'tenant-owner'})}"}
    for path, payload in (
        (f"/api/admin/tenants/{tenant_id}/grant", {"max_screens": 999}),
        (f"/api/admin/tenants/{tenant_id}/subscription", {"extend_days": 999}),
    ):
        method = client.post if path.endswith("grant") else client.patch
        refused = method(path, headers=owner_headers, json=payload)
        check(refused.status_code == 403,
              f"a tenant owner reached {path}: {refused.status_code}")


if __name__ == "__main__":
    try:
        run()
        if FAILURES:
            print("ADMIN LIFECYCLE FAILURES:")
            for failure in FAILURES:
                print("  -", failure)
            raise SystemExit(1)
        print("OK - the operator can set a paid window and grant limits of their own")
    finally:
        database.engine.dispose()
        _db_file.unlink(missing_ok=True)
