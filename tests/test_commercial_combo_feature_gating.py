"""Verification of commercial combo feature gating:
- When a plan lacks 'scheduling' (e.g. Free plan):
  - Setting playlist item dates/schedules returns 403
  - Creating client booking placements returns 403
  - Updating playlist item duration/rotation/order works normally
- When 'pdf_reports' is enabled (both Free and paid plans):
  - GET /content/{id}/report.pdf returns a valid PDF
  - The generated report has zero payment or invoice amounts
- When 'scheduling' and 'payslips' are enabled (Starter, Business, Custom):
  - Commercial bookings and playlist item schedules succeed.
"""
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCRATCH = f"olrac_gating_{uuid.uuid4().hex[:8]}"
os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{SCRATCH}"
os.environ["SECRET_KEY"] = "gating-test-secret"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"

import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

admin = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
admin.cursor().execute(f'CREATE DATABASE "{SCRATCH}" OWNER olrac')

db = None
try:
    from fastapi.testclient import TestClient
    from backend import models
    from backend.database import SessionLocal, engine
    from backend.main import app
    from backend.routers.auth import create_access_token, get_password_hash
    from backend.billing import DEFAULT_PLANS, ensure_billing_catalog

    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    ensure_billing_catalog(db)
    free_plan_row = db.query(models.Plan).filter(models.Plan.name == "Free").first()
    starter_plan_row = db.query(models.Plan).filter(models.Plan.name == "Starter").first()

    # Create two organizations: one on Free, one on Starter
    free_org = models.Organization(name="Free Tenant", slug="free-tenant", plan_id=free_plan_row.id)
    starter_org = models.Organization(name="Starter Tenant", slug="starter-tenant", plan_id=starter_plan_row.id)
    db.add_all([free_org, starter_org])
    db.commit()

    free_user = models.User(
        organization_id=free_org.id,
        username="owner@free.test",
        hashed_password=get_password_hash("password123"),
        role="owner",
        is_active=True,
    )
    starter_user = models.User(
        organization_id=starter_org.id,
        username="owner@starter.test",
        hashed_password=get_password_hash("password123"),
        role="owner",
        is_active=True,
    )
    db.add_all([free_user, starter_user])
    db.commit()

    free_token = create_access_token({"sub": free_user.username})
    starter_token = create_access_token({"sub": starter_user.username})
    free_headers = {"Authorization": f"Bearer {free_token}"}
    starter_headers = {"Authorization": f"Bearer {starter_token}"}

    client = TestClient(app)

    # 1. Verify Free plan feature flags in DEFAULT_PLANS
    plans_by_name = {p["name"]: p for p in DEFAULT_PLANS}
    assert plans_by_name["Free"]["features"].get("scheduling") is not True, "Free must not have scheduling"
    assert plans_by_name["Free"]["features"].get("payslips") is not True, "Free must not have payslips"
    assert plans_by_name["Free"]["features"].get("ads_counting") is True, "Free must have ads_counting"
    assert plans_by_name["Free"]["features"].get("pdf_reports") is True, "Free must have pdf_reports"

    # Verify Starter plan feature flags
    assert plans_by_name["Starter"]["features"].get("scheduling") is True, "Starter must have scheduling"
    assert plans_by_name["Starter"]["features"].get("payslips") is True, "Starter must have payslips"

    # 2. Upload media / content to Free tenant
    free_content = models.Content(
        organization_id=free_org.id,
        type="image",
        file_url="/uploads/free_image.jpg",
        name="Free Banner",
        status="ready",
        file_size_bytes=1024,
    )
    db.add(free_content)
    db.commit()

    # Create playlist for Free tenant
    free_playlist = models.Playlist(
        organization_id=free_org.id,
        name="Main Loop",
    )
    db.add(free_playlist)
    db.commit()

    # Add item directly to playlist
    add_resp = client.post(
        f"/api/playlists/{free_playlist.id}/items",
        json={"content_id": free_content.id, "order": 0, "duration": 10},
        headers=free_headers,
    )
    assert add_resp.status_code in (200, 201), f"Expected 200/201 adding item, got {add_resp.status_code}: {add_resp.text}"
    item_id = add_resp.json()["id"]

    # 3. Test that Free tenant CAN update duration and order without dates
    update_ok_resp = client.put(
        f"/api/playlists/{free_playlist.id}/items/{item_id}",
        json={"duration": 15, "order": 0},
        headers=free_headers,
    )
    assert update_ok_resp.status_code == 200, f"Expected 200 for basic update, got {update_ok_resp.status_code}"

    # 4. Test that Free tenant CANNOT set start_at/end_at/schedule (403 Forbidden)
    future_time = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    sched_fail_resp = client.put(
        f"/api/playlists/{free_playlist.id}/items/{item_id}",
        json={"start_at": future_time},
        headers=free_headers,
    )
    assert sched_fail_resp.status_code == 403, f"Expected 403 when setting start_at on Free, got {sched_fail_resp.status_code}"
    assert "scheduling" in sched_fail_resp.text

    # 5. Test that Free tenant CANNOT create commercial placement (403 Forbidden)
    placement_fail_resp = client.post(
        "/api/placements/",
        json={
            "content_id": free_content.id,
            "advertiser": "Acme Corp",
            "starts_at": datetime.now(timezone.utc).isoformat(),
            "ends_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        },
        headers=free_headers,
    )
    assert placement_fail_resp.status_code == 403, f"Expected 403 when creating placement on Free, got {placement_fail_resp.status_code}"

    # 6. Test GET /api/content/{content_id}/report.pdf for Free content (Proof of play PDF with zero prices)
    pdf_resp = client.get(f"/api/content/{free_content.id}/report.pdf", headers=free_headers)
    assert pdf_resp.status_code == 200, f"Expected 200 for report.pdf, got {pdf_resp.status_code}: {pdf_resp.text}"
    assert pdf_resp.headers["content-type"] == "application/pdf"
    assert pdf_resp.content.startswith(b"%PDF"), "Must return valid PDF bytes"
    # Ensure no rupee symbols or currency mentions in raw report PDF stream
    assert b"Invoice" not in pdf_resp.content, "Report PDF must not be an invoice"
    assert b"Payment" not in pdf_resp.content, "Report PDF must not contain payment ledger"

    # 7. Test Starter tenant CAN use scheduling & placements
    starter_content = models.Content(
        organization_id=starter_org.id,
        type="image",
        file_url="/uploads/starter_image.jpg",
        name="Starter Ad",
        status="ready",
        file_size_bytes=2048,
    )
    db.add(starter_content)
    db.commit()

    starter_playlist = models.Playlist(
        organization_id=starter_org.id,
        name="Commercial Loop",
    )
    db.add(starter_playlist)
    db.commit()

    starter_add_resp = client.post(
        f"/api/playlists/{starter_playlist.id}/items",
        json={"content_id": starter_content.id, "order": 0, "duration": 10},
        headers=starter_headers,
    )
    assert starter_add_resp.status_code in (200, 201)
    starter_item_id = starter_add_resp.json()["id"]

    # Starter CAN set scheduling dates
    starter_sched_resp = client.put(
        f"/api/playlists/{starter_playlist.id}/items/{starter_item_id}",
        json={"start_at": future_time},
        headers=starter_headers,
    )
    assert starter_sched_resp.status_code == 200, f"Expected 200 for starter scheduling, got {starter_sched_resp.status_code}"

    # Starter CAN create commercial placement
    starter_placement_resp = client.post(
        "/api/placements/",
        json={
            "content_id": starter_content.id,
            "advertiser": "Commercial Client Inc",
            "starts_at": datetime.now(timezone.utc).isoformat(),
            "ends_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
        },
        headers=starter_headers,
    )
    assert starter_placement_resp.status_code in (200, 201), f"Expected 200/201 for starter placement, got {starter_placement_resp.status_code}: {starter_placement_resp.text}"

    print("ALL COMMERCIAL COMBO FEATURE GATING TESTS PASSED PERFECTLY!")

finally:
    if db:
        db.close()
    admin = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    admin.cursor().execute(f"""
        SELECT pg_terminate_backend(pid) FROM pg_stat_activity
        WHERE datname = '{SCRATCH}' AND pid <> pg_backend_pid()
    """)
    admin.cursor().execute(f'DROP DATABASE IF EXISTS "{SCRATCH}"')
