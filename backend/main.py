import asyncio
import logging
import os
import re
import pathlib
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from .limiter import limiter

from . import database, models
from .billing import ensure_billing_catalog
from .database import Base, engine

logger = logging.getLogger(__name__)

# Set when the boot-time migration could not complete, so /api/health can report the
# drift rather than leaving an operator to discover it from a column error later.
SCHEMA_MIGRATION_ERROR: str | None = None


def _ensure_schema() -> None:
    """Build the schema on a brand-new database, and stamp it so Alembic can take over.

    `create_all()` used to run here unconditionally. On a database that already exists
    that is a harmless no-op, which is why nobody noticed -- but on a NEW one it built
    every table straight from the ORM and left no alembic_version row, so the first
    `alembic upgrade head` died on "relation users already exists" and the database could
    never be migrated again. Deploying to a fresh Postgres (Supabase, Neon, a new RDS) was
    a one-way trip into that state.

    Stamping closes it. The two paths were compared column by column and index by index on
    fresh databases: 248 columns either way, and no index the migrations create that
    create_all does not. A stamped database is the database the migrations would have
    built, so the next migration applies cleanly on top.

    Serialised with a Postgres advisory lock, which closes the race this used to carry a
    note about: two replicas booting into the same empty database would both see no
    alembic_version table, both run create_all, and both try to stamp. The lock is held for
    the transaction only and is a no-op on SQLite, which has no second process to race.
    """
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect, text

    is_postgres = engine.url.get_backend_name().startswith("postgres")

    def _bootstrap() -> None:
        # Checked BEFORE create_all: its presence is what distinguishes a database Alembic
        # already manages from one this process is about to create.
        alembic_owns_it = inspect(engine).has_table("alembic_version")

        here = pathlib.Path(__file__).parent
        config = Config(str(here / "alembic.ini"))
        # Absolute, because alembic.ini's script_location is relative to the working
        # directory and this runs from wherever the process was started.
        config.set_main_option("script_location", str(here / "alembic"))

        if alembic_owns_it:
            # Migrate, rather than returning and hoping somebody remembered.
            #
            # create_all() adds MISSING TABLES but never alters an existing one, so a
            # release that adds a column to a table already in the database left that column
            # absent while every new table appeared -- the schema looked half-applied and
            # the failure surfaced as "column organizations.brand_name does not exist" on
            # essentially every authenticated request, because Organization is loaded on
            # nearly all of them.
            #
            # render.yaml documents running `alembic upgrade head` by hand because
            # preDeployCommand needs a paid instance. That is a step between a green deploy
            # and a working one, performed by a person, at the moment the new code is
            # already live and failing. Doing it here closes that window.
            #
            # Safe to run on every boot: `upgrade head` is a no-op when there is nothing to
            # apply, and the advisory lock below means a second worker waits rather than
            # running the same migration twice.
            try:
                command.upgrade(config, "head")
                logger.info("database migrated to head")
            except Exception as exc:  # noqa: BLE001
                # Migrating on boot must never stop the service booting.
                #
                # Not hypothetical: the previous behaviour ran create_all on every start, so
                # a database can already hold a table that a LATER migration also creates,
                # and `upgrade head` then dies on "relation already exists" -- which is
                # exactly what this repo's local development database does. Refusing to
                # start there turns schema drift into a total outage, and on a restart of an
                # already-live service that outage is immediate.
                #
                # So log it loudly, continue to create_all below (which still brings any
                # missing TABLE into being), and report it on /api/health. What is lost is
                # ALTERs -- new columns on existing tables -- which is precisely the state
                # this code was in before the migration step was added.
                global SCHEMA_MIGRATION_ERROR
                SCHEMA_MIGRATION_ERROR = str(exc).splitlines()[0][:300]
                logger.error(
                    "SCHEMA MIGRATION FAILED -- this database may be missing columns this "
                    "build expects. Run `alembic -c backend/alembic.ini upgrade head` "
                    "against it by hand. Cause: %s", SCHEMA_MIGRATION_ERROR,
                )
            # After the migrations, so a table a migration was supposed to create is created
            # by that migration and not silently conjured by create_all first -- which would
            # then make the migration fail on "relation already exists".
            Base.metadata.create_all(bind=engine)
            return

        Base.metadata.create_all(bind=engine)
        command.stamp(config, "head")
        logger.info("new database: schema created and stamped at head")

    if not is_postgres:
        _bootstrap()
        return

    with engine.begin() as connection:
        # Arbitrary but fixed key; any other process running this function picks the same
        # one and waits rather than duplicating the work.
        connection.execute(text("SELECT pg_advisory_xact_lock(4823179055)"))
        _bootstrap()


