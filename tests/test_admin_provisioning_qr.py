"""POST /api/admin/tenants/{org_id}/provisioning/qr binds to the CHOSEN tenant.

The tenant-facing /api/provisioning/qr binds to scope.organization_id, which is right for
an owner and wrong for an operator acting with no "Enter workspace" step -- it would mint
a token for the operator's OWN house org, and the TV would enrol into the wrong tenant's
workspace. This is the fix: the target is explicit, not inferred. Pins:
  * the minted token is bound to the chosen org, not the admin's own
  * missing PROVISIONING_* env vars still answer 503, not 500
  * a removed workspace is refused
  * the actual QR token value never appears in the audit trail
  * a non-admin is refused
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-provqr-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-admin-provisioning-qr")
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
    target = models.Organization(name="Target Co", slug=f"target-{uuid.uuid4().hex[:6]}", status="active")
    removed = models.Organization(name="Gone Co", slug=f"gone-{uuid.uuid4().hex[:6]}", status="active",
                                  deleted_at=models.utcnow())
    db.add_all([house, target, removed])
    db.flush()
    admin = models.User(organization_id=house.id, username="pq-admin", email="pq-admin@x.t",
                        hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    non_admin = models.User(organization_id=target.id, username="pq-owner", email="pq-owner@x.t",
                            hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add_all([admin, non_admin])
    db.commit()
    house_id, target_id, removed_id = house.id, target.id, removed.id
    admin_auth = {"Authorization": f"Bearer {login_token(admin)}"}
    non_admin_auth = {"Authorization": f"Bearer {login_token(non_admin)}"}
    db.close()

    body = {"wifi_ssid": "Lobby-WiFi", "wifi_password": "s3cret-pw", "max_uses": 3}

    # --- not configured: 503, not 500 --------------------------------------------------------
    unconfigured = client.post(f"/api/admin/tenants/{target_id}/provisioning/qr", json=body, headers=admin_auth)
    check(unconfigured.status_code == 503, f"missing provisioning env vars should 503, got {unconfigured.status_code}")

    refused = client.post(f"/api/admin/tenants/{target_id}/provisioning/qr", json=body, headers=non_admin_auth)
    check(refused.status_code == 403, f"a non-admin should be refused, got {refused.status_code}")

    os.environ["PROVISIONING_CERT_SHA256_BASE64"] = "deadbeef"
    os.environ["PROVISIONING_APK_URL"] = "https://example.com/app.apk"
    os.environ["PUBLIC_BASE_URL"] = "https://api.example.com"
    try:
        removed_resp = client.post(f"/api/admin/tenants/{removed_id}/provisioning/qr", json=body, headers=admin_auth)
        check(removed_resp.status_code == 409, f"a removed workspace should be refused, got {removed_resp.status_code}")

        ok = client.post(f"/api/admin/tenants/{target_id}/provisioning/qr", json=body, headers=admin_auth)
        check(ok.status_code == 200, f"provisioning QR failed: {ok.status_code} {ok.text[:300]}")
        payload = ok.json()
        bundle = payload.get("android.app.extra.PROVISIONING_ADMIN_EXTRAS_BUNDLE", {})
        token_value = bundle.get("enrollment_token")
        check(bool(token_value), f"expected a minted enrollment token in the payload: {payload}")
        check(payload.get("android.app.extra.PROVISIONING_WIFI_SSID") == "Lobby-WiFi", f"wifi ssid wrong: {payload}")

        db = database.SessionLocal()
        token_row = db.query(models.EnrollmentToken).filter(models.EnrollmentToken.token == token_value).first()
        check(token_row is not None and token_row.organization_id == target_id,
              f"the minted token should be bound to the CHOSEN org ({target_id}), got {token_row.organization_id if token_row else None} "
              f"(the operator's own house org is {house_id} -- binding there would be the bug this route exists to fix)")
        check(token_row is not None and token_row.max_uses == 3, f"max_uses should round-trip: {token_row.max_uses if token_row else None}")

        audit_row = db.query(models.AdminAuditEvent).filter(
            models.AdminAuditEvent.action == "tenant.provisioning_qr").order_by(models.AdminAuditEvent.id.desc()).first()
        check(audit_row is not None and audit_row.target_org_id == target_id, f"audit row missing or wrong org: {audit_row}")
        if audit_row:
            trail = json.dumps([audit_row.before_json, audit_row.after_json])
            check(token_value not in trail, "the actual enrollment token must never appear in the audit trail")
        db.close()
    finally:
        for key in ("PROVISIONING_CERT_SHA256_BASE64", "PROVISIONING_APK_URL", "PUBLIC_BASE_URL"):
            os.environ.pop(key, None)

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Admin provisioning QR: binds to the chosen tenant (not the operator's own org), 503/409/403 guards hold, token never audited")


if __name__ == "__main__":
    run()
