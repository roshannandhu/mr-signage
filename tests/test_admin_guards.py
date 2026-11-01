"""Server-side guards on the admin console's dangerous actions.

The console's confirm dialogs are only courtesy; these are the checks that hold when a
request is crafted by hand. Pins:
  * a super admin cannot demote themselves (which is also what keeps one admin in place)
  * granting super_admin requires typing the user's username, checked on the server
  * publishing a release works for an operator whose OWN workspace subscription has lapsed
    (the old require_tenant_roles check applied tenant billing rules to the platform)
  * deleting a package a subscription still points at retires it instead of a 500
  * a suspended tenant's dashboard socket is refused
  * the demo reel upload is size-capped and stored through media_storage, and audited
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-adminguards-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-admin-guards")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from backend import database, media_storage, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers import admin as admin_router  # noqa: E402
from backend.routers.auth import get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run() -> None:
    client = TestClient(app)
    db = database.SessionLocal()
    plan = models.Plan(name="P", slug=f"p-{uuid.uuid4().hex[:6]}", feature_flags_json="{}",
                       is_active=True, max_storage_bytes=10 * 1024 ** 3)
    spare_plan = models.Plan(name="Spare", slug=f"spare-{uuid.uuid4().hex[:6]}", feature_flags_json="{}",
                             is_active=True, max_storage_bytes=10 * 1024 ** 3)
    db.add_all([plan, spare_plan])
    db.flush()
    house = models.Organization(name="House", slug=f"house-{uuid.uuid4().hex[:6]}", status="active", plan_id=plan.id)
    tenant = models.Organization(name="Tenant", slug=f"tenant-{uuid.uuid4().hex[:6]}", status="suspended", plan_id=plan.id)
    db.add_all([house, tenant])
    db.flush()
    # The operator's own house workspace has a lapsed subscription.
    db.add(models.Subscription(organization_id=house.id, plan_id=plan.id, status="expired",
                               current_period_end=models.utcnow() - timedelta(days=5)))
    # A tenant moved off spare_plan whose subscription row still references it.
    db.add(models.Subscription(organization_id=tenant.id, plan_id=spare_plan.id, status="active"))
    admin = models.User(organization_id=house.id, username="guard-admin", email="guard-admin@x.t",
                        hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    owner = models.User(organization_id=tenant.id, username="guard-owner", email="guard-owner@x.t",
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add_all([admin, owner])
    db.commit()
    admin_id, owner_id, spare_id = admin.id, owner.id, spare_plan.id
    admin_auth = {"Authorization": f"Bearer {login_token(admin)}"}
    owner_token = login_token(owner)
    db.close()

    # --- self-demotion -------------------------------------------------------------------------
    self_demote = client.patch(f"/api/admin/users/{admin_id}/role", json={"role": "owner"}, headers=admin_auth)
    check(self_demote.status_code == 400, f"demoting yourself should be refused, got {self_demote.status_code}")

    # --- promotion needs the username typed ------------------------------------------------------
    bare = client.patch(f"/api/admin/users/{owner_id}/role", json={"role": "super_admin"}, headers=admin_auth)
    check(bare.status_code == 400, f"promotion without confirmation should be refused, got {bare.status_code}")
    wrong = client.patch(f"/api/admin/users/{owner_id}/role",
                         json={"role": "super_admin", "confirm_username": "someone-else"}, headers=admin_auth)
    check(wrong.status_code == 400, f"promotion with the wrong username should be refused, got {wrong.status_code}")
    right = client.patch(f"/api/admin/users/{owner_id}/role",
                         json={"role": "super_admin", "confirm_username": "guard-owner"}, headers=admin_auth)
    check(right.status_code == 200 and right.json()["role"] == "super_admin",
          f"confirmed promotion failed: {right.status_code} {right.text[:200]}")
    back = client.patch(f"/api/admin/users/{owner_id}/role", json={"role": "owner"}, headers=admin_auth)
    check(back.status_code == 200, f"demoting another admin should work: {back.status_code} {back.text[:200]}")

    # --- releases ignore the operator's own billing state --------------------------------------
    release = client.post("/api/releases/", headers=admin_auth, json={
        "version_code": 4242, "version_name": "4.2.42",
        "apk_url": "https://example.com/app.apk", "sha256": "a" * 64,
    })
    check(release.status_code == 201,
          f"an operator with a lapsed house subscription should still publish: {release.status_code} {release.text[:200]}")

    # --- package delete with a lingering subscription ------------------------------------------
    deleted = client.delete(f"/api/admin/plans/{spare_id}", headers=admin_auth)
    check(deleted.status_code == 200 and deleted.json().get("status") == "deactivated",
          f"a package a subscription references should be retired, not 500: {deleted.status_code} {deleted.text[:200]}")

    # --- suspended tenant's socket ----------------------------------------------------------------
    refused = False
    try:
        with client.websocket_connect(f"/api/ws/dashboard/ws?token={owner_token}"):
            pass
    except WebSocketDisconnect as exc:
        refused = exc.code == 1008
    check(refused, "a suspended tenant's dashboard socket should be refused with 1008")

    # --- demo reel upload ----------------------------------------------------------------------------
    media_storage.is_s3_enabled = lambda: False
    original_cap = getattr(admin_router, "DEMO_VIDEO_MAX_BYTES", None)
    admin_router.DEMO_VIDEO_MAX_BYTES = 10
    try:
        too_big = client.post("/api/admin/demo-video/upload", headers=admin_auth,
                              files={"file": ("reel.mp4", b"x" * 11, "video/mp4")})
        check(too_big.status_code == 413, f"an oversized reel should be refused with 413, got {too_big.status_code}")
        wrong_type = client.post("/api/admin/demo-video/upload", headers=admin_auth,
                                 files={"file": ("reel.html", b"<b>", "text/html")})
        check(wrong_type.status_code == 400, f"a non-video reel should be refused, got {wrong_type.status_code}")
        ok = client.post("/api/admin/demo-video/upload", headers=admin_auth,
                         files={"file": ("reel.mp4", b"x" * 8, "video/mp4")})
        check(ok.status_code == 200, f"a valid reel upload failed: {ok.status_code} {ok.text[:200]}")
        url = ok.json().get("url", "") if ok.status_code == 200 else ""
        check("/uploads/platform/demo/" in url, f"the reel should be stored through media_storage: {url}")
        stored = pathlib.Path(media_storage.UPLOAD_DIR) / url.split("/uploads/", 1)[-1] if url else None
        check(stored is not None and stored.exists(), f"the stored reel is missing on disk: {stored}")
        if stored is not None and stored.exists():
            stored.unlink()
    finally:
        admin_router.DEMO_VIDEO_MAX_BYTES = original_cap

    db = database.SessionLocal()
    reel_audit = db.query(models.AdminAuditEvent).filter(
        models.AdminAuditEvent.action == "platform.demo_video.update").all()
    check(len(reel_audit) == 1 and json.loads(reel_audit[0].after_json).get("size_bytes") == 8,
          f"exactly the successful reel upload should be audited: {[r.after_json for r in reel_audit]}")
    db.close()

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Admin guards: self-demotion, confirmed promotion, release auth, plan FK, suspended socket, reel upload")


if __name__ == "__main__":
    run()
