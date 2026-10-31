"""GET /billing/public-plans: the storefront for someone who has not signed in yet.

The existing /billing/plans needs a login, which is fine once a pending-approval workspace
exists (it is the only caller) but is no use to a visitor who hasn't signed up at all -- the
new /signup page needs to show real prices and limits before any account exists.

Pins:
  * no Authorization header needed
  * an inactive plan (retired) and a hidden custom-<id> plan (is_active=False, minted per
    paid custom request) are both excluded
  * max_ad_slots is present and carries the plan's own value (0 = unlimited, same as
    max_clients)
  * ordered by price, cheapest first
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-public-plans-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-public-plans")
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
    db = database.SessionLocal()
    visible = models.Plan(
        name="Starter", slug=f"starter-{uuid.uuid4().hex[:6]}", monthly_price_paise=99_900,
        yearly_price_paise=999_000, price_paise=50_000, duration_days=30, max_screens=10,
        max_clients=10, max_ad_slots=20, max_storage_bytes=25 * 1024 ** 3,
        feature_flags_json=json.dumps({"scheduling": True}), is_active=True,
    )
    retired = models.Plan(
        name="Old Starter", slug=f"retired-{uuid.uuid4().hex[:6]}", price_paise=40_000,
        duration_days=30, max_storage_bytes=10 * 1024 ** 3, is_active=False,
    )
    custom = models.Plan(
        name="Custom plan #9", slug=f"custom-{uuid.uuid4().hex[:6]}", price_paise=500_000,
        duration_days=90, max_storage_bytes=50 * 1024 ** 3, is_active=False,
    )
    db.add_all([visible, retired, custom])
    db.commit()
    db.close()

    client = TestClient(app)
    resp = client.get("/api/billing/public-plans")
    check(resp.status_code == 200, f"should need no auth, got {resp.status_code}: {resp.text}")
    body = resp.json()
    names = [p["name"] for p in body]
    check("Starter" in names, f"an active plan should be listed: {names}")
    check("Old Starter" not in names, f"a retired (inactive) plan should not be listed: {names}")
    check("Custom plan #9" not in names, f"a hidden custom-minted plan should not be listed: {names}")

    starter = next((p for p in body if p["name"] == "Starter"), None)
    check(starter is not None and starter.get("max_ad_slots") == 20,
         f"max_ad_slots should carry the plan's own value: {starter}")

    prices = [p["price_paise"] for p in body]
    check(prices == sorted(prices), f"should be ordered cheapest first: {prices}")

    database.engine.dispose()


if __name__ == "__main__":
    try:
        run()
    finally:
        _db_file.unlink(missing_ok=True)
    if FAILURES:
        print("FAIL")
        for failure in FAILURES:
            print("  -", failure)
        sys.exit(1)
    print("OK - public-plans needs no login and hides inactive and hidden plans")
