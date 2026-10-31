"""A booking sold by per-location days is as long as its longest location.

The reported case: sell one screen 10 days and another 30, without naming a plan or an end
date. The campaign IS 30 days -- that is when the last location stops -- so the booking has
to be sold as 30, not as whatever date happened to be in the form.

The running side was always right: place_advert gives each target its own window and
AdPlacement.effective_ends_at takes the max of the booking, its extensions and every
per-location window. What was wrong was the SOLD window, which the operator, the invoice
and the bookings list all read.

Deliberately not changed: a sold window is never rewritten AFTER the fact. Selling a
location a longer run later leaves the original deal alone -- test_per_location_ad_window
covers that, and it stays true. This is only about getting it right at the moment of sale.

Throwaway Postgres database. Run directly:
    python tests/test_booking_window_follows_days.py
"""
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCRATCH = f"mr_window_{uuid.uuid4().hex[:8]}"
os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{SCRATCH}"
os.environ["SECRET_KEY"] = "window-test-secret"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"

import psycopg2  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

admin = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
admin.cursor().execute(f'CREATE DATABASE "{SCRATCH}" OWNER olrac')

db = None
try:
    from fastapi.testclient import TestClient  # noqa: E402
    from backend import models  # noqa: E402
    from backend.database import SessionLocal, engine  # noqa: E402
    from backend.main import app  # noqa: E402
    from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402

    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    org = models.Organization(name="Acme", slug="acme", status="active")
    db.add(org); db.commit()
    owner = models.User(organization_id=org.id, username="owner@acme.test",
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add(owner); db.commit()

    ad = models.Content(organization_id=org.id, type="video", file_url="/uploads/1/a.mp4",
                        name="Summer Sale", status="ready", duration_ms=30_000)
    db.add(ad); db.commit()

    shop = models.Screen(organization_id=org.id, name="Shop", status="online")
    mall = models.Screen(organization_id=org.id, name="Mall", status="online")
    db.add_all([shop, mall]); db.commit()

    http = TestClient(app)
    auth = {"Authorization": f"Bearer {create_access_token(data={'sub': owner.username})}"}
    now = models.utcnow()

    def sold_days(payload) -> int:
        start = payload["starts_at"]
        end = payload["ends_at"]
        from datetime import datetime
        return round((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 86400)

    # --- no end date, no plan: the longest location is the campaign --------------------
    created = http.post("/api/placements/", headers=auth, json={
        "content_id": ad.id, "advertiser": "Brightmart", "price_paise": 500000,
        "starts_at": now.isoformat(),
        "targets": [{"screen_id": shop.id, "days": 10}, {"screen_id": mall.id, "days": 30}],
    })
    assert created.status_code == 201, created.text
    booking = created.json()
    assert sold_days(booking) == 30, (
        "a booking whose longest location runs 30 days must be SOLD as 30, not as whatever "
        f"date was lying around: got {sold_days(booking)}"
    )
    print("  ok  with no end date given, the booking is sold as long as its longest location")

    # Each location still keeps the length IT was sold -- the campaign length must not be
    # flattened onto every screen.
    by_screen = {t["screen_id"]: t for t in booking["targets"]}
    assert by_screen[shop.id]["days"] == 10, by_screen[shop.id]
    assert by_screen[mall.id]["days"] == 30, by_screen[mall.id]
    print("  ok  each location still runs exactly the length it was sold")

    # And the screens themselves carry those windows, or the advert plays the wrong run.
    db.expire_all()
    targets = db.query(models.AdPlacementTarget).filter(
        models.AdPlacementTarget.placement_id == booking["id"]).all()
    for target in targets:
        item = db.query(models.PlaylistItem).filter(
            models.PlaylistItem.id == target.playlist_item_id).one()
        ran = round((item.end_at - item.start_at).total_seconds() / 86400)
        expected = 10 if target.screen_id == shop.id else 30
        assert ran == expected, (
            f"screen {target.screen_id} plays for {ran} days but was sold {expected}"
        )
    print("  ok  the playlist item on each screen carries that screen's own window")

    # --- an explicit end date is still obeyed exactly ----------------------------------
    # Its own creative: one advert carries one live booking (placements.create_placement),
    # and the sale above is still running.
    ad2 = models.Content(organization_id=org.id, type="video", file_url="/uploads/1/b.mp4",
                         name="Handshake Offer", status="ready", duration_ms=30_000)
    db.add(ad2); db.commit()
    fixed = http.post("/api/placements/", headers=auth, json={
        "content_id": ad2.id, "advertiser": "Handshake", "price_paise": 100000,
        "starts_at": now.isoformat(),
        "ends_at": (now + timedelta(days=45)).isoformat(),
        "targets": [{"screen_id": shop.id, "days": 10}],
    })
    assert fixed.status_code == 201, fixed.text
    assert sold_days(fixed.json()) == 45, (
        "an end date the operator stated must win over the per-location days: got "
        f"{sold_days(fixed.json())}"
    )
    print("  ok  an end date stated outright is obeyed, not overwritten by the days")

    # --- a booking with no length anywhere is still refused ----------------------------
    naked = http.post("/api/placements/", headers=auth, json={
        "content_id": ad2.id, "advertiser": "Nolength", "price_paise": 0,
        "starts_at": now.isoformat(),
        "targets": [{"screen_id": shop.id}],
    })
    assert naked.status_code == 422, (
        f"a booking with no end date, no plan and no days must be refused: {naked.status_code}"
    )
    print("  ok  a booking with no length stated anywhere is still refused")

    # --- the campaign's reported finish covers its longest location --------------------
    # effective_ends_at is what the alerts, the report and the expiry sweep read.
    db.expire_all()
    placement = db.query(models.AdPlacement).filter(
        models.AdPlacement.id == booking["id"]).one()
    reported = round((placement.effective_ends_at - placement.starts_at).total_seconds() / 86400)
    assert reported == 30, f"effective end should cover the 30-day location, got {reported}"
    print("  ok  the campaign's reported finish covers its longest location")

    print("booking window follows days: all checks passed")
finally:
    try:
        if db: db.close()
    except Exception:
        pass
    try:
        engine.dispose()
    except Exception:
        pass
    try:
        admin.cursor().execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')
    except Exception:
        pass
    admin.close()
