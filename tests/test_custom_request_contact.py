"""A custom-plan request can name who to actually reach about it (3.4).

Before this, a custom request carried the counts and a free-text note; an operator pricing
one only ever saw whatever organisation name was already on the account, never a company
name, a named contact, or a way to reach them that wasn't already in the users table.

Pins:
  * company/contact name, email and phone round-trip through create -> admin list
  * an invalid email or phone is refused with 422 and nothing is created
  * a cleared (empty-string) email or phone is accepted and stored as absent, not rejected
    -- a browser form sends "" for an untouched optional field, not nothing at all
  * the fields are genuinely optional: a request with none of them still succeeds
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-custom-contact-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-custom-contact")
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
    db = database.SessionLocal()
    org = models.Organization(name="Acme Retail", slug=f"acme-{uuid.uuid4().hex[:8]}", status="pending_approval")
    db.add(org)
    db.flush()
    owner = models.User(organization_id=org.id, username=f"owner-{uuid.uuid4().hex[:6]}", email=None,
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add(owner)
    db.commit()
    token = login_token(owner)
    headers = {"Authorization": f"Bearer {token}"}
    db.close()

    client = TestClient(app)

    base = {"max_screens": 20, "max_clients": 20, "max_ad_slots": 10, "max_storage_bytes": 50 * 1024 ** 3, "duration_days": 60}

    # --- the happy path: everything round-trips --------------------------------------------
    resp = client.post("/api/billing/custom-request", headers=headers, json={
        **base,
        "company_name": "Acme Retail Pvt Ltd",
        "contact_name": "Priya Nair",
        "contact_email": "priya@acmeretail.com",
        "contact_phone": "+91 98765 43210",
    })
    check(resp.status_code == 201, f"a well-formed request should succeed: {resp.status_code} {resp.text}")
    body = resp.json()
    check(body.get("company_name") == "Acme Retail Pvt Ltd", body)
    check(body.get("contact_name") == "Priya Nair", body)
    check(body.get("contact_email") == "priya@acmeretail.com", body)
    check(body.get("contact_phone") == "+91 98765 43210", body)

    # --- an invalid email is refused, and nothing is created --------------------------------
    before = client.get("/api/billing/custom-request", headers=headers).json()
    bad_email = client.post("/api/billing/custom-request", headers=headers, json={**base, "contact_email": "not-an-email"})
    check(bad_email.status_code == 422, f"a malformed email should be refused, got {bad_email.status_code}")
    after = client.get("/api/billing/custom-request", headers=headers).json()
    check(len(after) == len(before), "a refused request should not have been created")

    # --- an invalid phone is refused too -----------------------------------------------------
    bad_phone = client.post("/api/billing/custom-request", headers=headers, json={**base, "contact_phone": "call me maybe"})
    check(bad_phone.status_code == 422, f"a malformed phone should be refused, got {bad_phone.status_code}")

    # --- a cleared (empty-string) field is accepted, not rejected ---------------------------
    cleared = client.post("/api/billing/custom-request", headers=headers, json={
        **base, "contact_email": "", "contact_phone": "", "company_name": "", "contact_name": "",
    })
    check(cleared.status_code == 201, f"empty-string optional fields should be accepted, got {cleared.status_code}: {cleared.text}")
    check(cleared.json().get("contact_email") is None, cleared.json())
    check(cleared.json().get("contact_phone") is None, cleared.json())

    # --- every contact field is genuinely optional -------------------------------------------
    bare = client.post("/api/billing/custom-request", headers=headers, json=base)
    check(bare.status_code == 201, f"a request with no contact fields at all should still succeed: {bare.status_code} {bare.text}")

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
    print("OK - a custom request's contact details validate, round-trip, and stay optional")
