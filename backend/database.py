from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
import os
import pathlib
from dotenv import load_dotenv

env_path = pathlib.Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

# Fallback to local sqlite if no DATABASE_URL is provided.
#
# Convenient locally, dangerous anywhere else: a deployment that forgets DATABASE_URL does
# not fail, it quietly writes to a file on an ephemeral container disk that is destroyed on
# the next deploy -- and /api/health cheerfully answered "connected" the whole time. That
# happened on the first Render deploy and cost an afternoon of "why is Supabase empty?".
# It still falls back, because local development wants that, but it now says so.
SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./mr_signage.db")

if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    import logging

    logging.getLogger(__name__).warning(
        "DATABASE_URL is not set -- falling back to local SQLite (%s). Data written here "
        "is LOST on restart. Set DATABASE_URL to your Postgres/Supabase connection string.",
        SQLALCHEMY_DATABASE_URL,
    )

connect_args = {}
if SQLALCHEMY_DATABASE_URL.startswith("sqlite"):
    connect_args["check_same_thread"] = False

# pool_pre_ping is not optional against a managed Postgres.
#
# Supabase's pooler (and every other one) closes connections that have sat idle. SQLAlchemy
# keeps handing those dead sockets out, so the API answers "Database connection failed" on
# every request while the database itself is perfectly healthy -- and stays broken until
# the process is restarted. Seen exactly that: /api/health failing while a fresh psycopg2
# connection to the same URL read 1,520 rows.
#
# pre_ping costs one round trip per checkout and turns a dead connection into a silent
# reconnect. pool_recycle retires sockets before the pooler's own idle timeout can.
#
# Both are Postgres-only: SQLite has no server to drop the connection.
_pool_options = {} if SQLALCHEMY_DATABASE_URL.startswith("sqlite") else {
    "pool_pre_ping": True,
    "pool_recycle": 280,
    # SQLAlchemy defaults to 5 + 10 overflow. That was sized for request traffic, where a
    # connection is held for milliseconds -- but a fleet also holds one websocket per
    # screen, and those used to pin a connection each (see routers/websockets.py). With
    # the pinning fixed a burst of screens reconnecting at once still needs more headroom
    # than 15, and heartbeat, sync and play-log ingestion all share this pool.
    #
    # Overridable because the ceiling is the database's, not ours: point DATABASE_URL at
    # Supabase's transaction pooler (port 6543) rather than raising this past what a
    # direct-connection plan allows.
    "pool_size": int(os.getenv("DB_POOL_SIZE", "10")),
    "max_overflow": int(os.getenv("DB_MAX_OVERFLOW", "20")),
}

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args=connect_args, **_pool_options)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

import redis.asyncio as redis_async
from arq.connections import RedisSettings

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
import asyncio
import weakref

# One connection pool PER EVENT LOOP, not one for the process.
#
# redis.asyncio connections bind to the loop that created them. A single shared pool works
# perfectly under uvicorn, which runs one loop forever -- and then fails with
# "RuntimeError: Event loop is closed" the moment anything else touches Redis from a
# different loop: a TestClient request (a fresh loop per request), a management script, an
# asyncio.run() inside a threadpool worker.
#
# That failure mode is quiet in exactly the wrong way. Every Redis call in this codebase is
# wrapped in `except Exception` because Redis being down must not fail a TV's heartbeat, so
# a cross-loop error is swallowed and the feature just silently does nothing -- which is
# how a queued device command could be written and then never read back.
#
# Keyed weakly so a discarded loop takes its pool with it rather than accumulating one per
# request for the life of the process.
_pools: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, redis_async.ConnectionPool]" = (
    weakref.WeakKeyDictionary()
)
_no_loop_pool: redis_async.ConnectionPool | None = None


def _pool_for_current_loop() -> redis_async.ConnectionPool:
    global _no_loop_pool
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Called from synchronous code; there is no loop to key on yet.
        if _no_loop_pool is None:
            _no_loop_pool = redis_async.ConnectionPool.from_url(
                REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5
            )
        return _no_loop_pool

    pool = _pools.get(loop)
    if pool is None:
        pool = redis_async.ConnectionPool.from_url(
            REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5
        )
        _pools[loop] = pool
    return pool


def get_redis():
    return redis_async.Redis(connection_pool=_pool_for_current_loop())

# arq parses the DSN itself, so credentials, TLS (rediss://) and the database index all
# survive. The hand-rolled split() this replaces understood only "redis://host:port/db",
# which is the one shape no managed provider hands out:
#
#   redis://default:pw@host:6379   -> ValueError: int("pw@host") -- crashed at IMPORT, so
#                                     the whole API failed to boot.
#   rediss://default:pw@host:6379  -> did not match "redis://", fell through to
#                                     RedisSettings() = localhost:6379. Worse than the
#                                     crash: the app started, the queue looked healthy,
#                                     and no job ever ran.
REDIS_SETTINGS = RedisSettings.from_dsn(REDIS_URL)
