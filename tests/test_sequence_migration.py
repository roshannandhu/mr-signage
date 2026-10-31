"""A database built through the migration chain can take its first signup and its first new package.

d5a7b9c2e4f1 seeded the default organisation with id 1 and e6b8c0d3f5a2 the packages with ids 1-3,
both on Postgres serial columns. An explicit id does not move the sequence, so the first ORM
insert (no id given) drew 1 and failed with a duplicate key. The migration c8f2a4b6d0e1 sets every
id sequence to where its data already is. This builds exactly that state on a throwaway Postgres
database, runs the migration, and checks the next inserts succeed.

Needs PostgreSQL on localhost:5432 (it creates and drops its own database).
"""
import importlib.util
import os
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import psycopg2  # noqa: E402
import sqlalchemy as sa  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

DB = f"olrac_test_seq_{uuid.uuid4().hex[:8]}"
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
    os.environ.setdefault("SECRET_KEY", "pytest-sequence-migration")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

    from backend import database, models

    engine = sa.create_engine(url)
    models.Base.metadata.create_all(bind=engine)

    Session = sa.orm.sessionmaker(bind=engine)

    # The state the old migrations left behind: rows with explicit ids, sequences untouched.
    # Through the ORM so the model's defaults fill the NOT NULL columns; an explicit id does not
    # advance the sequence, which is the whole bug.
    seed = Session()
    seed.add(models.Organization(id=1, name="Default Organization", slug="default", status="active"))
    for plan_id, slug in ((1, "free"), (2, "starter"), (3, "business")):
        seed.add(models.Plan(id=plan_id, name=slug, slug=slug, max_storage_bytes=10 * 1024 ** 3, max_screens=5))
    seed.commit()
    seed.close()


    def first_insert_fails() -> bool:
        session = Session()
        try:
            session.add(models.Organization(name="New Co", slug=f"new-{uuid.uuid4().hex[:6]}"))
            session.commit()
            return False
        except sa.exc.IntegrityError:
            session.rollback()
            return True
        finally:
            session.close()

    # Prove the setup really reproduces the bug before trusting the fix.
    check(first_insert_fails(), "setup error: the seeded sequence should collide on its first insert")

    # Rebuild the unfixed state (the failed insert above consumed a value), then migrate.
    with engine.begin() as conn:
        conn.execute(sa.text("SELECT setval(pg_get_serial_sequence('organizations', 'id'), 1, false)"))
        conn.execute(sa.text("SELECT setval(pg_get_serial_sequence('plans', 'id'), 1, false)"))

    path = next(pathlib.Path(__file__).resolve().parents[1].glob("backend/alembic/versions/c8f2a4b6d0e1_*.py"))
    spec = importlib.util.spec_from_file_location("seq_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
        # Idempotent: a second run changes nothing and does not fail.
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()

    session = Session()
    try:
        org = models.Organization(name="First Signup", slug=f"signup-{uuid.uuid4().hex[:6]}")
        session.add(org)
        session.commit()
        check(org.id == 2, f"the first signup should draw id 2 after the seeded row, got {org.id}")

        plan = models.Plan(name="Fourth", slug="fourth", max_storage_bytes=1)
        session.add(plan)
        session.commit()
        check(plan.id == 4, f"the first new package should draw id 4 after ids 1-3, got {plan.id}")

        # An empty table must still start from 1, not 0 or 2.
        user = models.User(organization_id=org.id, username="u1", hashed_password="x", role="owner")
        session.add(user)
        session.commit()
        check(user.id == 1, f"an empty table's first id should be 1, got {user.id}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"inserting after the migration failed: {exc}")
    finally:
        session.close()
        engine.dispose()
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
    print("OK - the first signup and the first new package take the ids after the seeded rows")
