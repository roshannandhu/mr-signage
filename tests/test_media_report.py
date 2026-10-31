"""Per-advert proof-of-play report.

Uses a throwaway Postgres database — never the live one. Run directly:
    python tests/test_media_report.py
"""
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Point every module at a scratch database before anything imports the engine.
ADMIN_URL = os.getenv("DATABASE_URL", "postgresql://olrac:olrac_password@localhost:5432/olrac_signage")
SCRATCH = f"olrac_mediareport_{uuid.uuid4().hex[:8]}"
base, _, _ = ADMIN_URL.rpartition("/")
os.environ["DATABASE_URL"] = f"{base}/{SCRATCH}"

import psycopg2  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

# The app role cannot create databases, so the superuser does it and hands ownership over
# — same bootstrap the tenant-isolation probe uses.
admin_conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
admin_conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
admin_conn.cursor().execute(f'DROP DATABASE IF EXISTS "{SCRATCH}"')
admin_conn.cursor().execute(f'CREATE DATABASE "{SCRATCH}" OWNER olrac')

try:
    from fastapi.testclient import TestClient  # noqa: E402
    from backend import models  # noqa: E402
    from backend.database import SessionLocal, engine  # noqa: E402
    from backend.main import app  # noqa: E402
    from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402
    from tests._test_plans import grant_plan  # noqa: E402

    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    org = models.Organization(name="Acme Media", slug="acme")
    other = models.Organization(name="Rival Media", slug="rival")
    db.add_all([org, other]); db.flush()
    # The media report is sold behind a flag. Rival needs it too, so its cross-tenant read
    # below is refused for being the wrong tenant (404), not merely for lacking the flag (403).
    grant_plan(db, org, ads_counting=True)
    grant_plan(db, other, ads_counting=True)
    db.commit()

    user = models.User(organization_id=org.id, username="owner@acme.test",
                       hashed_password=get_password_hash("x"), role="owner", is_active=True)
    intruder = models.User(organization_id=other.id, username="owner@rival.test",
                           hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add_all([user, intruder]); db.commit()

    ad = models.Content(organization_id=org.id, type="video", file_url="http://x/a.mp4",
                        name="Summer Sale", status="ready")
    db.add(ad); db.commit()

    # A "place" is a screen group, so two screens in one group must roll up together.
    mall = models.ScreenGroup(organization_id=org.id, name="Phoenix Mall")
    airport_grp = models.ScreenGroup(organization_id=org.id, name="City Airport")
    db.add_all([mall, airport_grp]); db.commit()

    mall_a = models.Screen(organization_id=org.id, name="Food Court TV", group_id=mall.id, status="online")
    mall_b = models.Screen(organization_id=org.id, name="Entrance TV", group_id=mall.id, status="online")
    airport = models.Screen(organization_id=org.id, name="Gate 4 TV", group_id=airport_grp.id, status="offline")
    ungrouped = models.Screen(organization_id=org.id, name="Spare TV", status="offline")
    db.add_all([mall_a, mall_b, airport, ungrouped]); db.commit()

    now = models.utcnow()
    rollups = [
        # (screen, hours_ago, total, completed, errors)
        (mall_a, 1, 10, 9, 1),
        (mall_a, 2, 5, 5, 0),
        (mall_b, 3, 7, 7, 0),
        (airport, 40, 100, 90, 10),   # older than today, inside the month
        (ungrouped, 5, 3, 3, 0),      # no location and no group — must still be reported
    ]
    for screen, hours, total, completed, errors in rollups:
        db.add(models.PlayLogHourlyRollup(
            organization_id=org.id, screen_id=screen.id, media_id=ad.id,
            date_hour=now - timedelta(hours=hours),
            total_plays=total, completed_plays=completed,
            partial_plays=0, error_plays=errors,
        ))
    db.commit()

    client = TestClient(app)
    auth = {"Authorization": f"Bearer {create_access_token(data={'sub': user.username})}"}
    response = client.get(f"/api/analytics/media/{ad.id}", headers=auth)
    assert response.status_code == 200, response.text
    report = response.json()

    # Lifetime covers every row.
    assert report["lifetime"]["total_plays"] == 125, report["lifetime"]
    assert report["lifetime"]["completed_plays"] == 114, report["lifetime"]
    assert report["lifetime"]["error_plays"] == 11, report["lifetime"]

    # Per-screen: three screens, ordered by plays descending.
    assert [row["screen_name"] for row in report["per_screen"]][:3] == ["Gate 4 TV", "Food Court TV", "Entrance TV"], report["per_screen"]
    food_court = next(r for r in report["per_screen"] if r["screen_name"] == "Food Court TV")
    assert food_court["total_plays"] == 15, food_court
    assert food_court["group_name"] == "Phoenix Mall", food_court

    # Per-place: the two mall screens roll up into one place.
    #
    # Keyed on the screen's location, falling back to its group and then to a placeholder.
    # This assertion used to expect "Ungrouped screens", from when the report keyed on the
    # screen *group* -- a playlist-sharing construct with nothing to do with geography, so
    # two screens in one mall in different groups were reported as two separate places.
    # The grouping was fixed; this file was not in any collection list at the time, so it
    # never ran again and kept asserting the old behaviour. See routers/analytics.py.
    places = {row["location"]: row for row in report["per_location"]}
    assert set(places) == {"Phoenix Mall", "City Airport", "No location set"}, places
    assert places["Phoenix Mall"]["screens"] == 2
    assert places["Phoenix Mall"]["total_plays"] == 22, places["Phoenix Mall"]
    assert places["City Airport"]["total_plays"] == 100
    # A screen with neither location nor group still has to appear, or its plays vanish.
    assert places["No location set"]["total_plays"] == 3, places["No location set"]

    # Success percentage is completed / total, not completed / attempts.
    assert report["lifetime"]["success_percent"] == round(114 / 125 * 100, 1)

    assert report["daily"], "expected a timeseries"
    print("  ok  totals, per-screen, per-location (incl. unplaced) and timeseries")

    # Another tenant must not be able to read this advert's numbers.
    rival = {"Authorization": f"Bearer {create_access_token(data={'sub': intruder.username})}"}
    cross = client.get(f"/api/analytics/media/{ad.id}", headers=rival)
    assert cross.status_code == 404, f"cross-tenant read returned {cross.status_code}"
    print("  ok  cross-tenant read is refused")

    print("media report: all checks passed")
finally:
    # Return every pooled connection before the database is dropped, otherwise the
    # terminate below yanks them and SQLAlchemy logs a reset failure over the results.
    try:
        db.close()
    except Exception:
        pass
    engine.dispose()
    cur = admin_conn.cursor()
    cur.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{SCRATCH}'")
    cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}"')
    admin_conn.close()
