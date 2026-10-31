"""Deleting an advert or a booking must not destroy bookings that are live or paid for.

Deleting an advert cascaded to its bookings, targets, extensions and PAYMENT RECEIPTS with no
check at all, and deleting a booking removed its receipts. A receipt is the only record that
money changed hands. This pins:
  * an advert with a running booking cannot be deleted; one whose bookings are finished and
    unpaid can
  * a booking with recorded payments cannot be deleted until they are removed deliberately
  * giving a FINISHED booking a future end date takes an ad slot back, so it has to pass the
    ad-slot limit -- through both PUT /placements/{id} and the client-ad editor
"""
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-delguard-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-booking-deletion")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402
from tests._test_plans import grant_plan  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def iso(delta_days: int) -> str:
    return (models.utcnow() + timedelta(days=delta_days)).isoformat()


def setup():
    db = database.SessionLocal()
    org = models.Organization(name="Venue", slug=f"v-{uuid.uuid4().hex[:8]}", status="active", max_ad_slots=1)
    db.add(org)
    db.flush()
    grant_plan(db, org, payslips=True)  # the payment it records below is sold behind this flag
    db.add(models.User(organization_id=org.id, username="owner", email="ow@x.t",
                       hashed_password=get_password_hash("x"), role="owner", is_active=True))
    contents = [
        models.Content(organization_id=org.id, type="video", file_url=f"/uploads/1/{name}.mp4",
                       name=name, status="ready", duration_ms=30_000)
        for name in ("live", "paid", "past", "revive")
    ]
    db.add_all(contents)
    db.commit()
    ids = {content.name: content.id for content in contents}
    db.close()
    return ids


def run() -> None:
    ids = setup()
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'owner'})}"}

    def book(name: str, start: int, end: int):
        return client.post("/api/placements/", headers=headers, json={
            "content_id": ids[name], "advertiser": f"{name} client", "price_paise": 100000,
            "starts_at": iso(start), "ends_at": iso(end), "targets": [],
        })

    def content_exists(name: str) -> bool:
        db = database.SessionLocal()
        try:
            return db.query(models.Content).filter(models.Content.id == ids[name]).count() == 1
        finally:
            db.close()

    # The ad-slot limit is 1 and a running booking holds it, so the finished bookings are made
    # first, while nothing is running.
    past = book("past", -40, -10)
    check(past.status_code == 201, f"could not create the finished booking: {past.text}")
    paid = book("paid", -40, -10)
    check(paid.status_code == 201, f"could not create the paid booking: {paid.text}")
    revive = book("revive", -40, -10)
    check(revive.status_code == 201, f"could not create the booking to revive: {revive.text}")

    # --- an advert with a running booking cannot be deleted ---------------------------------
    live = book("live", -1, 10)
    check(live.status_code == 201, f"could not create the live booking: {live.text}")
    refused = client.delete(f"/api/content/{ids['live']}", headers=headers)
    check(refused.status_code == 409, f"a running advert was deletable ({refused.status_code})")
    check(content_exists("live"), "the running advert was deleted anyway")

    # --- a finished, unpaid booking does not block deletion -----------------------------------
    gone = client.delete(f"/api/content/{ids['past']}", headers=headers)
    check(gone.status_code == 200, f"a finished unpaid advert could not be deleted: {gone.text}")
    check(not content_exists("past"), "the finished advert survived its deletion")

    # --- payments protect both the booking and the advert ------------------------------------
    paid_id = paid.json()["id"]
    receipt = client.post(f"/api/placements/{paid_id}/payments", headers=headers,
                          json={"amount_paise": 50000, "method": "cash"})
    check(receipt.status_code == 201, f"could not record a payment: {receipt.text}")
    check(client.delete(f"/api/placements/{paid_id}", headers=headers).status_code == 409,
          "a booking with a payment on it was deletable")
    check(client.delete(f"/api/content/{ids['paid']}", headers=headers).status_code == 409,
          "an advert whose booking has a payment on it was deletable")
    check(content_exists("paid"), "the paid advert (and its receipt) was deleted anyway")

    payment_id = receipt.json()["payments"][0]["id"]
    removed = client.delete(f"/api/placements/{paid_id}/payments/{payment_id}", headers=headers)
    check(removed.status_code == 200, f"could not remove the payment: {removed.text}")
    check(client.delete(f"/api/placements/{paid_id}", headers=headers).status_code == 200,
          "a booking could not be deleted once its payments were removed")

    # --- reviving a finished booking takes a slot --------------------------------------------
    revive_id = revive.json()["id"]
    blocked = client.put(f"/api/placements/{revive_id}", headers=headers, json={"ends_at": iso(20)})
    check(blocked.status_code == 409,
          f"PUT revived a finished booking past the ad-slot limit ({blocked.status_code})")
    editor = client.put(f"/api/content/{ids['revive']}/client-ad", headers=headers,
                        json={"client_name": "revive client", "duration_days": 90})
    check(editor.status_code == 409,
          f"the client-ad editor revived a finished booking past the ad-slot limit ({editor.status_code})")

    # An ordinary edit of a finished booking (a note, a price) is not a revival.
    harmless = client.put(f"/api/placements/{revive_id}", headers=headers, json={"notes": "sold in May"})
    check(harmless.status_code == 200, f"an ordinary edit of a finished booking was refused: {harmless.text}")

    # With the slot free again the same edit is allowed.
    ended = client.put(f"/api/placements/{live.json()['id']}", headers=headers, json={"ends_at": iso(-1)})
    check(ended.status_code == 200, f"could not end the live booking: {ended.text}")
    allowed = client.put(f"/api/placements/{revive_id}", headers=headers, json={"ends_at": iso(20)})
    check(allowed.status_code == 200, f"revival was refused with a free slot: {allowed.text}")


if __name__ == "__main__":
    try:
        run()
    finally:
        database.engine.dispose()
        _db_file.unlink(missing_ok=True)
    if FAILURES:
        print("FAIL")
        for failure in FAILURES:
            print("  -", failure)
        sys.exit(1)
    print("OK - live and paid bookings survive deletion, and reviving one respects the ad-slot limit")
