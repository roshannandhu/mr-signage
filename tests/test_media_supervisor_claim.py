"""The media supervisor only takes videos that really look stuck, and takes each one once.

It used to re-run transcoding for EVERY video in "processing" every 30 seconds, with no age check
and no claim: a video that was simply still being transcoded got a second concurrent run, each
inserting its own renditions, so every resolution was stored twice and storage counted both.
Pins claim_stuck_media():
  * a video that started processing a moment ago is left alone
  * one that started long ago, or was never stamped, is taken
  * a second claim straight afterwards takes nothing (the first one re-stamped them)
  * finished and failed videos are never taken
"""
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-supervisor-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-media-supervisor")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from backend import database, models  # noqa: E402
import backend.main  # noqa: E402,F401  (import order; see test_subscription_expiry)
from backend.worker import STUCK_AFTER, claim_stuck_media  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run() -> None:
    db = database.SessionLocal()
    org = models.Organization(name="Venue", slug=f"v-{uuid.uuid4().hex[:8]}", status="active")
    db.add(org)
    db.flush()
    now = models.utcnow()

    def video(name, status, started):
        item = models.Content(organization_id=org.id, type="video", file_url=f"/uploads/1/{name}.mp4",
                              name=name, status=status, processing_started_at=started)
        db.add(item)
        db.flush()
        return item.id

    ids = {
        "fresh": video("fresh", "processing", now - timedelta(minutes=1)),
        "old": video("old", "processing", now - STUCK_AFTER - timedelta(minutes=5)),
        "never": video("never", "processing", None),
        "done": video("done", "ready", now - timedelta(hours=3)),
        "failed": video("failed", "failed", now - timedelta(hours=3)),
    }
    db.commit()

    claimed = claim_stuck_media(db)
    check(set(claimed) == {ids["old"], ids["never"]}, f"wrong set taken: {sorted(claimed)}, expected old + never")
    check(ids["fresh"] not in claimed, "a video that started a minute ago was treated as stuck")
    check(ids["done"] not in claimed and ids["failed"] not in claimed, "a finished or failed video was taken")

    again = claim_stuck_media(db)
    check(again == [], f"a second claim straight afterwards took {again}; the first should have re-stamped them")

    stamped = db.query(models.Content).filter(models.Content.id == ids["never"]).one().processing_started_at
    check(stamped is not None, "a claimed video was not given a start time")
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
    print("OK - the supervisor takes only stuck videos, and takes each one once")
