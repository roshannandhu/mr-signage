"""Every route Phase 3.1 sold behind a new plan flag actually refuses a plan without it.

payslips, pdf_reports, ads_counting and remote_control each gate a cluster of routes. This
builds one workspace holding none of the five new flags and one holding all of them, and
drives every gated action against both: the bare workspace gets 403 naming the flag, the
full one gets through. kiosk_lock has no route yet (it belongs to the TV lock feature, 3.10)
so it is not exercised here -- it only needs to exist in the plan model and the admin UI.
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-feature-gates-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-feature-gates")
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


def workspace(label: str, flags: dict[str, bool]):
    """One owner with a screen, a booked placement with one payment, and a campaign
    carrying a real play-log rollup -- everything every gated route needs to render."""
    db = database.SessionLocal()
    plan = models.Plan(
        name=label, slug=f"{label}-{uuid.uuid4().hex[:8]}",
        feature_flags_json=json.dumps(flags), is_active=True, max_storage_bytes=10 * 1024 ** 3,
    )
    db.add(plan)
    db.flush()
    org = models.Organization(name=label, slug=f"{label}-{uuid.uuid4().hex[:8]}", status="active", plan_id=plan.id)
    db.add(org)
    db.flush()
    owner = models.User(organization_id=org.id, username=f"owner-{uuid.uuid4().hex[:6]}", email=None,
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add(owner)
    screen = models.Screen(organization_id=org.id, device_id=f"d-{uuid.uuid4().hex[:8]}", name="Lobby", status="online")
    content = models.Content(organization_id=org.id, type="image", file_url="/uploads/1/a.jpg", name="Ad")
    playlist = models.Playlist(organization_id=org.id, name="Loop")
    campaign = models.Campaign(organization_id=org.id, name="Launch")
    db.add_all([screen, content, playlist, campaign])
    db.flush()
    now = models.utcnow()
    client = models.Client(organization_id=org.id, name="Acme", client_code=f"C{uuid.uuid4().hex[:6].upper()}", email="acme@example.com")
    db.add(client)
    db.flush()
    placement = models.AdPlacement(
        organization_id=org.id, content_id=content.id, advertiser="Acme", client_id=client.id,
        starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=10),
    )
    db.add(placement)
    db.flush()
    payment = models.AdPayment(organization_id=org.id, placement_id=placement.id, amount_paise=1000, method="upi")
    rollup = models.PlayLogHourlyRollup(
        organization_id=org.id, campaign_id=campaign.id, screen_id=screen.id, media_id=content.id,
        date_hour=now.replace(minute=0, second=0, microsecond=0),
        total_plays=5, completed_plays=5, error_plays=0,
    )
    db.add_all([payment, rollup])
    db.commit()
    token = login_token(owner)
    ids = {
        "screen": screen.id, "placement": placement.id, "payment": payment.id,
        "campaign": campaign.id, "content": content.id,
    }
    db.close()
    return token, ids


def expect(label, allowed, resp):
    if allowed:
        check(resp.status_code not in (401, 403), f"{label}: should have been allowed, got {resp.status_code}: {resp.text[:200]}")
    else:
        check(resp.status_code == 403, f"{label}: should have been refused, got {resp.status_code}: {resp.text[:200]}")


def drive(client, headers, ids, has: dict[str, bool]):
    def call(label, flag, method, url, **kw):
        resp = getattr(client, method)(url, headers=headers, **kw)
        expect(label, has.get(flag, False), resp)

    pid, cid, sid = ids["placement"], ids["campaign"], ids["screen"]

    # payslips
    call("record_payment", "payslips", "post", f"/api/placements/{pid}/payments",
        json={"amount_paise": 500, "method": "cash"})
    call("edit_payment", "payslips", "patch", f"/api/placements/{pid}/payments/{ids['payment']}",
        json={"amount_paise": 1000})
    call("download_invoice", "payslips", "get", f"/api/placements/{pid}/invoice.pdf")
    call("email_invoice", "payslips", "post", f"/api/placements/{pid}/email?document=invoice")
    call("delete_payment", "payslips", "delete", f"/api/placements/{pid}/payments/{ids['payment']}")
    call("clear_payment", "payslips", "delete", f"/api/placements/{pid}/payments")

    # pdf_reports
    call("download_report", "pdf_reports", "get", f"/api/placements/{pid}/report.pdf")
    call("email_report", "pdf_reports", "post", f"/api/placements/{pid}/email?document=report")
    call("export_pdf", "pdf_reports", "get", f"/api/analytics/campaigns/{cid}/export?format=pdf")

    # ads_counting
    call("campaign_stats", "ads_counting", "get", f"/api/analytics/campaigns/{cid}/stats")
    call("campaign_timeseries", "ads_counting", "get", f"/api/analytics/campaigns/{cid}/timeseries")
    call("media_report", "ads_counting", "get", f"/api/analytics/media/{ids['content']}")
    call("export_csv", "ads_counting", "get", f"/api/analytics/campaigns/{cid}/export?format=csv")

    # remote_control
    call("bring_to_front", "remote_control", "post", f"/api/screens/{sid}/bring-to-front")

    # campaign info and listing stay open regardless -- status, not counting
    resp = client.get(f"/api/analytics/campaigns/{cid}", headers=headers)
    check(resp.status_code == 200, f"campaign info should stay ungated: {resp.status_code}")


def run() -> None:
    client = TestClient(app)

    bare_token, bare_ids = workspace("bare", {})
    drive(client, {"Authorization": f"Bearer {bare_token}"}, bare_ids, has={})

    full_flags = {"payslips": True, "pdf_reports": True, "ads_counting": True, "remote_control": True}
    full_token, full_ids = workspace("full", full_flags)
    drive(client, {"Authorization": f"Bearer {full_token}"}, full_ids, has=full_flags)


if __name__ == "__main__":
    try:
        run()
    finally:
        database.engine.dispose()
        _db_file.unlink(missing_ok=True)
    if FAILURES:
        print("FAIL")
        for failure in FAILURES:
            print("  -", failure)
        sys.exit(1)
    print("OK - every Phase 3.1 plan-gated route refuses a plan without the flag and allows one with it")
