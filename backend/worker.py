import asyncio
import hashlib
import logging
import json
import os
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path

from arq import cron
from arq.connections import RedisSettings
from dotenv import load_dotenv

from .database import SessionLocal, REDIS_SETTINGS
from . import models

logger = logging.getLogger(__name__)
from .models import Content, MediaRendition
from . import media_storage
from .routers.content import UPLOAD_DIR  # noqa: F401  (re-exported for scripts)

import shutil
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError

project_root = Path(__file__).parent.parent
if not shutil.which("ffmpeg"):
    ffmpeg_bin = project_root / "ffmpeg_build" / "ffmpeg-master-latest-win64-gpl" / "bin"
    if ffmpeg_bin.exists():
        os.environ["PATH"] += os.pathsep + str(ffmpeg_bin)


# Two, not four. Every rendition is another object kept for the life of the asset, and
# four of them plus the retained source came to roughly 280MB for a 100MB upload --
# fifty videos overran a 10GB bucket on their own. A full-size master plus one small
# rendition covers the real spread of panels.
#
# Module level so media_selection can be tested against the set actually produced.
# select_rendition used to look up the literal name "720p" as its safe default, which
# silently returned nothing the moment this set changed -- and its own tests passed
# throughout, because they built their own renditions rather than these.
RENDITION_RESOLUTIONS = {
    "1080p": (1920, 1080),
    "480p": (854, 480),
}


def run_command_sync(cmd):
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return result.stdout


def probe_file(file_path):
    cmd = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name,duration",
        "-show_entries", "stream_tags=rotate",
        "-of", "json",
        str(file_path)
    ]
    stdout = run_command_sync(cmd)
    data = json.loads(stdout)
    if "streams" not in data or not data["streams"]:
        raise ValueError("No video stream found")
    stream = data["streams"][0]
    
    width = int(stream.get("width", 0))
    height = int(stream.get("height", 0))
    codec = stream.get("codec_name", "")
    duration_s = float(stream.get("duration", 0))
    rotation_tag = stream.get("tags", {}).get("rotate", "0")
    rotation = int(rotation_tag)
    
    return {
        "width": width,
        "height": height,
        "codec": codec,
        "duration_ms": int(duration_s * 1000),
        "rotation": rotation
    }


def compute_sha256(filepath):
    sha256 = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


# How long a video may sit in "processing" before it is treated as stuck. Used by the arq
# reconciler and by the in-process supervisor, so the two cannot disagree about it.
STUCK_AFTER = timedelta(minutes=10)


MAX_PROCESSING_ATTEMPTS = 3


# How long a play may sit on the device before aggregation assumes it was offline, for the
# plays whose TV app never said either way (played_offline is null). A TV with a live
# connection uploads within seconds of a play finishing; a half-hour gap is the device
# queuing it because it had nothing to upload TO.
OFFLINE_UPLOAD_GAP = timedelta(minutes=30)


def is_transient_storage_error(exc: BaseException) -> bool:
    """Whether an exception is the object store or the network having a bad moment.

    Those are worth another attempt; a file ffmpeg cannot read is not. Every exception used to
    mark the video permanently "failed", and the recovery job only re-queues rows still in
    "processing", so one dropped connection to the bucket cost the tenant an upload that would
    have worked a minute later.
    """
    try:
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError:  # pragma: no cover - boto3 is a hard dependency
        BotoCoreError = ClientError = ()  # type: ignore[assignment]
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) or 0
        return status >= 500 or error.get("Code") in {"RequestTimeout", "SlowDown", "Throttling", "InternalError"}
    return isinstance(exc, (BotoCoreError, TimeoutError, ConnectionError))


