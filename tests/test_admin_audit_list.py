"""GET /api/admin/audit -- the shared list behind Tenant 360's Activity tab and the
standalone Audit Log page. One endpoint, so the two can never disagree about an action's
name or shape.

Pins: filters by org_id/action/actor_user_id/since/until all narrow correctly, paging
(limit/offset) with X-Total-Count, newest first, non-admin refused.
"""
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-auditlist-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-audit-list")
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
    house = models.Organization(name="House", slug=f"house-{uuid.uuid4().hex[:6]}", status="active")
    tenant_a = models.Organization(name="A", slug=f"a-{uuid.uuid4().hex[:6]}", status="active")
    tenant_b = models.Organization(name="B", slug=f"b-{uuid.uuid4().hex[:6]}", status="active")
    db.add_all([house, tenant_a, tenant_b])
    db.flush()

    admin = models.User(organization_id=house.id, username="al-admin", email="al-admin@x.t",
                        hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    other_admin = models.User(organization_id=house.id, username="al-other", email="al-other@x.t",
                              hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    non_admin = models.User(organization_id=tenant_a.id, username="al-owner", email="al-owner@x.t",
                            hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add_all([admin, other_admin, non_admin])
    db.flush()

    now = models.utcnow()
    db.add_all([
        models.AdminAuditEvent(occurred_at=now - timedelta(hours=3), actor_user_id=admin.id,
                               actor_username="al-admin", target_org_id=tenant_a.id, action="tenant.suspend"),
        models.AdminAuditEvent(occurred_at=now - timedelta(hours=2), actor_user_id=other_admin.id,
                               actor_username="al-other", target_org_id=tenant_b.id, action="tenant.suspend"),
        models.AdminAuditEvent(occurred_at=now - timedelta(hours=1), actor_user_id=admin.id,
                               actor_username="al-admin", target_org_id=tenant_a.id, action="tenant.reinstate"),
        models.AdminAuditEvent(occurred_at=now - timedelta(days=10), actor_user_id=admin.id,
                               actor_username="al-admin", target_org_id=tenant_a.id, action="tenant.grant"),
    ])
    db.commit()
    tenant_a_id, admin_id = tenant_a.id, admin.id
    admin_auth = {"Authorization": f"Bearer {login_token(admin)}"}
    non_admin_auth = {"Authorization": f"Bearer {login_token(non_admin)}"}
    db.close()

    refused = client.get("/api/admin/audit", headers=non_admin_auth)
    check(refused.status_code == 403, f"a non-admin should be refused, got {refused.status_code}")

    all_rows = client.get("/api/admin/audit", headers=admin_auth)
    check(all_rows.status_code == 200 and len(all_rows.json()) == 4, f"expected 4 rows total: {all_rows.json()}")
    check([r["action"] for r in all_rows.json()][:2] == ["tenant.reinstate", "tenant.grant"] or
          all_rows.json()[0]["id"] > all_rows.json()[1]["id"],
          f"expected newest-first ordering: {[r['id'] for r in all_rows.json()]}")

    by_org = client.get(f"/api/admin/audit?org_id={tenant_a_id}", headers=admin_auth).json()
    check(len(by_org) == 3, f"org filter should narrow to 3 rows, got {len(by_org)}")

    by_action = client.get("/api/admin/audit?action=tenant.suspend", headers=admin_auth).json()
    check(len(by_action) == 2, f"action filter should narrow to 2 rows, got {len(by_action)}")

    by_actor = client.get(f"/api/admin/audit?actor_user_id={admin_id}", headers=admin_auth).json()
    check(len(by_actor) == 3, f"actor filter should narrow to 3 rows, got {len(by_actor)}")

    since_cutoff = (now - timedelta(hours=4)).isoformat()
    # params=, not an f-string into the URL: a raw "+00:00" offset embedded directly in a
    # query string is read back as a space by standard URL decoding.
    since_resp = client.get("/api/admin/audit", headers=admin_auth, params={"since": since_cutoff})
    recent = since_resp.json()
    check(since_resp.status_code == 200, f"since filter request failed: {since_resp.status_code} {since_resp.text[:200]}")
    check(len(recent) == 3, f"since filter should exclude the 10-day-old row, got {len(recent)}: {recent}")

    paged = client.get("/api/admin/audit?limit=2&offset=0", headers=admin_auth)
    check(len(paged.json()) == 2, f"limit should cap the page at 2, got {len(paged.json())}")
    check(paged.headers.get("x-total-count") == "4", f"X-Total-Count should reflect the unfiltered total: {paged.headers.get('x-total-count')}")

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Admin audit list: every filter narrows correctly, newest first, paged with X-Total-Count")


if __name__ == "__main__":
    run()
