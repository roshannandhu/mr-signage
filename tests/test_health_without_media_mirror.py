"""/api/health answers 200 on a database that has no media_blob table.

The table has no model and no migration; it was created by hand and a fresh database does not
have it. The health check probed it on the request's own session and swallowed the failure, but on
Postgres a failed statement aborts the transaction, so the very next query in the handler failed
and the endpoint returned 500. Render's health check hits this route (render.yaml healthCheckPath),
so a freshly built database would have looked down. SQLite does not abort transactions, which is
why every other health test passed.

Needs PostgreSQL on localhost:5432 (it creates and drops its own database).
"""
import os
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

DB = f"olrac_test_health_{uuid.uuid4().hex[:8]}"
FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def admin_connection():
    conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    return conn


def run() -> None:
    admin = admin_connection()
    admin.cursor().execute(f"CREATE DATABASE {DB} OWNER olrac")
    admin.close()
    url = f"postgresql://olrac:olrac_password@localhost:5432/{DB}"
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("SECRET_KEY", "pytest-health-mirror")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

    from backend import database, models

    models.Base.metadata.create_all(bind=database.engine)
    import backend.main  # noqa: F401
    from backend import media_urls
    from fastapi.testclient import TestClient

    with database.engine.connect() as conn:
        present = conn.execute(sa.text("SELECT to_regclass('public.media_blob')")).scalar()
    check(present is None, "setup error: the throwaway database should not have a media_blob table")

    # The probe only runs when there is no object storage and no public bucket. Under pytest a
    # `mock` key counts as real storage, so say so directly rather than depend on the environment.
    media_urls.is_s3_enabled = lambda: False
    media_urls.public_media_base = lambda: ""

    client = TestClient(backend.main.app, raise_server_exceptions=False)
    response = client.get("/api/health")
    check(response.status_code == 200, f"health returned {response.status_code} without a media_blob table: {response.text[:200]}")
    if response.status_code == 200:
        check(response.json().get("database") == "connected", f"unexpected health body: {response.json()}")

    database.engine.dispose()


if __name__ == "__main__":
    try:
        run()
    finally:
        try:
            admin = admin_connection()
            admin.cursor().execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
            admin.close()
        except Exception:  # noqa: BLE001
            pass
    if FAILURES:
        print("FAIL")
        for failure in FAILURES:
            print("  -", failure)
        sys.exit(1)
    print("OK - health stays up on a database with no media mirror table")