def claim_stuck_media(db) -> list[int]:
    """Take ownership of the videos that look stuck, and return their ids.

    The supervisor used to re-run process_media_sync for EVERY row in "processing" every 30
    seconds, with no age check and no claim. A video that was simply still transcoding got a
    second concurrent run (and a third, 30 seconds on), each inserting its own renditions, so
    every resolution was stored twice and the quota counted the duplicates.

    "Stuck" is the same test the arq reconciler uses: started more than STUCK_AFTER ago, or
    never stamped. Claiming is a single conditional UPDATE that re-stamps the start time, so
    two API processes (or the supervisor and the reconciler) cannot both take the same video:
    the loser's UPDATE matches no row.
    """
    threshold = models.utcnow() - STUCK_AFTER
    stuck = or_(Content.processing_started_at < threshold, Content.processing_started_at.is_(None))
    candidates = [row[0] for row in db.query(Content.id).filter(Content.status == "processing", stuck).all()]
    claimed = []
    for content_id in candidates:
        taken = (
            db.query(Content)
            .filter(Content.id == content_id, Content.status == "processing", stuck)
            .update(
                {
                    Content.processing_started_at: models.utcnow(),
                    # Each take-over is an attempt, exactly as it is when the arq reconciler
                    # re-queues one. Without the count a video that can never finish would be
                    # retried by the supervisor every ten minutes for ever.
                    Content.processing_retries: func.coalesce(Content.processing_retries, 0) + 1,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if taken != 1:
            continue
        attempts = db.query(Content.processing_retries).filter(Content.id == content_id).scalar() or 0
        if attempts > MAX_PROCESSING_ATTEMPTS:
            db.query(Content).filter(Content.id == content_id).update(
                {
                    Content.status: "failed",
                    Content.failed_reason: f"Transcoding did not finish after {MAX_PROCESSING_ATTEMPTS} attempts.",
                },
                synchronize_session=False,
            )
            db.commit()
            continue
        claimed.append(content_id)
    return claimed


def process_media_sync(content_id: int):
    db = SessionLocal()
    # Scratch directory for this job, removed in `finally` whatever happens. Renditions
    # used to be written straight into the uploads tree beside the source, so a transcode
    # that died halfway left partial mp4s behind that nothing ever cleaned up.
    workspace = tempfile.mkdtemp(prefix=f"mr_transcode_{content_id}_")
    try:
        content = db.query(Content).filter(Content.id == content_id).first()
        if not content:
            return
        
        # Only process video
        if content.type != "video":
            content.status = "ready"
            db.commit()
            return

        # One code path for both backends. ffmpeg cannot read an s3:// URL, so object
        # storage is pulled down first; local storage is copied into the same scratch
        # directory so the rest of this function never has to care which it was.
        storage_key = media_storage.storage_key_for(content.file_url)
        organization_id = content.organization_id
        file_path = media_storage.fetch_to(
            content.file_url, Path(workspace) / Path(storage_key).name
        )
        
        # A retry re-transcodes every resolution, so anything from a previous attempt is
        # stale. Without this each retry stacked another full set of renditions on the
        # row — three attempts left twelve where there should be four.
        #
        # Objects before rows. Dropping the rows alone is only safe while the key stays
        # put, and it does not: once an advert has been assigned to a client,
        # relocate_content_to_client moves the master into that client's folder and the
        # new renditions land beside it, so the pre-move set is overwritten by nothing and
        # left in the bucket with no row pointing at it.
        stale = db.query(MediaRendition).filter(
            MediaRendition.content_id == content.id
        ).all()
        for rendition in stale:
            if rendition.file_url:
                media_storage.delete(rendition.file_url)
        for rendition in stale:
            db.delete(rendition)
        db.flush()

        info = probe_file(file_path)
        # Persist the true length so a playlist item can default to it rather than a
        # flat 10 seconds, which truncates every advert longer than that.
        content.duration_ms = info.get("duration_ms") or None
        
        resolutions = RENDITION_RESOLUTIONS
        
        # Scratch, not the uploads tree: nothing is published until it transcodes
        # cleanly and is stored deliberately below.
        output_dir = Path(workspace)
        
        # Transcode renditions using a single-pass complex filtergraph.
        # This decodes the source video ONCE and generates all 4 renditions concurrently,
        # cutting CPU and IO wait time by ~75% compared to looping subprocess calls.
        filter_complex = []
        outputs = []
        out_files = {}

        for i, (name, (w_max, h_max)) in enumerate(resolutions.items()):
            out_filename = f"{file_path.stem}_{name}.mp4"
            out_filepath = output_dir / out_filename
            # Both, because the storage key below is built from the filename. Keeping only
            # the path left `out_filename` bound to whatever the last iteration set, so all
            # four renditions were stored under the 360p key and overwrote each other.
            out_files[name] = (out_filename, out_filepath)
            
            # Use force_original_aspect_ratio=decrease so we don't upscale or break aspect ratio
            # Then use a second scale to ensure both width and height are divisible by 2 for libx264
            scale_filter = f"scale={w_max}:{h_max}:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"
            filter_complex.append(f"[0:v]{scale_filter}[v{i}]")
            
            outputs.extend([
                "-map", f"[v{i}]",
                "-map", "0:a?",
                # Unindexed on purpose: each of these applies to the output file it precedes,
                # and every output file here holds exactly one video and one audio stream.
                # Written `-c:v:1` they addressed stream 1 *within* that file, which does not
                # exist, so ffmpeg silently dropped the preset, the crf and the audio bitrate
                # on every rendition after the first.
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                str(out_filepath)
            ])

        cmd = [
            "ffmpeg", "-y", "-i", str(file_path),
            "-filter_complex", ";".join(filter_complex)
        ] + outputs
        
        run_command_sync(cmd)
        
        # Kept so the source can be replaced by the largest rendition below.
        stored_urls: dict[str, str] = {}
        stored_sizes: dict[str, int] = {}

        from .media_urls import storage_prefix
        if content.file_url and "/" in content.file_url:
            raw_path = content.file_url.removeprefix("s3://").removeprefix("/uploads/").lstrip("/")
            if "/" in raw_path:
                prefix = raw_path.rsplit("/", 1)[0]
            else:
                prefix = storage_prefix(content.organization) if content.organization else str(organization_id)
        else:
            prefix = storage_prefix(content.organization) if content.organization else str(organization_id)

        for name, (out_filename, out_filepath) in out_files.items():
            
            rend_info = probe_file(out_filepath)
            rend_size = out_filepath.stat().st_size
            rend_sha = compute_sha256(out_filepath)
            
            rendition = MediaRendition(
                content_id=content.id,
                resolution=name,
                width=rend_info["width"],
                height=rend_info["height"],
                rotation=rend_info["rotation"],
                duration_ms=rend_info["duration_ms"],
                codec=rend_info["codec"],
                sha256=rend_sha,
                file_size_bytes=rend_size,
                # Store it back on whichever backend the original came from, so a
                # rendition is fetched exactly like any other asset.
                file_url=media_storage.store(
                    out_filepath,
                    f"{prefix}/{out_filename}",
                    content_type="video/mp4",
                ),
            )
            db.add(rendition)
            stored_urls[name] = rendition.file_url
            stored_sizes[name] = rend_size
            
        # extract thumbnail if missing
        if not content.thumbnail:
            thumb_filename = f"{file_path.stem}_thumb.jpg"
            thumb_filepath = output_dir / thumb_filename
            cmd = [
                "ffmpeg", "-y",
                "-ss", "00:00:01",
                "-i", str(file_path),
                "-vf", "scale=320:-2",
                "-frames:v", "1",
                str(thumb_filepath)
            ]
            try:
                run_command_sync(cmd)
                content.thumbnail = media_storage.store(
                    thumb_filepath,
                    f"{prefix}/{thumb_filename}",
                    content_type="image/jpeg",
                )
            except Exception:
                # Not fatal — the asset still plays without a thumbnail — but silence here
                # is why "thumbnail not showing" was impossible to diagnose: the item was
                # marked ready and nothing anywhere recorded that this step had failed.
                logger.exception("thumbnail generation failed for content %s", content.id)
        
        # The source is redundant once the renditions exist: the largest is a full-size
        # copy, so keeping both doubles what every video costs in the bucket for a file
        # nothing ever serves.
        #
        # Off unless asked for, because it cannot be undone -- with the source gone a
        # future codec change means re-uploading rather than re-transcoding. Turned on
        # where storage is the binding constraint; see DISCARD_SOURCE_AFTER_TRANSCODE.
        discard_source = os.getenv(
            "DISCARD_SOURCE_AFTER_TRANSCODE", "false"
        ).strip().lower() in ("1", "true", "yes")
        source_url = content.file_url
        master_url = stored_urls.get("1080p")
        # master_url != source_url guards the retry case: once the source has been
        # replaced by a rendition, a later re-run must not delete the only copy it has.
        replacing_source = bool(discard_source and master_url and master_url != source_url)

        if replacing_source:
            content.file_url = master_url
            content.file_size_bytes = stored_sizes.get("1080p", content.file_size_bytes)

        content.status = "ready"
        db.commit()

        # Notify connected dashboard users and screens in real-time
        try:
            from .routers.websockets import broadcast_ws_event
            import asyncio
            event = {
                "type": "content_updated",
                "content_id": content.id,
                "status": "ready",
                "duration_ms": content.duration_ms,
            }
            if organization_id:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(broadcast_ws_event(f"dashboard:{organization_id}", event))
                except Exception:
                    pass
        except Exception:
            pass

        # Deliberately after the commit. If this fails the row already points at the
        # rendition, so the cost is an orphaned object -- recoverable -- rather than a
        # content row referring to bytes that are no longer there.
        if replacing_source:
            try:
                media_storage.delete(source_url)
            except Exception:
                logger.exception("could not remove source for content %s", content_id)
            
    except Exception as e:
        db.rollback()
        if is_transient_storage_error(e):
            # Left in "processing" on purpose. The reconciler and the supervisor both pick up
            # a video that has sat there for STUCK_AFTER and count the attempt, so this is
            # retried a few times and only then given up on.
            logger.warning("Transient error processing content %s, will be retried: %s", content_id, e)
        else:
            content = db.query(Content).filter(Content.id == content_id).first()
            if content:
                content.status = "failed"
                content.failed_reason = str(e)
                db.commit()
    finally:
        db.close()
        shutil.rmtree(workspace, ignore_errors=True)


async def process_media(ctx, content_id: int):
    print(f"Starting process_media task for Content ID: {content_id}")
    await asyncio.to_thread(process_media_sync, content_id)
    print(f"Finished process_media task for Content ID: {content_id}")


async def recover_stuck_processing(ctx):
    db = SessionLocal()
    try:
        from datetime import timedelta
        redis = ctx.get("redis")
        if not redis:
            return
        
        threshold = models.utcnow() - STUCK_AFTER
        # Times the current attempt, not the upload. The previous version reset the timer
        # by overwriting uploaded_at, which silently rewrote the "added 3 months ago" date
        # shown in the library and broke sorting by date added.
        stuck_content = db.query(Content).filter(
            Content.status == "processing",
            or_(
                Content.processing_started_at < threshold,
                Content.processing_started_at.is_(None),
            ),
        ).all()

        for c in stuck_content:
            c.processing_retries = (c.processing_retries or 0) + 1
            if c.processing_retries > MAX_PROCESSING_ATTEMPTS:
                c.status = "failed"
                c.failed_reason = "Transcoding timed out and exceeded retry limit."
                print(f"Content {c.id} failed after {c.processing_retries} attempts")
            else:
                c.processing_started_at = models.utcnow()
                await redis.enqueue_job("process_media", c.id)
                print(f"Re-queued stuck content {c.id} (attempt {c.processing_retries})")

        db.commit()
    finally:
        db.close()



def alert_event_payload(event: str, alert) -> str:
    """The message a connected dashboard receives when an alert opens or closes."""
    return json.dumps({
        "type": event,
        "alert": {
            "id": alert.id,
            "kind": alert.kind,
            "severity": alert.severity,
            "title": alert.title,
            "detail": alert.detail,
            "screen_id": alert.screen_id,
            "content_id": alert.content_id,
            "raised_at": alert.raised_at.isoformat() if alert.raised_at else None,
        },
    })


def _alert_email_recipients(db, org_id: int) -> list[str]:
    """Owners and editors with an email address -- the roles that can act on the fleet.

    Not viewers: an alert asks someone to go fix a screen or extend a campaign, which a
    viewer cannot do, so mailing them would just be noise they cannot act on.
    """
    return [
        email for (email,) in db.query(models.User.email).filter(
            models.User.organization_id == org_id,
            models.User.role.in_(("owner", "editor")),
            models.User.email.isnot(None),
            models.User.email != "",
        ).all()
    ]


def notify_alert_opened(db, org, alert) -> None:
    """Email everyone who can act on this alert, once.

    Gated on the same `remote_control` flag that sells queued remote commands -- "Remote
    control & alerts" is one feature from a buyer's point of view, not two, and a tenant on
    a plan without it gets the dashboard toast but not an inbox full of mail.

    Called from the one place an alert is raised (below), so the arq reconciler and the
    in-process health monitor -- which both route through reconcile_org_alerts -- get this
    for free rather than needing their own copy that could drift from each other.

    Best-effort throughout: a down SMTP server, or one bad address among several
    recipients, must not fail the sweep that is raising the alert itself.
    """
    from . import alerting, billing, mailer

    if "email" in (alert.notified or []):
        return
    if alert.severity not in (alerting.CRITICAL, alerting.WARNING):
        return
    if org is None or org.plan is None or not billing.plan_features(org.plan).get("remote_control"):
        return
    if not mailer.is_configured():
        return
    recipients = _alert_email_recipients(db, org.id)
    if not recipients:
        return

    subject = f"[{alert.severity.upper()}] {alert.title}"
    body = (
        f"{alert.title}\n\n"
        f"{alert.detail or ''}\n\n"
        f"Workspace: {org.name}\n"
        f"Raised: {alert.raised_at:%d %b %Y, %H:%M} UTC\n"
    )
    sent_any = False
    for email in recipients:
        try:
            mailer.send(to=email, subject=subject, body=body)
            sent_any = True
        except Exception as exc:  # noqa: BLE001 - one bad address must not block the rest
            logger.warning("Alert email to %s failed: %s", email, exc)
    if sent_any:
        alert.notified = [*(alert.notified or []), "email"]
        db.commit()


def reconcile_org_alerts(db, org_id: int, now) -> tuple[list, list]:
    """Raise an alert for each condition that is newly true in one organisation, and resolve
    the ones that have stopped being true. Returns (raised, resolved) for the caller to publish.

    The one place this is decided. It used to exist twice -- here, and a cut-down copy in the
    in-process health monitor that only ever raised. Without the arq worker (no Redis) nothing
    resolved an alert, so a screen that came back stayed "offline" on the dashboard for ever.
    The copy also evaluated without bookings, so reusing it to resolve would have closed every
    campaign alert as no-longer-true.
    """
    from . import alerting

    screens = db.query(models.Screen).filter(
        models.Screen.organization_id == org_id
    ).all()
    contents = db.query(Content).filter(
        Content.organization_id == org_id
    ).all()

    # Only bookings that have not already finished: a campaign that ended last
    # month can never satisfy the "ending soon" window, so loading it is pure cost
    # on an estate with years of history.
    # On the EFFECTIVE end, not the sold one. Filtering on `ends_at` dropped any
    # campaign kept alive by an extension or by a longer per-location window, so
    # the booking most likely to be renewed again was the one that stopped being
    # watched -- and its "ending soon" alert never fired a second time.
    placements = db.query(models.AdPlacement).filter(
        models.AdPlacement.organization_id == org_id,
        models.AdPlacement.effective_ends_at >= now - alerting.CAMPAIGN_ENDING_WITHIN,
    ).all()

    # How many screens each booking actually reaches, groups expanded, in two
    # queries for the whole organisation rather than one per booking. The plan
    # cap counts expanded members, so the alert has to as well or a five-screen
    # plan sold as one group target would read as four screens short.
    reach: dict[int, set[int]] = {p.id: set() for p in placements}
    if reach:
        rows = db.query(
            models.AdPlacementTarget.placement_id,
            models.AdPlacementTarget.screen_id,
            models.AdPlacementTarget.group_id,
        ).filter(models.AdPlacementTarget.placement_id.in_(list(reach))).all()
        members: dict[int, list[int]] = {}
        group_ids = {group_id for _, _, group_id in rows if group_id}
        if group_ids:
            for screen_id, group_id in db.query(
                models.Screen.id, models.Screen.group_id
            ).filter(models.Screen.group_id.in_(group_ids)).all():
                members.setdefault(group_id, []).append(screen_id)
        for placement_id, screen_id, group_id in rows:
            if screen_id:
                reach[placement_id].add(screen_id)
            elif group_id:
                reach[placement_id].update(members.get(group_id, ()))

    current = alerting.evaluate_all(
        screens, contents, now, placements,
        screens_used={pid: len(seen) for pid, seen in reach.items()},
    )
    open_alerts = {
        a.dedupe_key: a
        for a in db.query(models.Alert).filter(
            models.Alert.organization_id == org_id,
            models.Alert.resolved_at.is_(None),
        ).all()
    }

    raised = []
    for key, condition in current.items():
        if key in open_alerts:
            continue
        alert = models.Alert(
            organization_id=org_id,
            kind=condition.kind,
            severity=condition.severity,
            screen_id=condition.screen_id,
            content_id=condition.content_id,
            title=condition.title,
            detail=condition.detail,
            dedupe_key=key,
            notified=[],
        )
        db.add(alert)
        try:
            # Committed one at a time so a collision with a concurrent sweep loses
            # only that alert to the partial unique index, rather than rolling back
            # every other alert raised in this pass.
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        db.refresh(alert)
        raised.append(alert)
        try:
            notify_alert_opened(db, db.get(models.Organization, org_id), alert)
        except Exception as exc:  # noqa: BLE001 - a notification failure must not drop the alert
            logger.warning("Alert-opened email hook failed for alert %s: %s", alert.id, exc)

    resolved = []
    for key, alert in open_alerts.items():
        if key in current:
            continue
        alert.resolved_at = models.utcnow()
        db.commit()
        resolved.append(alert)
    return raised, resolved


def screen_health_pass(db, now) -> list[tuple[int, str, object]]:
    """One pass of the in-process screen monitor: mark silent screens offline, then bring the
    alerts of every organisation that changed -- or that still has one open -- up to date.

    Returns (organisation id, event, alert) for everything raised or resolved, for the caller
    to publish. Organisations with an open alert are included even when no screen changed,
    which is what lets a screen that has come back clear its "offline" alert.
    """
    from .routers.screens import screen_offline_after_seconds

    threshold = now - timedelta(seconds=screen_offline_after_seconds())
    stale = (
        db.query(models.Screen)
        .filter(models.Screen.status == "online", models.Screen.last_seen < threshold)
        .all()
    )
    org_ids = set()
    for screen in stale:
        screen.status = "offline"
        org_ids.add(screen.organization_id)
        logger.info(
            "Health monitor: screen %d (%s) marked offline (last_seen %s)",
            screen.id, screen.name or "unnamed", screen.last_seen,
        )
    if stale:
        db.commit()

    org_ids.update(
        org_id for (org_id,) in db.query(models.Alert.organization_id)
        .filter(models.Alert.resolved_at.is_(None)).distinct()
    )
    org_ids.discard(None)  # an unpaired screen has no organisation to alert

    changes = []
    for org_id in org_ids:
        try:
            raised, resolved = reconcile_org_alerts(db, org_id, now)
        except Exception as exc:
            db.rollback()
            logger.warning("Health monitor alert eval failed for org %s: %s", org_id, exc)
            continue
        changes += [(org_id, "alert_raised", alert) for alert in raised]
        changes += [(org_id, "alert_resolved", alert) for alert in resolved]
    return changes


async def reconcile_alerts(ctx):
    """Notice what is wrong with the fleet, and what has stopped being wrong.

    Runs every minute. Raises an alert for each condition that is newly true, resolves the
    ones that are no longer true, and publishes both to the tenant's dashboard channel so a
    connected browser reacts immediately instead of waiting out its poll.

    Delivery to phones and inboxes hangs off the rows this writes; it deliberately does not
    happen here, so a failing SMTP server cannot stop the fleet being monitored.
    """
    from datetime import datetime, timezone

    db = SessionLocal()
    redis = ctx.get("redis")
    raised_total = 0
    resolved_total = 0
    try:
        org_ids = [row[0] for row in db.query(models.Organization.id).all()]
        for org_id in org_ids:
            raised, resolved = reconcile_org_alerts(db, org_id, datetime.now(timezone.utc))
            for alert in raised:
                raised_total += 1
                await _publish_alert(redis, org_id, "alert_raised", alert)
            for alert in resolved:
                resolved_total += 1
                await _publish_alert(redis, org_id, "alert_resolved", alert)

        if raised_total or resolved_total:
            print(f"Alerts: raised {raised_total}, resolved {resolved_total}")
    except Exception as e:
        db.rollback()
        print(f"Error reconciling alerts: {e}")
    finally:
        db.close()


async def _publish_alert(redis, organization_id: int, event: str, alert) -> None:
    """Tell any connected dashboard, best effort.

    Wrapped: Redis being unavailable must not stop alerts being recorded. The row is the
    source of truth and the dashboard falls back to fetching it.
    """
    if not redis:
        return
    try:
        await redis.publish(f"dashboard:{organization_id}", alert_event_payload(event, alert))
    except Exception as e:
        print(f"Failed to publish alert to redis: {e}")


def _aggregate_play_logs_portable(db) -> None:
    """Aggregation for any dialect, done in Python.

    The fast path below is one Postgres statement built on `date_trunc`, `UPDATE …
    RETURNING` and `IS NOT DISTINCT FROM` -- none of which SQLite has. On SQLite the whole
    statement raised, the failure was swallowed, and every play log stayed unaggregated
    forever: proof of play, the booking report and the per-advert tiles all read zero on a
    database that was full of plays. Same portability rule build_booking_report already
    follows for its daily trend, and effective_ends_at for CASE over GREATEST.

    Slower, so Postgres keeps its single-statement path.
    """
    pending = db.query(models.PlayLog).filter(models.PlayLog.aggregated.is_(False)).all()
    if not pending:
        return

    buckets: dict[tuple, dict[str, int]] = {}
    for log in pending:
        started = log.corrected_started_at
        if started is None:
            # Nothing to bucket it into. Left unaggregated it would be retried forever, so
            # mark it done and let it be excluded rather than block the queue behind it.
            log.aggregated = True
            continue
        hour = started.replace(minute=0, second=0, microsecond=0)
        key = (log.organization_id, log.campaign_id, log.screen_id, log.media_id, hour)
        # A plain dict key is already NULL-safe, which is what IS NOT DISTINCT FROM buys
        # on the Postgres path.
        totals = buckets.setdefault(
            key, {"total": 0, "completed": 0, "partial": 0, "error": 0, "duration": 0, "offline": 0}
        )
        totals["total"] += 1
        if log.status == "completed":
            totals["completed"] += 1
        elif log.status == "partial":
            totals["partial"] += 1
        elif log.status == "error":
            totals["error"] += 1
        totals["duration"] += log.duration_ms or 0
        if log.played_offline is True or (
            log.played_offline is None and log.received_at and log.corrected_finished_at
            and log.received_at - log.corrected_finished_at > OFFLINE_UPLOAD_GAP
        ):
            totals["offline"] += 1
        log.aggregated = True

    for (org_id, campaign_id, screen_id, media_id, hour), totals in buckets.items():
        row = (
            db.query(models.PlayLogHourlyRollup)
            .filter(
                models.PlayLogHourlyRollup.organization_id == org_id,
                models.PlayLogHourlyRollup.campaign_id.is_(None) if campaign_id is None
                else models.PlayLogHourlyRollup.campaign_id == campaign_id,
                models.PlayLogHourlyRollup.screen_id == screen_id,
                models.PlayLogHourlyRollup.media_id.is_(None) if media_id is None
                else models.PlayLogHourlyRollup.media_id == media_id,
                models.PlayLogHourlyRollup.date_hour == hour,
            )
            .first()
        )
        if row is None:
            db.add(models.PlayLogHourlyRollup(
                organization_id=org_id,
                campaign_id=campaign_id,
                screen_id=screen_id,
                media_id=media_id,
                date_hour=hour,
                total_plays=totals["total"],
                completed_plays=totals["completed"],
                partial_plays=totals["partial"],
                error_plays=totals["error"],
                duration_ms=totals["duration"],
                offline_plays=totals["offline"],
            ))
        else:
            row.total_plays += totals["total"]
            row.completed_plays += totals["completed"]
            row.partial_plays += totals["partial"]
            row.error_plays += totals["error"]
            row.duration_ms = (row.duration_ms or 0) + totals["duration"]
            row.offline_plays = (row.offline_plays or 0) + totals["offline"]


def aggregate_play_logs_sync(db: SessionLocal) -> int:
    """Aggregate unaggregated play_logs into play_log_hourly_rollups.

    Every proof-of-play figure in the product is read from the rollups, so a failure here
    is indistinguishable from "this advert never played" -- which is why it is no longer
    reported by a print to stdout that nobody reads.
    """
    from sqlalchemy import text

    dialect = getattr(getattr(db, "bind", None), "dialect", None)
    if dialect is not None and dialect.name != "postgresql":
        try:
            _aggregate_play_logs_portable(db)
            db.commit()
            return 1
        except Exception:
            db.rollback()
            logger.exception("Failed to aggregate play logs; proof-of-play will read zero")
            return 0

    try:
        db.execute(text("""
            WITH to_aggregate AS (
                UPDATE play_logs
                SET aggregated = TRUE
                WHERE aggregated = FALSE
                RETURNING *
            ),
            agg AS (
                SELECT
                    organization_id, campaign_id, screen_id, media_id,
                    date_trunc('hour', corrected_started_at) AS date_hour,
                    COUNT(*) AS total_plays,
                    SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_plays,
                    SUM(CASE WHEN status = 'partial' THEN 1 ELSE 0 END) AS partial_plays,
                    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS error_plays,
                    SUM(duration_ms) AS duration_ms,
                    -- Explicitly offline, or unlabelled and uploaded more than
                    -- OFFLINE_UPLOAD_GAP after the play finished.
                    SUM(CASE WHEN played_offline IS TRUE
                             OR (played_offline IS NULL AND received_at - corrected_finished_at > INTERVAL '30 minutes')
                        THEN 1 ELSE 0 END) AS offline_plays
                FROM to_aggregate
                GROUP BY organization_id, campaign_id, screen_id, media_id, date_trunc('hour', corrected_started_at)
            ),
            updated AS (
                UPDATE play_log_hourly_rollups p
                SET
                    total_plays = p.total_plays + agg.total_plays,
                    completed_plays = p.completed_plays + agg.completed_plays,
                    partial_plays = p.partial_plays + agg.partial_plays,
                    error_plays = p.error_plays + agg.error_plays,
                    duration_ms = p.duration_ms + agg.duration_ms,
                    offline_plays = p.offline_plays + agg.offline_plays
                FROM agg
                WHERE p.organization_id = agg.organization_id
                  AND p.screen_id = agg.screen_id
                  AND p.date_hour = agg.date_hour
                  AND p.campaign_id IS NOT DISTINCT FROM agg.campaign_id
                  AND p.media_id IS NOT DISTINCT FROM agg.media_id
                RETURNING p.organization_id, p.campaign_id, p.screen_id, p.media_id, p.date_hour
            )
            INSERT INTO play_log_hourly_rollups (
                organization_id, campaign_id, screen_id, media_id, date_hour,
                total_plays, completed_plays, partial_plays, error_plays, duration_ms, offline_plays
            )
            SELECT
                agg.organization_id, agg.campaign_id, agg.screen_id, agg.media_id, agg.date_hour,
                agg.total_plays, agg.completed_plays, agg.partial_plays, agg.error_plays, agg.duration_ms, agg.offline_plays
            FROM agg
            LEFT JOIN updated u
            ON agg.organization_id = u.organization_id
              AND agg.screen_id = u.screen_id
              AND agg.date_hour = u.date_hour
              AND agg.campaign_id IS NOT DISTINCT FROM u.campaign_id
              AND agg.media_id IS NOT DISTINCT FROM u.media_id
            WHERE u.organization_id IS NULL;
        """))
        db.commit()
        return 1
    except Exception:
        db.rollback()
        logger.exception("Failed to aggregate play logs; proof-of-play will read zero")
        return 0


async def aggregate_play_logs(ctx):
    db = SessionLocal()
    try:
        aggregate_play_logs_sync(db)
    finally:
        db.close()


async def prune_play_logs(ctx):
    db = SessionLocal()
    try:
        from sqlalchemy import text
        # 180 days was the old default and it could never be reached: 100 always-on
        # screens at the 10s default item duration write ~864k rows/day, and with this
        # table's nine index structures that is ~300MB/day. A 500MB database is full in
        # under two days, so the prune never ran on anything. The rollups keep the
        # reporting history; these raw rows only need to outlive a billing dispute.
        retention_days = int(os.getenv("PLAY_LOG_RETENTION_DAYS", "7"))
        total_deleted = 0
        chunk_size = 5000
        
        while True:
            # Delete in chunks to avoid table locks and excessive WAL bloat
            result = db.execute(text("""
                DELETE FROM play_logs 
                WHERE event_id IN (
                    SELECT event_id 
                    FROM play_logs 
                    WHERE aggregated = TRUE 
                      AND received_at < NOW() - CAST(:days || ' days' AS INTERVAL)
                    LIMIT :chunk_size
                )
            """).bindparams(days=str(retention_days), chunk_size=chunk_size))
            
            deleted_count = result.rowcount
            total_deleted += deleted_count
            db.commit()
            
            if deleted_count < chunk_size:
                break
                
        print(f"Pruned {total_deleted} aggregated play logs older than {retention_days} days.")
    except Exception as e:
        db.rollback()
        print(f"Error pruning play logs: {e}")
    finally:
        db.close()



async def prune_play_log_rollups(ctx):
    """Age out the hourly rollups.

    Nothing deleted from this table -- no cron, no endpoint, no retention setting. It
    grows by one row per (organisation, campaign, screen, media, hour) forever, which is
    roughly 36k rows a day for a hundred screens, so it fills a 500MB database on its own
    in about two months even once the raw log is being pruned properly.

    The default keeps more than a year so that year-on-year reporting still works; this is
    the aggregated history, and it is small per row, so there is no reason to be mean with
    it. Chunked like prune_play_logs so a first run against a long backlog cannot hold a
    lock over the whole table.
    """
    db = SessionLocal()
    try:
        from sqlalchemy import text
        retention_days = int(os.getenv("ROLLUP_RETENTION_DAYS", "400"))
        total_deleted = 0
        chunk_size = 5000

        while True:
            result = db.execute(text("""
                DELETE FROM play_log_hourly_rollups
                WHERE id IN (
                    SELECT id
                    FROM play_log_hourly_rollups
                    WHERE date_hour < NOW() - CAST(:days || ' days' AS INTERVAL)
                    LIMIT :chunk_size
                )
            """).bindparams(days=str(retention_days), chunk_size=chunk_size))

            deleted_count = result.rowcount
            total_deleted += deleted_count
            db.commit()

            if deleted_count < chunk_size:
                break

        print(f"Pruned {total_deleted} play-log rollups older than {retention_days} days.")
    except Exception as e:
        db.rollback()
        print(f"Error pruning play-log rollups: {e}")
    finally:
        db.close()


async def prune_screenshots(ctx):
    """Keep only the newest captures per screen, and delete their files too.

    Nothing removed these before. The UI only ever shows the newest ten, so the rest were
    invisible while still consuming a row and an image file each — unbounded growth across
    a fleet that captures regularly.
    """
    keep = int(os.getenv("SCREENSHOT_KEEP_PER_SCREEN", "10"))
    db = SessionLocal()
    removed = 0
    try:
        screen_ids = [row[0] for row in db.query(models.ScreenshotLog.screen_id).distinct()]
        for screen_id in screen_ids:
            stale = (
                db.query(models.ScreenshotLog)
                .filter(models.ScreenshotLog.screen_id == screen_id)
                .order_by(models.ScreenshotLog.created_at.desc())
                .offset(keep)
                .all()
            )
            for shot in stale:
                # File first: if the row goes and the unlink fails we lose the only
                # pointer to it and it becomes an orphan.
                #
                # Via media_storage rather than delete_stored_file: the latter only
                # understands "/uploads/", so with R2 configured every pruned screenshot
                # dropped its row and left the object in the bucket permanently.
                media_storage.delete(shot.file_url)
                db.delete(shot)
                removed += 1
        db.commit()
        if removed:
            print(f"Pruned {removed} screenshots beyond the newest {keep} per screen.")
    except Exception as e:
        db.rollback()
        print(f"Error pruning screenshots: {e}")
    finally:
        db.close()


async def prune_finished_bookings(ctx):
    """Delete the playlist items belonging to campaigns that have finished.

    sync_tv already stops sending an expired item, which is what frees the panel. This is
    the other half: without it the rows accumulate forever, so the playlists page fills
    with adverts that stopped months ago and every playlist query carries them.

    Reached ONLY through AdPlacementTarget, so nothing hand-made is ever touched -- an
    operator's own playlist item has no target row pointing at it and is invisible here.
    The booking itself is kept: it is the record of a sale, and the report has to keep
    answering for it long after the advert came down.
    """
    from datetime import timedelta

    from sqlalchemy import func

    from . import models as _models

    db = SessionLocal()
    removed = 0
    try:
        now = _models.utcnow()
        # A grace period, not `< now`: the effective end can move when an extension is
        # sold, and deleting an item minutes after it lapsed would take an advert down that
        # the tenant is mid-way through renewing.
        cutoff = now - timedelta(days=2)
        # Filtered in SQL, not in Python. This used to load EVERY target that still had a
        # live item and dereference target.placement one row at a time, so the work grew
        # with the whole history of bookings rather than with the handful that had just
        # finished -- an estate with three years of campaigns paid for all of them nightly.
        #
        # coalesce, because a location sold its own shorter window finishes on that date
        # while the campaign around it runs on: the item it put on that screen is done and
        # should go, and only a location with no window of its own waits for the booking.
        targets = (
            db.query(_models.AdPlacementTarget)
            .join(_models.AdPlacement, _models.AdPlacement.id == _models.AdPlacementTarget.placement_id)
            .filter(
                _models.AdPlacementTarget.playlist_item_id.isnot(None),
                func.coalesce(
                    _models.AdPlacementTarget.ends_at,
                    _models.AdPlacement.effective_ends_at,
                ) <= cutoff,
            )
            .all()
        )
        for target in targets:
            item = db.query(_models.PlaylistItem).filter(
                _models.PlaylistItem.id == target.playlist_item_id
            ).first()
            if not item:
                continue
            playlist = item.playlist
            db.delete(item)
            # The target keeps the booking's shape for the report; it just no longer points
            # at a live item. SET NULL is what the foreign key already does on delete.
            target.playlist_item_id = None
            if playlist:
                playlist.updated_at = _models.utcnow()
            removed += 1
        db.commit()
        if removed:
            print(f"Removed {removed} playlist items from finished bookings.")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"Error pruning finished bookings: {exc}")
    finally:
        db.close()


async def purge_removed_tenants(ctx):
    """Destroy workspaces an admin removed more than 30 days ago.

    Here rather than in the `main.py` supervisor thread because those threads start in
    EVERY API process: a destructive job wants exactly one runner, and the arq worker is
    the only thing in this deployment that is guaranteed to be singular.

    Set TENANT_PURGE_DRY_RUN=true to have it log what it would destroy and destroy
    nothing -- worth doing for the first cycle on real data.
    """
    from .services.tenant_purge import purge_removed_tenants as _purge

    db = SessionLocal()
    try:
        reports = _purge(db)
        if reports:
            print(f"Purged {len(reports)} removed workspace(s).")
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        print(f"Error purging removed tenants: {exc}")
    finally:
        db.close()


class WorkerSettings:
    functions = [process_media]
    cron_jobs = [
        cron(recover_stuck_processing, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
        cron(aggregate_play_logs, minute={0, 15, 30, 45}),
        # Every minute: an operator should hear about a dead screen in about the
        # time it takes to notice one in the room.
        cron(reconcile_alerts, minute=set(range(60))),
        # Hourly, not nightly. The table grows at ~316MB/day for a hundred screens
        # (measured, not estimated), so a once-a-day prune lets it peak at retention plus
        # a whole extra day of rows just before it fires -- which overruns a small database
        # even with retention set to its 1-day floor. Each run is a chunked, bounded
        # delete, so running it 24x more often costs little and caps the peak at an hour.
        cron(prune_play_logs, minute={0}),
        cron(prune_screenshots, hour=3, minute=30),
        # After the screenshot prune, in the same quiet window.
        cron(prune_finished_bookings, hour=3, minute=45),
        # Once a day is plenty: rollups accrue at ~36k rows/day, not 864k, and the
        # retention window is over a year. Off the hour to stay clear of the raw prune.
        cron(prune_play_log_rollups, hour=4, minute=0),
        # Last in the quiet window, and only once a day: the 30-day window means being a
        # few hours late costs nothing, while being wrong is unrecoverable.
        cron(purge_removed_tenants, hour=4, minute=30),
    ]
    redis_settings = REDIS_SETTINGS

    # arq defaults to 10 concurrent jobs. Every media job spawns ffmpeg, which is
    # CPU-bound and memory-hungry, so ten at once will thrash a small VM and can take it
    # out entirely -- and the transcodes finish no sooner for competing over one core.
    # Raise it only alongside the cores to run them on.
    max_jobs = int(os.getenv("WORKER_MAX_JOBS", "2"))
