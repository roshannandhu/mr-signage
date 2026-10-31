import pathlib
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Its OWN database, chosen BEFORE backend is imported.
#
# This file set no DATABASE_URL at all, so running it directly -- which is how every script
# here is meant to be run -- let backend/database.py fall back to load_dotenv() and bind the
# engine to whatever backend/.env pointed at. On a developer machine that is the PRODUCTION
# Supabase database, and this script creates an organisation, a screen and play logs. Rows
# named "Ad Test Org" and "BTF Test Org" were found sitting in production, dated to test runs.
#
# conftest.py already guards the pytest path by setting DATABASE_URL before collection; it
# cannot help a script run on its own, which is why the choice belongs here.
import tempfile
_db_path = pathlib.Path(tempfile.gettempdir()) / f"olrac-btf-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
os.environ.setdefault("SECRET_KEY", "pytest-isolated")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient
from backend.main import app

# Build the schema explicitly. backend.main used to do this as an import side effect,
# so importing the app silently wrote to whatever DATABASE_URL pointed at; it now runs
# in the lifespan, which a bare TestClient(app) never starts. Each isolated script owns
# its own database anyway, so creating it here is the honest version of what was
# happening implicitly before.
from backend import database as _bootstrap_db, models as _bootstrap_models
_bootstrap_models.Base.metadata.create_all(bind=_bootstrap_db.engine)
from backend import database, models
from backend.routers.auth import get_password_hash

client = TestClient(app)

def run():
    db = database.SessionLocal()
    try:
        # Clean test records
        db.query(models.PlayLog).delete()
        db.query(models.Screen).filter(models.Screen.name.like("BTF Screen%")).delete()
        db.query(models.User).filter(models.User.email == "btf_admin@olrac.com").delete()
        org_to_clean = db.query(models.Organization).filter(models.Organization.name == "BTF Test Org").first()
        if org_to_clean:
            # Every child table that holds a NOT NULL organization_id. Only subscriptions
            # were cleared, so an alert raised by an earlier run blocked the delete and the
            # test failed in its own setup. The FKs carry no ON DELETE, so nothing cascades.
            for child in (models.Alert, models.Subscription):
                db.query(child).filter(child.organization_id == org_to_clean.id).delete()
            db.delete(org_to_clean)
        db.commit()

        # Remote control (bring-to-front) is sold behind a plan flag; this org needs it,
        # or every call below gets 403 instead of exercising the command delivery paths
        # this test is actually about.
        import json as _json
        plan = db.query(models.Plan).filter(models.Plan.slug == "btf-remote-control").first()
        if not plan:
            plan = models.Plan(
                name="BTF Plan", slug="btf-remote-control",
                feature_flags_json=_json.dumps({"remote_control": True}),
                is_active=True, max_storage_bytes=10 * 1024 ** 3,
            )
            db.add(plan)
            db.flush()
        org = models.Organization(name="BTF Test Org", slug="btf-test-org", status="active", plan_id=plan.id)
        db.add(org)
        db.commit()
        db.refresh(org)

        admin = models.User(
            email="btf_admin@olrac.com",
            username="btf_admin",
            hashed_password=get_password_hash("adminpass123"),
            organization_id=org.id,
            role="owner",
        )
        db.add(admin)
        db.commit()

        device_id = f"btf-device-{uuid.uuid4().hex[:8]}"
        screen = models.Screen(
            name="BTF Screen 1",
            device_id=device_id,
            organization_id=org.id,
            status="online",
            approved_at=models.utcnow(),
            last_seen=models.utcnow(),
            orientation=0,
            orientation_source="auto",
        )
        db.add(screen)
        db.commit()
        db.refresh(screen)

        screen_id = screen.id

    finally:
        db.close()

    # Authenticate
    login_resp = client.post("/api/auth/token", data={"username": "btf_admin@olrac.com", "password": "adminpass123"})
    assert login_resp.status_code == 200, login_resp.text
    token = login_resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Test POST /api/screens/{screen_id}/bring-to-front
    btf_resp = client.post(f"/api/screens/{screen_id}/bring-to-front", headers=headers)
    # This endpoint needs Redis: the command is queued there for the device to collect on
    # its next heartbeat. A 503 means the broker was unreachable and the command genuinely
    # was not delivered -- which the endpoint now reports instead of answering "ok"
    # regardless, the behaviour that made an undelivered command look like flaky hardware.
    if btf_resp.status_code == 503:
        raise AssertionError(
            "bring-to-front needs a running Redis on REDIS_URL; start one and re-run."
        )
    assert btf_resp.status_code == 200, btf_resp.text
    assert btf_resp.json()["status"] == "ok"

    # 2. Test Heartbeat retrieves the pending command
    hb_resp = client.post(
        "/api/screens/heartbeat",
        json={
            "device_id": device_id,
            "device_version": "1.0.0",
        }
    )
    assert hb_resp.status_code == 200, hb_resp.text
    hb_data = hb_resp.json()
    assert hb_data["pending_command"] == "bring_to_front", f"Expected 'bring_to_front', got {hb_data.get('pending_command')}"

    # 3. Subsequent heartbeat has no pending command (consumed)
    hb_resp2 = client.post(
        "/api/screens/heartbeat",
        json={
            "device_id": device_id,
            "device_version": "1.0.0",
        }
    )
    assert hb_resp2.status_code == 200
    assert hb_resp2.json().get("pending_command") is None

    # 4. Issue bring-to-front again and test sync_tv retrieval
    btf_resp2 = client.post(f"/api/screens/{screen_id}/bring-to-front", headers=headers)
    assert btf_resp2.status_code == 200

    sync_resp = client.get(f"/api/screens/{device_id}/sync")
    assert sync_resp.status_code == 200, sync_resp.text
    sync_data = sync_resp.json()
    assert sync_data["pending_command"] == "bring_to_front", f"Expected 'bring_to_front', got {sync_data.get('pending_command')}"

    # 5. Subsequent sync has no pending command
    sync_resp2 = client.get(f"/api/screens/{device_id}/sync")
    assert sync_resp2.status_code in (200, 204)
    if sync_resp2.status_code == 200:
        assert sync_resp2.json().get("pending_command") is None

    print("ALL BRING-TO-FRONT AND OPEN-APP-ON-TV TESTS PASSED!")

if __name__ == "__main__":
    run()
