"""GET /api/admin/payments: pagination (offset/limit + X-Total-Count) and the kind/provider
filters, added alongside the existing organization_id/since/until ones.

Deliberately narrow -- test_admin_billing.py already covers the basic list-and-amounts
behaviour; this pins pagination, filters, total counts, and authorization.
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-paypaging-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-admin-payments-paging")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run() -> None:
    client = TestClient(app)
    db = database.SessionLocal()
    org = models.Organization(name="Payer Co", slug=f"payer-{uuid.uuid4().hex[:6]}", status="active")
    db.add(org)
    db.flush()
    admin = models.User(organization_id=org.id, username="pp-admin", email="pp-admin@x.t",
                        hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    db.add(admin)
    owner = models.User(organization_id=org.id, username="pp-owner", email="pp-owner@x.t",
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add(owner)
    db.flush()

    now = models.utcnow()
    for i in range(5):
        db.add(models.PlatformPayment(
            organization_id=org.id, kind="plan" if i < 3 else "custom", amount_paise=1000 * (i + 1),
            currency="INR", provider="razorpay" if i < 4 else "mock", status="captured", paid_at=now,
        ))
    db.commit()
    admin_auth = {"Authorization": f"Bearer {login_token(admin)}"}
    owner_auth = {"Authorization": f"Bearer {login_token(owner)}"}
    db.close()

    # Authorization guards
    unauth = client.get("/api/admin/payments")
    check(unauth.status_code == 401, f"unauthenticated should be 401: {unauth.status_code}")

    forbidden = client.get("/api/admin/payments", headers=owner_auth)
    check(forbidden.status_code == 403, f"non-super-admin should be 403: {forbidden.status_code}")

    # Backward compatibility: unpaginated request returns all rows + X-Total-Count
    compat = client.get("/api/admin/payments", headers=admin_auth)
    check(compat.status_code == 200 and len(compat.json()) == 5, f"unpaginated should return 5 rows: {len(compat.json())}")
    check(compat.headers.get("x-total-count") == "5", f"compat X-Total-Count should be 5: {compat.headers.get('x-total-count')}")

    # Pagination: offset & limit
    page1 = client.get("/api/admin/payments", headers=admin_auth, params={"limit": 2, "offset": 0})
    check(page1.status_code == 200 and len(page1.json()) == 2, f"first page should have 2 rows: {page1.json()}")
    check(page1.headers.get("x-total-count") == "5", f"X-Total-Count should be 5: {page1.headers.get('x-total-count')}")

    page2 = client.get("/api/admin/payments", headers=admin_auth, params={"limit": 2, "offset": 2})
    check(len(page2.json()) == 2, f"second page should have 2 rows: {page2.json()}")
    ids_p1 = {r["id"] for r in page1.json()}
    ids_p2 = {r["id"] for r in page2.json()}
    check(ids_p1.isdisjoint(ids_p2), f"pages should not overlap: {ids_p1} vs {ids_p2}")

    # Validation: negative offset rejected
    neg_offset = client.get("/api/admin/payments", headers=admin_auth, params={"offset": -1})
    check(neg_offset.status_code == 422, f"negative offset should be 422: {neg_offset.status_code}")

    # Filters: kind and provider
    by_kind = client.get("/api/admin/payments", headers=admin_auth, params={"kind": "custom"}).json()
    check(len(by_kind) == 2, f"kind filter should narrow to 2 rows, got {len(by_kind)}")

    by_provider = client.get("/api/admin/payments", headers=admin_auth, params={"provider": "mock"}).json()
    check(len(by_provider) == 1, f"provider filter should narrow to 1 row, got {len(by_provider)}")

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Admin payments paging: offset/limit pages don't overlap, X-Total-Count correct, kind/provider filters narrow, auth & validation held")


if __name__ == "__main__":
    run()
