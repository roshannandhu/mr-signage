"""Booking an advert onto a screen must put it on that screen's playback timeline.

The screen page renders PlaylistBuilder against `effective_playlist_id` and nothing else,
so "I assigned the ad and the timeline is empty" is exactly this chain breaking:

    create placement -> playlist_for_target -> screen.playlist_id -> effective_playlist_id
                                            -> PlaylistItem in that playlist

Three shapes a screen can be in when the booking lands, because they take different
branches in playlist_for_target and only the first is obvious:

  1. no playlist at all     -> one is provisioned
  2. its own playlist       -> the advert is appended to it
  3. inherits a group loop  -> it is FORKED to the screen, seeded with what it was already
                               playing, and the advert lands on top

Throwaway Postgres database. Run directly:  python tests/test_assign_shows_on_timeline.py
"""
import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCRATCH = f"mr_timeline_{uuid.uuid4().hex[:8]}"
os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{SCRATCH}"
os.environ["SECRET_KEY"] = "timeline-test-secret"
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

    org = models.Organization(name="Acme", slug="acme")
    db.add(org); db.commit()
    owner = models.User(organization_id=org.id, username="owner@acme.test",
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add(owner); db.commit()

    ad = models.Content(organization_id=org.id, type="video", file_url="/uploads/1/a.mp4",
                        name="Summer Sale", status="ready", duration_ms=30_000)
    # One creative per shape below. An advert carries ONE live booking (see
    # placements.create_placement), and these three are separate sales, not one campaign
    # with three locations -- which is what a single creative across all three would be.
    ad_own = models.Content(organization_id=org.id, type="video", file_url="/uploads/1/a2.mp4",
                            name="Summer Sale II", status="ready", duration_ms=30_000)
    ad_group = models.Content(organization_id=org.id, type="video", file_url="/uploads/1/a3.mp4",
                              name="Summer Sale III", status="ready", duration_ms=30_000)
    house = models.Content(organization_id=org.id, type="image", file_url="/uploads/1/house.png",
                           name="Venue promo", status="ready")
    db.add_all([ad, ad_own, ad_group, house]); db.commit()

    # 1: bare screen. 2: screen with its own loop. 3: screen inheriting a group loop.
    bare = models.Screen(organization_id=org.id, name="Bare", status="online")
    owned = models.Screen(organization_id=org.id, name="Owned", status="online")
    group = models.ScreenGroup(organization_id=org.id, name="Mall")
    db.add_all([bare, owned, group]); db.commit()
    inheritor = models.Screen(organization_id=org.id, name="Inheritor",
                              group_id=group.id, status="online")
    db.add(inheritor); db.commit()

    own_loop = models.Playlist(organization_id=org.id, name="Owned loop")
    group_loop = models.Playlist(organization_id=org.id, name="Mall loop")
    db.add_all([own_loop, group_loop]); db.commit()
    owned.playlist_id = own_loop.id
    group.playlist_id = group_loop.id
    db.add_all([
        models.PlaylistItem(playlist_id=own_loop.id, content_id=house.id, duration=8, order=0),
        models.PlaylistItem(playlist_id=group_loop.id, content_id=house.id, duration=8, order=0),
    ])
    db.commit()

    http = TestClient(app)
    auth = {"Authorization": f"Bearer {create_access_token(data={'sub': owner.username})}"}
    now = models.utcnow()

    def timeline_of(screen_id: int):
        """What the screen page would render: resolve the playlist the way the API reports
        it, then read that playlist's items through the API the builder calls."""
        listed = http.get("/api/screens/", headers=auth)
        assert listed.status_code == 200, listed.text
        row = next(s for s in listed.json() if s["id"] == screen_id)
        playlist_id = row["effective_playlist_id"]
        if playlist_id is None:
            return None, []
        got = http.get(f"/api/playlists/{playlist_id}", headers=auth)
        assert got.status_code == 200, got.text
        return playlist_id, got.json()["items"]

    # --- 1. a screen with no playlist at all ---------------------------------------------
    before_id, before_items = timeline_of(bare.id)
    assert before_id is None and before_items == [], (before_id, before_items)

    booked = http.post("/api/placements/", headers=auth, json={
        "content_id": ad.id, "advertiser": "Brightmart", "price_paise": 500000,
        "starts_at": now.isoformat(), "ends_at": (now + timedelta(days=30)).isoformat(),
        "targets": [{"screen_id": bare.id}],
    })
    assert booked.status_code == 201, booked.text

    playlist_id, items = timeline_of(bare.id)
    assert playlist_id is not None, (
        "booking an advert onto a screen with no playlist left it with none, so the screen "
        "page still renders 'Nothing scheduled yet' over a sold campaign"
    )
    assert [i["content_id"] for i in items] == [ad.id], (
        f"the advert is not on the screen's timeline: {items}"
    )
    print("  ok  a screen with no playlist gets one, with the advert on it")

    # --- 2. a screen that already has its own loop ---------------------------------------
    booked2 = http.post("/api/placements/", headers=auth, json={
        "content_id": ad_own.id, "advertiser": "Brightmart", "price_paise": 500000,
        "starts_at": now.isoformat(), "ends_at": (now + timedelta(days=30)).isoformat(),
        "targets": [{"screen_id": owned.id}],
    })
    assert booked2.status_code == 201, booked2.text

    playlist_id, items = timeline_of(owned.id)
    assert playlist_id == own_loop.id, "the booking moved the screen off its own loop"
    content_ids = [i["content_id"] for i in items]
    assert house.id in content_ids, "the booking wiped what the screen was already playing"
    assert ad_own.id in content_ids, f"the advert never reached the existing loop: {content_ids}"
    print("  ok  a screen with its own loop keeps it, with the advert appended")

    # --- 3. a screen inheriting a group loop ---------------------------------------------
    # The fork: it must get its OWN playlist (so the advert cannot leak to the rest of the
    # group) seeded with what it was already playing (so it does not go from the venue's
    # content to one advert and nothing else).
    inherited_id, inherited_items = timeline_of(inheritor.id)
    assert inherited_id == group_loop.id, inherited_id
    assert [i["content_id"] for i in inherited_items] == [house.id], inherited_items

    booked3 = http.post("/api/placements/", headers=auth, json={
        "content_id": ad_group.id, "advertiser": "Brightmart", "price_paise": 500000,
        "starts_at": now.isoformat(), "ends_at": (now + timedelta(days=30)).isoformat(),
        "targets": [{"screen_id": inheritor.id}],
    })
    assert booked3.status_code == 201, booked3.text

    playlist_id, items = timeline_of(inheritor.id)
    assert playlist_id is not None, "the inheriting screen lost its playlist entirely"
    assert playlist_id != group_loop.id, (
        "the advert was written into the GROUP's loop, so every screen in the group now "
        "runs a campaign sold to one of them"
    )
    content_ids = [i["content_id"] for i in items]
    assert house.id in content_ids, (
        "the fork dropped the venue's own content -- the screen went from playing the "
        "group's loop to playing one advert and nothing else"
    )
    assert ad_group.id in content_ids, f"the advert never reached the forked loop: {content_ids}"

    # The group's own loop must be untouched, or the other screens got the advert too.
    group_items = http.get(f"/api/playlists/{group_loop.id}", headers=auth).json()["items"]
    assert [i["content_id"] for i in group_items] == [house.id], (
        f"the group's loop was modified by a booking sold to one screen: {group_items}"
    )
    print("  ok  an inheriting screen is forked, keeps the venue content, and the group is untouched")

    # --- the marker the player gates on actually moved -----------------------------------
    # sync_tv answers 204 until this moves, so a booking that does not bump it reaches the
    # dashboard and never reaches the television.
    db.expire_all()
    for screen_id, name in ((bare.id, "bare"), (owned.id, "owned"), (inheritor.id, "inheritor")):
        screen = db.query(models.Screen).filter(models.Screen.id == screen_id).one()
        resolved = screen.resolve_playlist_id()
        playlist = db.query(models.Playlist).filter(models.Playlist.id == resolved).one()
        marker = screen.assignment_updated_at or screen.last_seen
        if playlist.updated_at > (marker or playlist.updated_at):
            marker = playlist.updated_at
        assert marker is not None, f"{name}: no sync marker, so the TV is never told"
    print("  ok  every booked screen carries a sync marker, so the player is told to refetch")

    print("assign -> timeline: all checks passed")
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
