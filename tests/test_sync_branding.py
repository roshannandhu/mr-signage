"""Phase 3.9: the TV's sync response carries the tenant's own brand once it has paired.

GET /api/screens/{device_id}/sync answered with no brand information at all, so every
paired TV's waiting/idle screen said "MR Signage" regardless of what the tenant had set on
their own Branding page -- the same brand their dashboard header, PDFs and emails already
carried. Pins:
  * a tenant with a brand set gets brand_name, a resolved brand_logo_url and brand_color
  * a tenant who never set one gets null for all three, not the organisation's raw name --
    that fallback belongs on the dashboard header and reports, not on a screen on a wall
  * the demo-reel branch (a suspended/pending workspace) still carries the brand
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-syncbrand-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-sync-branding")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run() -> None:
    client = TestClient(app)
    db = database.SessionLocal()

    branded = models.Organization(
        name="Internal Workspace Name", slug=f"branded-{uuid.uuid4().hex[:8]}", status="active",
        brand_name="Lobby Media Co", brand_color="#112233", logo_url="/uploads/1/logo.png",
    )
    bare = models.Organization(name="Bare Co", slug=f"bare-{uuid.uuid4().hex[:8]}", status="active")
    suspended = models.Organization(
        name="Suspended Co", slug=f"suspended-{uuid.uuid4().hex[:8]}", status="suspended",
        brand_name="Suspended Brand", logo_url="/uploads/1/logo2.png",
    )
    db.add_all([branded, bare, suspended])
    db.flush()
    branded_device = f"branded-{uuid.uuid4().hex[:8]}"
    bare_device = f"bare-{uuid.uuid4().hex[:8]}"
    suspended_device = f"suspended-{uuid.uuid4().hex[:8]}"
    db.add(models.Screen(name="Branded TV", device_id=branded_device, organization_id=branded.id, status="online"))
    db.add(models.Screen(name="Bare TV", device_id=bare_device, organization_id=bare.id, status="online"))
    db.add(models.Screen(name="Suspended TV", device_id=suspended_device, organization_id=suspended.id, status="online"))
    db.commit()
    db.close()

    # --- a tenant with a brand set gets it on sync -------------------------------------------
    sync = client.get(f"/api/screens/{branded_device}/sync")
    check(sync.status_code == 200, f"sync failed: {sync.status_code} {sync.text}")
    body = sync.json()
    check(body.get("brand_name") == "Lobby Media Co", f"expected the tenant's brand name, got {body.get('brand_name')!r}")
    check(body.get("brand_color") == "#112233", f"expected the tenant's brand color, got {body.get('brand_color')!r}")
    check(bool(body.get("brand_logo_url")), f"expected a resolved logo URL, got {body.get('brand_logo_url')!r}")
    check("Internal Workspace Name" not in json.dumps(body),
          "the raw organisation name should never appear in the sync payload")

    # --- a tenant who never set a brand gets null, not their workspace name ------------------
    bare_sync = client.get(f"/api/screens/{bare_device}/sync")
    check(bare_sync.status_code == 200, f"sync failed: {bare_sync.status_code} {bare_sync.text}")
    bare_body = bare_sync.json()
    check(bare_body.get("brand_name") is None, f"an unbranded tenant should get null, got {bare_body.get('brand_name')!r}")
    check(bare_body.get("brand_logo_url") is None, f"an unbranded tenant should get no logo, got {bare_body.get('brand_logo_url')!r}")

    # --- the demo-reel branch (a suspended workspace) still carries the brand ----------------
    suspended_sync = client.get(f"/api/screens/{suspended_device}/sync")
    check(suspended_sync.status_code == 200, f"sync failed: {suspended_sync.status_code} {suspended_sync.text}")
    suspended_body = suspended_sync.json()
    check(suspended_body.get("service_state") == "suspended",
          f"expected the demo-reel branch, got service_state={suspended_body.get('service_state')!r}")
    check(suspended_body.get("brand_name") == "Suspended Brand",
          f"the demo-reel branch should still carry the tenant's brand, got {suspended_body.get('brand_name')!r}")

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Sync branding: a tenant's own brand reaches the TV, an unbranded one gets null not their raw name")


if __name__ == "__main__":
    run()