def _run_worker_in_process() -> bool:
    """Whether this API process should also run the arq worker.

    Off by default: docker-compose runs the worker as its own service, and starting a
    second copy here would double every cron job.

    On for single-process hosts (Render/Railway free tiers give you one web service and no
    background worker). Without a worker running SOMEWHERE, `process_media` never fires --
    so uploads never reach status="ready" and never reach a TV -- and `aggregate_play_logs`
    never fires, so play_logs pile up correctly while every dashboard count, which reads
    PlayLogHourlyRollup, reads zero forever.
    """
    return os.getenv("RUN_WORKER_IN_PROCESS", "").strip().lower() in {"1", "true", "yes"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Called here rather than at module scope. Importing this module used to build the
    # schema as a side effect, so anything that merely imported backend.main -- pytest
    # collection above all -- opened a connection and wrote to whatever database the
    # environment happened to point at.
    _ensure_schema()

    db = database.SessionLocal()
    try:
        auth.ensure_initial_owner(db)
        ensure_billing_catalog(db)
        # Before anything can serve or accept media: a key typed into the admin console has
        # to be live from the first request, not from the first supervisor tick.
        from .services.storage_service import load_storage_overrides

        load_storage_overrides(db)
    finally:
        db.close()

    def _media_supervisor_loop():
        import time
        while True:
            rec_db = database.SessionLocal()
            try:
                from .worker import claim_stuck_media, process_media_sync

                stuck_ids = claim_stuck_media(rec_db)
                if stuck_ids:
                    logger.info("Media supervisor took over %d stuck item(s); ensuring completion", len(stuck_ids))
                    for content_id in stuck_ids:
                        try:
                            process_media_sync(content_id)
                        except Exception as exc_item:
                            logger.warning("Supervisor error processing media %d: %s", content_id, exc_item)
                # A booking that is sold, paid and running can lose the playlist items it
                # created without anyone touching it: PlaylistItem cascades from both its
                # playlist and its content, while the target's link is ON DELETE SET NULL.
                # The booking then reads "Running" on every page while playing on nothing,
                # which is invisible until a client asks why their advert never appeared.
                #
                # Here rather than as a scheduled job because the scheduled jobs need Redis,
                # and this matters most when the deployment is already degraded.
                from .services import reconcile_unplaced_bookings

                reconcile_unplaced_bookings(rec_db)

                # A paid window closing is the one lifecycle event with no user action
                # behind it, so something has to notice the clock. Here for the same reason
                # as the reconcile above: the scheduled jobs run on arq over Redis, and a
                # deployment with Redis down would otherwise let every expired subscription
                # keep full access indefinitely -- which is the state this started in.
                # Storage settings typed into the console, re-read here so a second worker
                # process picks up what the one handling the request wrote.
                from .services.storage_service import load_storage_overrides

                load_storage_overrides(rec_db)

                from .services import expire_due_subscriptions

                expired = expire_due_subscriptions(rec_db)
                if expired:
                    logger.info("Expired %d subscription(s) past their period", expired)
            except Exception as exc:
                logger.warning("Failed in media supervisor loop: %s", exc)
            finally:
                rec_db.close()
            time.sleep(30)

    def _screen_health_monitor_loop():
        """Detect offline screens every 30 seconds, and keep their alerts true.

        The arq reconciler runs every minute, but that means a screen can be offline
        for up to 2 minutes (1 min threshold + 1 min cron gap) before an alert fires.
        This tighter loop cuts that to ~30 seconds. It also RESOLVES: with no Redis there is
        no arq worker, and this loop was the only thing running, so an alert it raised stayed
        open after the screen came back.
        """
        import asyncio as _asyncio
        import time
        from datetime import datetime, timezone
        from .worker import alert_event_payload, screen_health_pass

        _loop = _asyncio.new_event_loop()

        def _redis_publish(org_id: int, payload_str: str):
            """Best-effort publish on the thread's own event loop."""
            try:
                r = database.get_redis()
                _loop.run_until_complete(r.publish(f"dashboard:{org_id}", payload_str))
            except Exception as exc:
                logger.debug("Health monitor Redis publish failed: %s", exc)

        while True:
            rec_db = database.SessionLocal()
            try:
                for org_id, event, alert in screen_health_pass(rec_db, datetime.now(timezone.utc)):
                    _redis_publish(org_id, alert_event_payload(event, alert))
            except Exception as exc:
                logger.warning("Failed in screen health monitor loop: %s", exc)
            finally:
                rec_db.close()
            time.sleep(30)

    import threading
    threading.Thread(target=_media_supervisor_loop, daemon=True).start()
    threading.Thread(target=_screen_health_monitor_loop, daemon=True, name="screen-health-monitor").start()

    arq_worker = None
    worker_task = None
    if _run_worker_in_process():
        from arq.worker import create_worker

        from .worker import WorkerSettings

        # handle_signals=False because uvicorn already owns SIGINT/SIGTERM. Left at the
        # default, arq installs its own handlers over uvicorn's and the process stops
        # shutting down cleanly.
        arq_worker = create_worker(WorkerSettings, handle_signals=False)
        worker_task = asyncio.create_task(arq_worker.async_run())
        logger.info("arq worker started in-process (RUN_WORKER_IN_PROCESS)")

    try:
        yield
    finally:
        if arq_worker is not None:
            try:
                await arq_worker.close()
            except AttributeError:
                # When arq did not install its own signal handlers, close() signals the
                # run loop with SIGUSR1 -- which is POSIX-only, so this raises on a Windows
                # dev machine and never on a deployment target. The cancel below is enough.
                pass
        if worker_task is not None:
            worker_task.cancel()


app = FastAPI(title="MR Signage API", version="2.0.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

allowed_origins = [
    origin.strip()
    for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
    if origin.strip()
]

# A dashboard opened from a phone or a TV on the same network is served from the host's
# LAN address, not localhost, so that origin has to be allowed too or every request is
# blocked by the browser before it leaves the device. Private ranges only — this never
# opens the API to the public internet.
_ALLOWED_ORIGIN_REGEX = re.compile(
    r"^https?://("
    r"localhost|127\.0\.0\.1|"
    r".*?\.workers\.dev|"
    r".*?\.pages\.dev|"
    r".*?\.onrender\.com|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(:\d+)?$"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=_ALLOWED_ORIGIN_REGEX.pattern,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# FastAPI's default 422 body echoes the offending request back to the caller under
# "input". On any endpoint that accepts a credential that hands the plaintext secret
# straight back — a mistyped screen sign-in returned the operator's password, and the same
# applies to user creation and the provisioning QR's wifi password. The field names stay
# so the error is still diagnosable; only the values are masked.
SENSITIVE_FIELDS = {"password", "wifi_password", "device_secret", "enrollment_token", "secret"}


def _redact(value):
    if isinstance(value, dict):
        return {
            key: ("***" if key.lower() in SENSITIVE_FIELDS else _redact(inner))
            for key, inner in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


@app.exception_handler(RequestValidationError)
async def redacted_validation_error(_request: Request, exc: RequestValidationError):
    errors = []
    for error in exc.errors():
        cleaned = dict(error)
        if "input" in cleaned:
            cleaned["input"] = _redact(cleaned["input"])
        # ctx can carry the offending value for constraint failures (min_length etc.)
        cleaned.pop("ctx", None)
        errors.append(cleaned)
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


UPLOAD_DIR = os.path.join(pathlib.Path(__file__).parent.parent.absolute(), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")


# How long a signature handed to a client stays good. Generous on purpose: a TV pulls a
# several-hundred-megabyte advert in ranged requests over a long stretch, and a short
# window expired mid-download and looked exactly like a corrupt file.
_MEDIA_SIGNATURE_SECONDS = 6 * 3600
# Strictly less than the above, so a redirect cached by a browser can never outlive the
# URL it points at.
_MEDIA_REDIRECT_CACHE_SECONDS = 60


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """`bytes=start-end` as a concrete, in-bounds pair, or None if it is not usable."""
    if not header or not header.startswith("bytes=") or "," in header:
        return None
    span = header.removeprefix("bytes=").strip()
    start_text, _, end_text = span.partition("-")
    try:
        if not start_text:
            # A suffix range ("bytes=-500") counts back from the end.
            length = int(end_text)
            return (max(0, size - length), size - 1) if length > 0 else None
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
    except ValueError:
        return None
    end = min(end, size - 1)
    if start > end or start >= size:
        return None
    return start, end


def _serve_media_from_database(key: str, range_header: str | None):
    """The object's bytes from the `media_blob` mirror, or None if it is not mirrored.

    Returns None rather than raising for every reason it can fail -- absent table, absent
    row, unreachable database -- because the caller's next move is the same in all of them
    and a media route is no place to surface a schema problem.

    Range is honoured because Android's player asks for one: without it a TV re-downloads a
    whole advert to resume and cannot seek within it at all.
    """
    from sqlalchemy import text

    from . import database

    try:
        with database.engine.connect() as connection:
            row = connection.execute(
                text("SELECT content_type, data FROM media_blob WHERE key = :key"),
                {"key": key},
            ).first()
    except Exception:
        return None
    if row is None:
        return None

    content_type, data = row[0], bytes(row[1])
    request_range = _parse_range(range_header, len(data))
    if request_range:
        start, end = request_range
        chunk = data[start : end + 1]
        return Response(
            chunk,
            status_code=206,
            media_type=content_type,
            headers={
                "Content-Range": f"bytes {start}-{end}/{len(data)}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(chunk)),
            },
        )
    return Response(
        data,
        media_type=content_type,
        headers={
            "Accept-Ranges": "bytes",
            "Cache-Control": f"public, max-age={_MEDIA_SIGNATURE_SECONDS}",
        },
    )


@app.get("/api/media/{key:path}")
@app.head("/api/media/{key:path}")
def serve_media(key: str, request: Request = None):
    """Stable URL for a stored object; signs the real one fresh on every request.

    This is the only place a media signature is produced. `resolve_media_url` hands out
    this path instead of a presigned URL precisely so that nothing downstream ever holds a
    credential or an expiry: a URL cached by a browser, written into a TV's local database
    months ago or embedded in a report still resolves, because the signing happens when the
    link is followed rather than when it was handed out.

    Deliberately unauthenticated. The key contains the upload's UUID, so knowing it is the
    capability -- the same posture as the presigned URL this replaces, which was equally a
    bearer link, and which the dashboard had no way to authenticate anyway because an
    <img> tag cannot carry a token.
    """
    from .media_urls import get_s3_config, is_s3_enabled, public_media_base, s3_client

    # The key goes straight into an S3 request, so it must not be able to climb out of the
    # prefix it was minted under.
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise HTTPException(status_code=404, detail="Not found")

    # A bucket published read-only needs no signature, so this works with no credentials at
    # all. Checked before the credential path so that a deployment with both configured
    # takes the cheaper route: no boto3 client, no signing, and a URL that never expires and
    # is therefore cacheable by the CDN in front of it.
    public_base = public_media_base()
    if public_base:
        return RedirectResponse(
            f"{public_base}/{key}",
            status_code=307,
            headers={"Cache-Control": f"public, max-age={_MEDIA_SIGNATURE_SECONDS}"},
        )

    if not is_s3_enabled():
        # Last resort before giving up: a copy held in the database itself. A deployment can
        # lose its storage credentials while keeping its database, and then every object it
        # owns is unreachable even though nothing was deleted -- so a mirrored copy is the
        # difference between a dashboard that works and one that is entirely blank.
        #
        # ponytail: bytes through Postgres is slower and pricier than object storage and is
        # meant as a bridge, not a home. Set credentials or publish the bucket and this
        # branch stops being reached.
        range_hdr = request.headers.get("range") if request else None
        served = _serve_media_from_database(key, range_hdr)
        if served is not None:
            return served
        # Local storage is served by the /uploads mount above and never reaches here, so
        # this only fires for an s3:// row in a deployment that has since lost its
        # credentials. Saying so beats a blank image.
        raise HTTPException(status_code=503, detail="Object storage is not configured")

    cfg = get_s3_config()
    target = s3_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": cfg["bucket"], "Key": key},
        ExpiresIn=_MEDIA_SIGNATURE_SECONDS,
    )
    # 307 rather than 302: players re-issue their Range header on the follow-up, which is
    # what lets a TV seek within a long advert.
    return RedirectResponse(
        target,
        status_code=307,
        headers={"Cache-Control": f"private, max-age={_MEDIA_REDIRECT_CACHE_SECONDS}"},
    )


@app.get("/api/health")
async def health_check(db: Session = Depends(database.get_db)):
    """Liveness, plus WHICH database is actually behind it.

    This used to answer {"database": "connected"} for any working connection, which is
    true and useless: a deployment silently using the SQLite fallback looked identical to
    one on Postgres, so "is production really on Supabase?" could not be answered without
    reading container logs. Credentials are never included -- host and dialect only.
    """
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(status_code=503, detail="Database connection failed")

    url = database.engine.url
    ephemeral = url.get_backend_name() == "sqlite"

    # Redis was not checked here at all, and it is not an optional component: without it
    # every upload fails outright, nothing is pushed to a screen, and all six cron jobs
    # stop -- including the two that keep the database inside its size limit. The old
    # answer was a flat "ok" while the fleet quietly stopped updating.
    #
    # Reported rather than fatal: playback and telemetry survive a Redis outage, so a
    # 503 here would take a serving system out of a load balancer for a degradation.
    redis_ok = False
    redis_error = None
    try:
        await database.get_redis().ping()
        redis_ok = True
    except Exception as exc:  # noqa: BLE001 - any failure means "not usable"
        redis_error = type(exc).__name__

    # Object storage, reported because its absence is invisible until a screen shows
    # nothing. Without it every upload is written to the container's own disk, which on
    # Render and most PaaS hosts is discarded on the next deploy: the row survives, the
    # file does not, and the dashboard lists media that 404s everywhere.
    from .media_urls import is_s3_enabled, public_media_base
    from .mailer import is_configured as email_configured

    object_storage = is_s3_enabled()
    # Reading and writing fail independently. A published bucket serves every object the
    # deployment already has with no credential, while uploading still needs an API token --
    # so "thumbnails are blank" and "new uploads vanish on redeploy" are two different
    # states, and collapsing them into one boolean is what made the second one invisible.
    public_reads = bool(public_media_base())
    if not object_storage and not public_reads:
        # The database mirror serves the same objects, so a deployment using it must not be
        # told its thumbnails are blank -- they are not, and a false alarm on a tenant's
        # dashboard costs more trust than the real warning underneath it is worth.
        # On its own connection, never the request's session. The table has no model or migration
        # and may not exist, and on Postgres a failed statement aborts the whole transaction: the
        # swallowed error then made the next query in this handler fail, so a database without the
        # mirror answered 500 -- to Render's health check, which would read that as down.
        try:
            with database.engine.connect() as connection:
                public_reads = bool(
                    connection.execute(text("SELECT 1 FROM media_blob LIMIT 1")).first()
                )
        except Exception:
            public_reads = False
    # Reported, not fatal. Nothing in the fleet depends on mail -- but a tenant who clicks
    # "email this report to the client" and is told it went is owed the truth, and the
    # place to find out is here rather than from the client who never received it.
    email_ready = email_configured()

    warnings = []
    if ephemeral:
        warnings.append("using local SQLite fallback; DATABASE_URL is not set")
    if not object_storage:
        warnings.append(
            "object storage has no credentials (AWS_ACCESS_KEY_ID unset or 'mock'); "
            "uploads are written to local disk and are LOST on every redeploy"
            + ("" if public_reads else ", and existing media cannot be served at all")
        )

    # Surfaced here because it is the one security-relevant setting that is open by
    # default and invisible when it is wrong. While it is on, any caller that knows a
    # device id -- echoed by /register, shown in the dashboard, printed in logs -- can
    # read a screen's full playlist and its maintenance pin, and can inject play logs
    # that bill an advertiser. It exists to keep pre-credential players alive during the
    # rollout, so it is a state to leave, not a state to sit in.
    from .routers.screens import legacy_device_auth_allowed

    legacy_remaining = None
    if legacy_device_auth_allowed():
        from .services import screens_without_secret

        legacy_remaining = screens_without_secret(db)
        warnings.append(
            "ALLOW_LEGACY_DEVICE_AUTH is on; screens may call device endpoints with no "
            f"credential. {legacy_remaining} screen(s) still hold no device secret; turn it "
            "off once that reaches 0"
        )
    if not redis_ok:
        warnings.append(
            f"Redis unreachable ({redis_error}); uploads, live push and all scheduled "
            "jobs are stopped"
        )

    # Which storage variables this process can actually SEE.
    #
    # Names only, never values. "I saved them in Render and nothing changed" is otherwise
    # undiagnosable from outside: the two failure modes -- a variable spelled slightly wrong,
    # and a variable set on a different service -- look identical to a missing one, and the
    # only person who can tell them apart is the one who cannot see inside the process.
    #
    # `unexpected` is the one that finds a typo: AWS_ACCES_KEY_ID is reported by name, so the
    # mistake is visible instead of silent.
    known = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION",
             "S3_ENDPOINT_URL", "S3_BUCKET_NAME", "R2_PUBLIC_BASE_URL"}
    seen = {
        name for name in os.environ
        if name.startswith(("AWS_", "S3_", "R2_")) and (os.environ[name] or "").strip()
    }
    # A value that is present but is the literal placeholder `mock`, which `_setting` maps to
    # "not configured" outside the test suite. Indistinguishable from a real key in the list
    # above, and it is the one state that makes a correctly-saved variable do nothing -- so
    # it gets named rather than inferred. Still names only: whether a value equals a known
    # placeholder reveals nothing a real secret would.
    placeholder = sorted(
        name for name in seen & known
        if os.environ[name].strip().lower() in {"mock", "changeme", "your-key", "xxx"}
    )
    storage_env = {
        "set": sorted(seen & known),
        "missing": sorted(known - seen),
        # Anything AWS_/S3_/R2_-shaped that this code never reads -- almost always a typo.
        "unexpected": sorted(seen - known),
        "placeholder": placeholder,
    }

    # Named for the same reason legacy device auth is: it is a setting that silently turns
    # a commercial rule off, and nothing else would ever say so.
    from .billing import mock_payments_enabled

    if mock_payments_enabled():
        warnings.append(
            "PAYMENT_PROVIDER=mock; /api/billing/mock/confirm will activate a workspace "
            "with no payment. Set a real provider before selling anything"
        )

    return {
        "status": "ok" if redis_ok and not ephemeral and not warnings else "degraded",
        "database": "connected",
        "backend": url.get_backend_name(),
        "host": url.host or url.database,
        "redis": "connected" if redis_ok else "unreachable",
        "object_storage": (
            "configured"
            if object_storage
            else "public read (new uploads are ephemeral)"
            if public_reads
            else "local disk (ephemeral)"
        ),
        "email": "configured" if email_ready else "not configured (SMTP_HOST/SMTP_FROM unset)",
        # Names only. See the note where this is built.
        "storage_env": storage_env,
        # Absent when the boot migration succeeded, which is the normal case.
        "schema_migration_error": SCHEMA_MIGRATION_ERROR,
        # None once legacy device auth is off. Otherwise how many screens would be stranded by
        # switching it off; the switch is safe when this is 0.
        "legacy_screens_without_secret": legacy_remaining,
        # Loud on purpose: these are the states where everything looks fine and either
        # every write is discarded on the next deploy, or nothing is being processed.
        "warning": "; ".join(warnings) or None,
    }


# One import, at the point of use. There were two of these -- an identical line at the
# top of the file and this one -- so adding a router meant remembering to edit both.
from .routers import (
    admin, alerts, analytics, auth, billing, branding, clients, content, device_diagnostics, emergency, enrollment_tokens,
    groups, placements, playlists, provisioning, releases, screens, screenshots,
    tenant_plans, users, websockets,
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(screens.router, prefix="/api/screens", tags=["screens"])
app.include_router(device_diagnostics.router, prefix="/api/screens", tags=["screens"])
app.include_router(groups.router, prefix="/api/groups", tags=["groups"])
app.include_router(content.router, prefix="/api/content", tags=["content"])
app.include_router(playlists.router, prefix="/api/playlists", tags=["playlists"])
app.include_router(placements.router, prefix="/api/placements", tags=["placements"])
app.include_router(clients.router, prefix="/api/clients", tags=["clients"])
app.include_router(branding.router, prefix="/api/branding", tags=["branding"])
app.include_router(tenant_plans.router, prefix="/api/tenant-plans", tags=["tenant-plans"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(websockets.router, prefix="/api/ws", tags=["Websockets"])
app.include_router(billing.router, prefix="/api/billing", tags=["billing"])
app.include_router(emergency.router, prefix="/api/emergency", tags=["Emergency"])
app.include_router(screenshots.router, prefix="/api/screenshots", tags=["Screenshots"])
app.include_router(releases.router, prefix="/api/releases", tags=["Releases"])
app.include_router(alerts.router, prefix="/api/alerts", tags=["alerts"])
app.include_router(analytics.router)
app.include_router(enrollment_tokens.router, prefix="/api", tags=["enrollment-tokens"])
app.include_router(provisioning.router, prefix="/api/provisioning", tags=["provisioning"])


# The API used to serve its own hand-written /login page here: a dark "Authorize Display"
# card with a Google button. It is gone, and was not converted, for two reasons.
#
# It was a mock. `approveScreen()` ran no OAuth at all -- it revealed a success message and
# fired an intent: deep link 1.2 seconds later, so it told an installer their display was
# authorised while authorising nothing. The real flow is google/oauth-url ->
# google/oauth-callback in routers/screens.py, and nothing in the TV app or the dashboard
# ever linked here.
#
# It also duplicated a route the Next.js dashboard already owns. Two /login pages on two
# origins is a thing to explain forever.
#
# The OAuth landing pages in routers/screens.py stay server-rendered on purpose: Google
# redirects the TV's browser to redirect_uri on THIS origin, so they cannot move to the
# dashboard without breaking the sign-in this session already spent a long time fixing.


@app.get("/")
def read_root():
    return {"message": "Welcome to MR Signage API"}
