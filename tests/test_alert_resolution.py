"""An alert closes when its cause goes away, even with no arq worker running.

The in-process health monitor used to raise "screen is offline" and then never resolve it: only the
arq reconciler did, and arq needs Redis. Without Redis a screen that came back stayed offline on the
dashboard for ever. Both now go through reconcile_org_alerts, and the monitor's pass looks at every
organisation that still has an open alert, not just the ones where a screen just dropped.

Pins screen_health_pass():
  * a silent screen is marked offline and raises exactly one alert
  * a second pass raises nothing more
  * when the screen is back, the next pass resolves the alert although nothing went stale
  * a booking alert is not closed just because the monitor evaluated the organisation
  * a screen that belongs to no organisation does not break the pass
and that the arq job, which shares the same function, still raises and resolves.
"""
import asyncio
import json
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-alerts-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-alert-resolution")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from backend import database, models  # noqa: E402
import backend.main  # noqa: E402,F401  (import order; see test_subscription_expiry)
from backend.worker import alert_event_payload, reconcile_alerts, screen_health_pass  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def open_kinds(db, org_id):
    db.expire_all()
    return sorted(a.kind for a in db.query(models.Alert).filter(
        models.Alert.organization_id == org_id, models.Alert.resolved_at.is_(None)))


def run() -> None:
    db = database.SessionLocal()
    org = models.Organization(name="Venue", slug=f"v-{uuid.uuid4().hex[:8]}", status="active")
    db.add(org)
    db.flush()
    screen = models.Screen(organization_id=org.id, device_id=f"d-{uuid.uuid4().hex[:8]}", name="Lobby",
                           status="online", last_seen=models.utcnow() - timedelta(minutes=10))
    # Online and silent, but not part of any organisation: nothing to alert, and nobody to tell.
    orphan = models.Screen(organization_id=None, device_id=f"d-{uuid.uuid4().hex[:8]}", name="Unpaired",
                           status="online", last_seen=models.utcnow() - timedelta(minutes=10))
    db.add_all([screen, orphan])
    db.commit()
    org_id, screen_id = org.id, screen.id

    def now():
        return datetime.now(timezone.utc)

    # --- the screen goes quiet -----------------------------------------------------------------
    changes = screen_health_pass(db, now())
    db.expire_all()
    check(db.query(models.Screen).filter(models.Screen.id == screen_id).one().status == "offline",
          "a silent screen was not marked offline")
    raised = [c for c in changes if c[1] == "alert_raised"]
    check(len(raised) == 1 and raised[0][0] == org_id and raised[0][2].kind == "screen_offline",
          f"expected one screen_offline alert for the org, got {[(c[0], c[1], c[2].kind) for c in changes]}")
    check(open_kinds(db, org_id) == ["screen_offline"], f"open alerts: {open_kinds(db, org_id)}")

    check(screen_health_pass(db, now()) == [], "a second pass over the same situation raised or resolved something")

    # --- a booking alert exists too; it must outlive the screen's alert -------------------------
    content = models.Content(organization_id=org_id, name="Ad", type="video", file_url="/uploads/1/ad.mp4")
    db.add(content)
    db.flush()
    placement = models.AdPlacement(organization_id=org_id, content_id=content.id, advertiser="Acme",
                                   starts_at=models.utcnow() - timedelta(days=1),
                                   ends_at=models.utcnow() + timedelta(days=3))
    db.add(placement)
    db.commit()
    changes = screen_health_pass(db, now())
    check(open_kinds(db, org_id) == ["campaign_ending", "screen_offline"],
          f"the booking alert was not raised alongside: {open_kinds(db, org_id)}")

    # --- the screen comes back: nothing is stale, but its alert must still close -----------------
    live = db.query(models.Screen).filter(models.Screen.id == screen_id).one()
    live.status, live.last_seen, live.playback_state = "online", models.utcnow(), "playing"
    db.commit()
    changes = screen_health_pass(db, now())
    check([(c[1], c[2].kind) for c in changes] == [("alert_resolved", "screen_offline")],
          f"coming back should resolve exactly the offline alert, got {[(c[1], c[2].kind) for c in changes]}")
    check(open_kinds(db, org_id) == ["campaign_ending"],
          f"the resolve closed too much or too little: {open_kinds(db, org_id)}")

    # --- the booking is extended past the warning window: now that one closes as well ------------
    booking = db.query(models.AdPlacement).filter(models.AdPlacement.id == placement.id).one()
    booking.ends_at = models.utcnow() + timedelta(days=60)
    db.commit()
    screen_health_pass(db, now())
    check(open_kinds(db, org_id) == [], f"an extended booking's alert stayed open: {open_kinds(db, org_id)}")

    # --- what a dashboard receives -------------------------------------------------------------
    sample = db.query(models.Alert).filter(models.Alert.organization_id == org_id).first()
    body = json.loads(alert_event_payload("alert_resolved", sample))
    check(body["type"] == "alert_resolved" and body["alert"]["id"] == sample.id and body["alert"]["kind"] == sample.kind,
          f"unexpected event payload: {body}")

    # --- the arq job shares the logic and still raises and resolves ------------------------------
    live = db.query(models.Screen).filter(models.Screen.id == screen_id).one()
    live.status, live.last_seen = "offline", models.utcnow() - timedelta(minutes=10)
    db.commit()
    asyncio.run(reconcile_alerts({}))
    check(open_kinds(db, org_id) == ["screen_offline"], f"the arq job did not raise: {open_kinds(db, org_id)}")
    live = db.query(models.Screen).filter(models.Screen.id == screen_id).one()
    live.status, live.last_seen, live.playback_state = "online", models.utcnow(), "playing"
    db.commit()
    asyncio.run(reconcile_alerts({}))
    check(open_kinds(db, org_id) == [], f"the arq job did not resolve: {open_kinds(db, org_id)}")
    db.close()


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
    print("OK - alerts close when their cause goes away, with or without the arq worker")
