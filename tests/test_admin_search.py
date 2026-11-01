"""GET /api/admin/search finds tenants, users, screens, payments and releases by name/id.

Pins:
  * each type matches on the right columns (tenant name/slug, user email/username, screen
    name/device_id, payment provider ids, release version name)
  * a literal % in the query matches only a literal % in the data, not "everything"
  * a query under 2 characters returns no results rather than a huge unfiltered scan
  * a non-admin is refused
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-adminsearch-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-admin-search")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run() -> None:
    client = TestClient(app)
    db = database.SessionLocal()

    org = models.Organization(name="Findable Signage Co", slug=f"findable-{uuid.uuid4().hex[:8]}", status="active")
    percent_org = models.Organization(name="100% Ads Co", slug=f"percent-{uuid.uuid4().hex[:8]}", status="active")
    db.add_all([org, percent_org])
    db.flush()

    admin = models.User(organization_id=org.id, username="srch-admin", email="srch-admin@x.t",
                        hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    owner = models.User(organization_id=org.id, username="srch-findable-owner", email="findable@x.t",
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    non_admin = models.User(organization_id=org.id, username="srch-plain", email="srch-plain@x.t",
                            hashed_password=get_password_hash("x"), role="viewer", is_active=True)
    db.add_all([admin, owner, non_admin])

    screen = models.Screen(name="Lobby Findable TV", device_id=f"findable-dev-{uuid.uuid4().hex[:8]}",
                           organization_id=org.id, status="online")
    db.add(screen)

    payment = models.PlatformPayment(organization_id=org.id, kind="plan", amount_paise=100000,
                                     currency="INR", provider="razorpay",
                                     provider_order_id="order_FindableXYZ", status="captured")
    db.add(payment)

    release = models.AppRelease(version_code=9001, version_name="9.9.9-findable",
                               apk_url="https://example.com/a.apk", sha256="a" * 64, rollout_state="released")
    db.add(release)

    db.commit()
    org_id, percent_org_id, screen_id, payment_id = org.id, percent_org.id, screen.id, payment.id
    admin_auth = {"Authorization": f"Bearer {login_token(admin)}"}
    non_admin_auth = {"Authorization": f"Bearer {login_token(non_admin)}"}
    db.close()

    refused = client.get("/api/admin/search?q=Findable", headers=non_admin_auth)
    check(refused.status_code == 403, f"a non-admin should be refused, got {refused.status_code}")

    too_short = client.get("/api/admin/search?q=F", headers=admin_auth)
    check(too_short.status_code == 200 and too_short.json()["results"] == [],
          f"a 1-character query should return nothing, got {too_short.json()}")

    found = client.get("/api/admin/search?q=Findable", headers=admin_auth).json()["results"]
    types = {hit["type"] for hit in found}
    check({"tenant", "user", "screen", "payment", "release"} <= types,
          f"expected a hit of every type for 'Findable', got types {types}: {found}")
    check(any(h["type"] == "tenant" and h["id"] == org_id for h in found), f"tenant hit missing/wrong: {found}")
    check(any(h["type"] == "user" and h["label"] == "findable@x.t" for h in found), f"user hit missing/wrong: {found}")
    check(any(h["type"] == "screen" and h["id"] == screen_id for h in found), f"screen hit missing/wrong: {found}")
    check(any(h["type"] == "payment" and h["id"] == payment_id for h in found), f"payment hit missing/wrong: {found}")
    check(any(h["type"] == "release" and h["id"] == 9001 for h in found), f"release hit missing/wrong: {found}")

    percent_hits = client.get("/api/admin/search?q=100%25", headers=admin_auth).json()["results"]
    tenant_hits = [h for h in percent_hits if h["type"] == "tenant"]
    check(len(tenant_hits) == 1 and tenant_hits[0]["id"] == percent_org_id,
          f"'%' should match only the literal percent-sign tenant, got {tenant_hits}")
    unrelated_hits = [h for h in client.get("/api/admin/search?q=Findable", headers=admin_auth).json()["results"]
                      if h["type"] == "tenant" and h["id"] == percent_org_id]
    check(unrelated_hits == [], f"the '%'-named tenant should not match a 'Findable' query: {unrelated_hits}")

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Admin search: every type matched, literal % escaped, short queries empty, non-admin refused")


if __name__ == "__main__":
    run()
