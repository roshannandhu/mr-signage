"""The media supervisor only takes videos that really look stuck, and takes each one once.

It used to re-run transcoding for EVERY video in "processing" every 30 seconds, with no age check
and no claim: a video that was simply still being transcoded got a second concurrent run, each
inserting its own renditions, so every resolution was stored twice and storage counted both.
Also pins how a failed transcode is treated: a dropped connection to the bucket leaves the video in
"processing" to be retried (it used to mark it permanently failed), a file ffmpeg cannot read still
fails at once, and a video that can never finish is given up on after a few attempts rather than
being retried every ten minutes for ever.
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
from backend import media_storage  # noqa: E402
from backend.worker import (  # noqa: E402
    MAX_PROCESSING_ATTEMPTS,
    STUCK_AFTER,
    claim_stuck_media,
    is_transient_storage_error,
    process_media_sync,
)

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

    # --- each take-over is an attempt, and a video that never finishes is given up on ---------------
    db.expire_all()
    check(db.query(models.Content).filter(models.Content.id == ids["old"]).one().processing_retries == 1,
          "taking over a stuck video did not count an attempt")
    doomed = video("doomed", "processing", now - STUCK_AFTER - timedelta(minutes=5))
    db.query(models.Content).filter(models.Content.id == doomed).update({"processing_retries": MAX_PROCESSING_ATTEMPTS})
    db.commit()
    check(doomed not in claim_stuck_media(db), "a video past its attempt limit was handed out again")
    db.expire_all()
    gave_up = db.query(models.Content).filter(models.Content.id == doomed).one()
    check(gave_up.status == "failed" and gave_up.failed_reason, f"a video past its limit was not failed: {gave_up.status!r}")
    db.close()


def run_error_handling() -> None:
    from botocore.exceptions import ClientError, EndpointConnectionError
    import subprocess

    # --- which exceptions are worth another try -----------------------------------------------------
    check(is_transient_storage_error(EndpointConnectionError(endpoint_url="https://r2")), "a connection error is transient")
    check(is_transient_storage_error(TimeoutError()), "a timeout is transient")
    check(is_transient_storage_error(ClientError({"Error": {"Code": "SlowDown"}, "ResponseMetadata": {"HTTPStatusCode": 503}}, "GetObject")),
          "a 503 from the bucket is transient")
    check(not is_transient_storage_error(ClientError({"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}}, "GetObject")),
          "access denied is not transient")
    check(not is_transient_storage_error(ValueError("bad")), "a ValueError is not transient")
    check(not is_transient_storage_error(subprocess.CalledProcessError(1, "ffmpeg")), "an ffmpeg failure is not transient")

    # --- what process_media_sync does with each ------------------------------------------------------
    db = database.SessionLocal()
    org = models.Organization(name="Venue", slug=f"e-{uuid.uuid4().hex[:8]}", status="active")
    db.add(org)
    db.flush()
    started = models.utcnow()
    dropped = models.Content(organization_id=org.id, type="video", file_url="/uploads/1/dropped.mp4",
                             name="dropped", status="processing", processing_started_at=started)
    broken = models.Content(organization_id=org.id, type="video", file_url="/uploads/1/broken.mp4",
                            name="broken", status="processing", processing_started_at=started)
    db.add_all([dropped, broken])
    db.commit()
    dropped_id, broken_id = dropped.id, broken.id
    db.close()

    original = media_storage.fetch_to
    try:
        def fail_with(error):
            def fetch(*_args, **_kwargs):
                raise error
            return fetch

        media_storage.fetch_to = fail_with(EndpointConnectionError(endpoint_url="https://r2"))
        process_media_sync(dropped_id)
        media_storage.fetch_to = fail_with(RuntimeError("ffmpeg could not read this file"))
        process_media_sync(broken_id)
    finally:
        media_storage.fetch_to = original

    db = database.SessionLocal()
    check(db.query(models.Content).filter(models.Content.id == dropped_id).one().status == "processing",
          "a dropped connection permanently failed the video instead of leaving it to be retried")
    unreadable = db.query(models.Content).filter(models.Content.id == broken_id).one()
    check(unreadable.status == "failed" and "ffmpeg" in (unreadable.failed_reason or ""),
          f"an unreadable file should still fail at once with its reason: {unreadable.status!r}")
    db.close()


if __name__ == "__main__":
    try:
        run()
        run_error_handling()
    finally:
        database.engine.dispose()
        _db_file.unlink(missing_ok=True)
    if FAILURES:
        print("FAIL")
        for failure in FAILURES:
            print("  -", failure)
        sys.exit(1)
    print("OK - the supervisor takes only stuck videos, and takes each one once")
