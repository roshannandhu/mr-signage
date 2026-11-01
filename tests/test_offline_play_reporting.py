"""Online/offline counts (Phase 3.6) reach every surface that is supposed to show them.

The aggregation itself (which plays count as offline) is pinned in test_play_log_aggregation.py
on the portable path and verified by hand against real Postgres's raw-SQL fast path. This
checks the API surface: campaign stats, the per-advert media report, a booking's report, and
the admin tenant page's 30-day tile all expose online_plays/offline_plays, and online plus
offline always add back up to the total.
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-offline-reporting-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-offline-reporting")
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
    db = database.SessionLocal()
    plan = models.Plan(
        name="Full", slug=f"full-{uuid.uuid4().hex[:8]}",
        feature_flags_json=json.dumps({"ads_counting": True, "pdf_reports": True}),
        is_active=True, max_storage_bytes=10 * 1024 ** 3,
    )
    db.add(plan)
    db.flush()
    org = models.Organization(name="Reporting Co", slug=f"org-{uuid.uuid4().hex[:8]}", status="active", plan_id=plan.id)
    db.add(org)
    db.flush()
    admin_org = models.Organization(name="Platform", slug=f"platform-{uuid.uuid4().hex[:8]}", status="active")
    db.add(admin_org)
    db.flush()
    owner = models.User(organization_id=org.id, username=f"owner-{uuid.uuid4().hex[:6]}", email=None,
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    super_admin = models.User(organization_id=admin_org.id, username=f"admin-{uuid.uuid4().hex[:6]}", email=None,
                              hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    content = models.Content(organization_id=org.id, type="image", file_url="/uploads/1/a.jpg", name="Ad")
    screen = models.Screen(organization_id=org.id, device_id=f"d-{uuid.uuid4().hex[:8]}", name="Lobby", status="online")
    campaign = models.Campaign(organization_id=org.id, name="Launch")
    db.add_all([owner, super_admin, content, screen, campaign])
    db.flush()
    now = models.utcnow()
    placement = models.AdPlacement(
        organization_id=org.id, content_id=content.id, advertiser="Acme",
        starts_at=now - timedelta(days=5), ends_at=now + timedelta(days=5),
    )
    db.add(placement)
    db.flush()
    db.add(models.AdPlacementTarget(placement_id=placement.id, screen_id=screen.id))

    # 3 online, 1 offline -- in the current hour, inside both the placement's window and the
    # admin's 30-day lookback.
    hour = now.replace(minute=0, second=0, microsecond=0)
    db.add_all([
        models.PlayLogHourlyRollup(organization_id=org.id, campaign_id=campaign.id, screen_id=screen.id,
                                   media_id=content.id, date_hour=hour,
                                   total_plays=4, completed_plays=4, offline_plays=1),
    ])
    db.commit()
    owner_token, admin_token = login_token(owner), login_token(super_admin)
    org_id, campaign_id, content_id, placement_id, screen_id = org.id, campaign.id, content.id, placement.id, screen.id
    db.close()

    client = TestClient(app)
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    admin_headers = {"Authorization": f"Bearer {admin_token}"}

    def check_split(label, body):
        check(body.get("total_plays") == 4, f"{label}: expected total_plays=4, got {body.get('total_plays')}")
        check(body.get("offline_plays") == 1, f"{label}: expected offline_plays=1, got {body.get('offline_plays')}")
        check(body.get("online_plays") == 3, f"{label}: expected online_plays=3, got {body.get('online_plays')}")
        check(body.get("online_plays", 0) + body.get("offline_plays", 0) == body.get("total_plays", -1),
             f"{label}: online + offline should equal total, got {body}")

    stats = client.get(f"/api/analytics/campaigns/{campaign_id}/stats", headers=owner_headers)
    check(stats.status_code == 200, stats.text)
    if stats.status_code == 200:
        check_split("campaign lifetime", stats.json().get("lifetime", {}))

    media = client.get(f"/api/analytics/media/{content_id}", headers=owner_headers)
    check(media.status_code == 200, media.text)
    if media.status_code == 200:
        body = media.json()
        check_split("media lifetime", body.get("lifetime", {}))
        per_screen = body.get("per_screen", [])
        row = next((r for r in per_screen if r.get("screen_id") == screen_id), None)
        check(row is not None and row.get("offline_plays") == 1, f"per-screen row should carry the offline count: {row}")

    report = client.get(f"/api/placements/{placement_id}/report", headers=owner_headers)
    check(report.status_code == 200, report.text)
    if report.status_code == 200:
        check_split("booking report totals", report.json().get("totals", {}))

    plays = client.get(f"/api/admin/tenants/{org_id}/plays", headers=admin_headers)
    check(plays.status_code == 200, plays.text)
    if plays.status_code == 200:
        check_split("admin tenant 30-day tile", plays.json())

    refused = client.get(f"/api/admin/tenants/{org_id}/plays", headers=owner_headers)
    check(refused.status_code == 403, f"an ordinary owner should not reach the admin plays route, got {refused.status_code}")

    database.engine.dispose()


if __name__ == "__main__":
    try:
        run()
    finally:
        _db_file.unlink(missing_ok=True)
    if FAILURES:
        print("FAIL")
        for failure in FAILURES:
            print("  -", failure)
        sys.exit(1)
    print("OK - online/offline play counts reach the campaign, media, booking-report and admin tenant surfaces")
