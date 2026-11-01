"""Every platform-operator change leaves exactly one audit row, and nothing else does.

Before admin_audit_events, suspend, plan change, role change and permanent delete reached
only stdout, so "who did this, when, and why" could not be answered after the fact. Pins:
  * each admin mutation writes one row with the right action, actor and target tenant
  * the reason typed into the console (X-Admin-Reason, URI-encoded) is stored decoded
  * a maintenance PIN never reaches the trail, only whether one is set
  * a super admin's successful write from inside a tenant's workspace is recorded as
    act_as.write; reads, refused writes, and the tenant's own writes are not
  * an admin route called while acting inside a workspace is recorded once, not twice
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-adminaudit-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-admin-audit")
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


def audit_rows() -> list[models.AdminAuditEvent]:
    db = database.SessionLocal()
    try:
        rows = db.query(models.AdminAuditEvent).order_by(models.AdminAuditEvent.id).all()
        db.expunge_all()
        return rows
    finally:
        db.close()


def run() -> None:
    client = TestClient(app)
    db = database.SessionLocal()

    plan = models.Plan(
        name="Starter", slug=f"starter-{uuid.uuid4().hex[:6]}", feature_flags_json=json.dumps({}),
        is_active=True, max_storage_bytes=10 * 1024 ** 3,
    )
    db.add(plan)
    db.flush()
    house = models.Organization(name="House", slug=f"house-{uuid.uuid4().hex[:6]}", status="active", plan_id=plan.id)
    tenant = models.Organization(name="Tenant B", slug=f"tenant-b-{uuid.uuid4().hex[:6]}", status="active", plan_id=plan.id)
    pending = models.Organization(name="Pending C", slug=f"pending-c-{uuid.uuid4().hex[:6]}", status="pending_approval")
    db.add_all([house, tenant, pending])
    db.flush()
    admin = models.User(organization_id=house.id, username="audit-admin", email="audit-admin@x.t",
                        hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    owner = models.User(organization_id=tenant.id, username="audit-owner", email="audit-owner@x.t",
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    editor = models.User(organization_id=tenant.id, username="audit-editor", email="audit-editor@x.t",
                         hashed_password=get_password_hash("x"), role="editor", is_active=True)
    db.add_all([admin, owner, editor])
    db.commit()
    tenant_id, pending_id, plan_id, editor_id, admin_id = tenant.id, pending.id, plan.id, editor.id, admin.id
    admin_auth = {"Authorization": f"Bearer {login_token(admin)}"}
    owner_auth = {"Authorization": f"Bearer {login_token(owner)}"}
    db.close()

    def expect_one(action: str, response, *, org_id=None) -> models.AdminAuditEvent | None:
        before = len(audit_rows())
        check(response.status_code < 400, f"{action}: request failed {response.status_code} {response.text[:200]}")
        rows = audit_rows()
        new = rows[expect_one.seen:]
        expect_one.seen = len(rows)
        check(len(new) == 1, f"{action}: expected exactly one audit row, got {[r.action for r in new]} (had {before})")
        if not new:
            return None
        row = new[-1]
        check(row.action == action, f"expected action {action}, got {row.action}")
        check(row.actor_user_id == admin_id and row.actor_username == "audit-admin",
              f"{action}: wrong actor {row.actor_user_id}/{row.actor_username}")
        if org_id is not None:
            check(row.target_org_id == org_id, f"{action}: target_org_id {row.target_org_id} != {org_id}")
        return row
    expect_one.seen = 0

    base = "/api/admin"
    approved = expect_one("tenant.approve", client.post(f"{base}/tenants/{pending_id}/approve",
                                                        json={"plan_id": plan_id}, headers=admin_auth), org_id=pending_id)
    if approved:
        check(json.loads(approved.before_json)["status"] == "pending_approval"
              and json.loads(approved.after_json)["status"] == "active",
              f"approve diff wrong: {approved.before_json} -> {approved.after_json}")

    suspended = expect_one("tenant.suspend", client.post(
        f"{base}/tenants/{tenant_id}/suspend",
        headers={**admin_auth, "X-Admin-Reason": "Unpaid%20since%20October"}), org_id=tenant_id)
    if suspended:
        check(suspended.reason == "Unpaid since October", f"reason not decoded: {suspended.reason!r}")
        check(suspended.ip is not None, "the caller's address should be recorded")

    expect_one("tenant.reinstate", client.post(f"{base}/tenants/{tenant_id}/reinstate", headers=admin_auth), org_id=tenant_id)
    expect_one("tenant.subscription.update", client.patch(f"{base}/tenants/{tenant_id}/subscription",
                                                         json={"extend_days": 30}, headers=admin_auth), org_id=tenant_id)
    expect_one("tenant.quota.update", client.patch(f"{base}/tenants/{tenant_id}/quota",
                                                   json={"max_screens": 4}, headers=admin_auth), org_id=tenant_id)
    expect_one("tenant.grant", client.post(f"{base}/tenants/{tenant_id}/grant",
                                           json={"max_clients": 50}, headers=admin_auth), org_id=tenant_id)

    pin = expect_one("platform.maintenance_pin.update",
                     client.put(f"{base}/maintenance-pin", json={"pin": "4826"}, headers=admin_auth))
    if pin:
        check("4826" not in (pin.before_json or "") + (pin.after_json or ""),
              f"the PIN itself must never be stored in the audit trail: {pin.after_json}")

    created = client.post(f"{base}/plans", json={"name": "Audit Plan", "slug": f"audit-{uuid.uuid4().hex[:6]}"},
                          headers=admin_auth)
    expect_one("plan.create", created)
    new_plan_id = created.json().get("id")
    expect_one("plan.update", client.patch(f"{base}/plans/{new_plan_id}", json={"price_paise": 9900}, headers=admin_auth))
    expect_one("plan.delete", client.delete(f"{base}/plans/{new_plan_id}", headers=admin_auth))

    role = expect_one("user.role.update", client.patch(f"{base}/users/{editor_id}/role",
                                                       json={"role": "viewer"}, headers=admin_auth), org_id=tenant_id)
    if role:
        check(json.loads(role.before_json)["role"] == "editor" and json.loads(role.after_json)["role"] == "viewer",
              f"role diff wrong: {role.before_json} -> {role.after_json}")

    expect_one("platform.contact_email.update", client.put(f"{base}/settings/contact-email",
                                                           json={"email": "ops@example.com"}, headers=admin_auth))
    expect_one("tenant.remove", client.delete(f"{base}/tenants/{tenant_id}", headers=admin_auth), org_id=tenant_id)
    expect_one("tenant.restore", client.post(f"{base}/tenants/{tenant_id}/restore", headers=admin_auth), org_id=tenant_id)

    # --- an admin route called from inside a workspace is recorded once --------------------
    acting = {**admin_auth, "X-Act-As-Org": str(tenant_id)}
    expect_one("tenant.suspend", client.post(f"{base}/tenants/{tenant_id}/suspend", headers=acting), org_id=tenant_id)
    expect_one("tenant.reinstate", client.post(f"{base}/tenants/{tenant_id}/reinstate", headers=admin_auth), org_id=tenant_id)

    # --- a write from inside a tenant's workspace --------------------------------------------
    write = expect_one("act_as.write", client.post("/api/clients/", json={"name": "Acme Ads"}, headers=acting), org_id=tenant_id)
    if write:
        check(write.acting_org_id == tenant_id, f"acting_org_id should be the workspace: {write.acting_org_id}")
        check(json.loads(write.after_json) == {"method": "POST", "path": "/api/clients/"},
              f"act-as entry should name the request: {write.after_json}")

    # --- and what must NOT be recorded --------------------------------------------------------
    count = len(audit_rows())
    client.get("/api/clients/", headers=acting)
    refused = client.post("/api/clients/", json={}, headers=acting)
    check(refused.status_code == 422, f"setup: an empty client should be refused, got {refused.status_code}")
    tenant_own = client.post("/api/clients/", json={"name": "Owner's own"}, headers=owner_auth)
    check(tenant_own.status_code == 201, f"setup: owner client create failed {tenant_own.status_code} {tenant_own.text[:200]}")
    check(len(audit_rows()) == count,
          f"reads, refused writes and a tenant's own writes must not be audited: {[r.action for r in audit_rows()[count:]]}")

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Admin audit log: one row per admin change, reasons decoded, PINs redacted, act-as writes recorded")


if __name__ == "__main__":
    run()
