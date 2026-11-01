"""The admin tenants list costs the same number of queries for 2 tenants as for 21.

It used to run ~9 queries per tenant plus 2 per ad booking; at production's ~114 ms per
query that made the console's Overview and Tenants pages take most of a minute. Pins:
  * query count is independent of the number of tenants (and of their bookings)
  * the bulk numbers still match how quotas are enforced: archived screens and unclaimed
    pairings are not counted, "online" means heard from recently, an ad holds its slot until
    its effective end (extensions and per-location windows), storage is content + renditions
  * paging returns X-Total-Count, and a search for "%" matches a literal percent sign only
"""
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-querybudget-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-query-budget")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import event  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def seed_tenant(db, plan, name: str) -> int:
    now = models.utcnow()
    org = models.Organization(name=name, slug=f"t-{uuid.uuid4().hex[:8]}", status="active", plan_id=plan.id)
    db.add(org)
    db.flush()
    db.add(models.User(organization_id=org.id, username=f"owner-{uuid.uuid4().hex[:8]}",
                       email=f"{uuid.uuid4().hex[:8]}@x.t", hashed_password="x", role="owner", is_active=True))
    online_screen = models.Screen(name="online", device_id=uuid.uuid4().hex, organization_id=org.id,
                                  status="online", last_seen=now)
    db.add_all([
        online_screen,
        models.Screen(name="offline", device_id=uuid.uuid4().hex, organization_id=org.id, status="online",
                      last_seen=now - timedelta(hours=1)),
        models.Screen(name="archived", device_id=uuid.uuid4().hex, organization_id=org.id, status="online",
                      last_seen=now, deleted_at=now),
        models.Screen(name="unclaimed", device_id=uuid.uuid4().hex, organization_id=org.id,
                      status="waiting_pairing", last_seen=now),
    ])
    content = models.Content(organization_id=org.id, name="ad", file_size_bytes=1000)
    db.add(content)
    db.flush()
    db.add(models.MediaRendition(content_id=content.id, resolution="720p",
                                 width=1280, height=720, file_url="/uploads/x.mp4", file_size_bytes=500))
    extended = models.AdPlacement(organization_id=org.id, content_id=content.id, advertiser="A",
                                  starts_at=now - timedelta(days=10), ends_at=now - timedelta(days=2))
    finished = models.AdPlacement(organization_id=org.id, content_id=content.id, advertiser="B",
                                  starts_at=now - timedelta(days=10), ends_at=now - timedelta(days=2))
    longer_location = models.AdPlacement(organization_id=org.id, content_id=content.id, advertiser="C",
                                         starts_at=now - timedelta(days=10), ends_at=now - timedelta(days=1))
    db.add_all([extended, finished, longer_location])
    db.flush()
    db.add(models.AdPlacementExtension(placement_id=extended.id, extended_from=now - timedelta(days=2),
                                       extended_to=now + timedelta(days=3)))
    db.add(models.AdPlacementTarget(placement_id=longer_location.id, screen_id=online_screen.id,
                                    ends_at=now + timedelta(days=5)))
    db.add_all([
        models.Client(organization_id=org.id, name="c1", client_code=uuid.uuid4().hex[:6]),
        models.Client(organization_id=org.id, name="c2", client_code=uuid.uuid4().hex[:6]),
    ])
    db.commit()
    return org.id


def run() -> None:
    client = TestClient(app)
    db = database.SessionLocal()
    plan = models.Plan(name="P", slug=f"p-{uuid.uuid4().hex[:6]}", feature_flags_json="{}",
                       is_active=True, max_storage_bytes=10 * 1024 ** 3)
    db.add(plan)
    db.flush()
    house = models.Organization(name="House", slug=f"house-{uuid.uuid4().hex[:6]}", status="active")
    db.add(house)
    db.flush()
    admin = models.User(organization_id=house.id, username="budget-admin", email="budget-admin@x.t",
                        hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    db.add(admin)
    db.commit()
    auth = {"Authorization": f"Bearer {login_token(admin)}"}
    first_id = seed_tenant(db, plan, "First Tenant")

    statements = {"n": 0}

    def count(*_args, **_kwargs):
        statements["n"] += 1

    def queries_for_list() -> int:
        statements["n"] = 0
        event.listen(database.engine, "before_cursor_execute", count)
        try:
            response = client.get("/api/admin/tenants", headers=auth)
        finally:
            event.remove(database.engine, "before_cursor_execute", count)
        check(response.status_code == 200, f"tenants list failed: {response.status_code} {response.text[:200]}")
        return statements["n"]

    small = queries_for_list()

    rows = {row["id"]: row for row in client.get("/api/admin/tenants", headers=auth).json()}
    first = rows.get(first_id, {})
    check(first.get("screens_count") == 2, f"archived and unclaimed screens must not count: {first.get('screens_count')}")
    check(first.get("online_screens_count") == 1, f"only the recently-seen screen is online: {first.get('online_screens_count')}")
    check(first.get("ad_slots_used") == 2, f"extended and longer-location bookings hold slots: {first.get('ad_slots_used')}")
    check(first.get("clients_used") == 2, f"clients wrong: {first.get('clients_used')}")
    check(first.get("storage_used_bytes") == 1500, f"storage is content + renditions: {first.get('storage_used_bytes')}")
    check(first.get("owner_email", "").endswith("@x.t"), f"owner missing: {first.get('owner_email')}")

    for index in range(19):
        seed_tenant(db, plan, f"Tenant {index}")
    percent_id = seed_tenant(db, plan, "100% Ads")
    db.close()

    large = queries_for_list()
    check(large == small, f"query count must not grow with tenants: {small} for 2 tenants, {large} for 22")

    paged = client.get("/api/admin/tenants?limit=5&offset=0&sort=name_asc", headers=auth)
    check(paged.status_code == 200 and len(paged.json()) == 5, f"paging returned {len(paged.json())} rows")
    check(paged.headers.get("x-total-count") == "22", f"X-Total-Count wrong: {paged.headers.get('x-total-count')}")
    names = [row["name"] for row in paged.json()]
    check(names == sorted(names, key=str.lower), f"name sort wrong: {names}")

    literal = client.get("/api/admin/tenants?q=%25", headers=auth).json()
    check([row["id"] for row in literal] == [percent_id],
          f"searching '%' must match only names containing a literal %: {[row['name'] for row in literal]}")

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print(f"Admin query budget: tenants list is {small} queries for 2 tenants and for 22; counts match quota rules")


if __name__ == "__main__":
    run()
