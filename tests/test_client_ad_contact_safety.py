"""Editing an advert must never overwrite another client's contact details.

The "Edit client & ad details" modal pre-fills the email and phone from the advert's CURRENT
client. Pointing the advert at a different, existing client therefore sent the OLD client's
contact details, and the route wrote them onto the new client -- whose reports then went to
the wrong address. The client lookup also used `ilike`, so a client called "%" matched
whichever client sorted first. This pins:
  * re-pointing at another client leaves that client's contact details alone
  * a blank contact field on that client may still be filled
  * correcting the advert's OWN client's contact details still works
  * a name made of wildcard characters matches nothing but itself
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-contact-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-client-ad-contact")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def setup():
    db = database.SessionLocal()
    org = models.Organization(name="Venue", slug=f"v-{uuid.uuid4().hex[:8]}", status="active")
    db.add(org)
    db.flush()
    db.add(models.User(organization_id=org.id, username="owner", email="ow@x.t",
                       hashed_password=get_password_hash("x"), role="owner", is_active=True))
    db.add_all([
        models.Client(organization_id=org.id, name="Acme", client_code="ACME111", email="acme@x.com", phone="111"),
        models.Client(organization_id=org.id, name="Beta", client_code="BETA222", email="beta@x.com", phone="222"),
        models.Client(organization_id=org.id, name="Gamma", client_code="GAMM333", email="gamma@x.com", phone=None),
    ])
    content = models.Content(organization_id=org.id, type="video", file_url="/uploads/1/a.mp4",
                             name="Ad", status="ready", duration_ms=30_000)
    db.add(content)
    db.commit()
    content_id = content.id
    db.close()
    return content_id


def contact(name: str):
    db = database.SessionLocal()
    try:
        rows = db.query(models.Client).filter(models.Client.name == name).all()
        return [(row.email, row.phone) for row in rows]
    finally:
        db.close()


def run() -> None:
    content_id = setup()
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'owner'})}"}
    url = f"/api/content/{content_id}/client-ad"

    def edit(**body):
        return client.put(url, headers=headers, json=body)

    # The advert is booked to Acme.
    check(edit(client_name="Acme").status_code == 200, "could not book the advert to Acme")

    # --- re-point at Beta, sending what the modal pre-filled from Acme ----------------------
    moved = edit(client_name="Beta", client_email="acme@x.com", client_phone="111")
    check(moved.status_code == 200, f"re-pointing failed: {moved.text}")
    check(contact("Beta") == [("beta@x.com", "222")], f"Beta's contact details were overwritten: {contact('Beta')}")
    check(contact("Acme") == [("acme@x.com", "111")], f"Acme's contact details changed: {contact('Acme')}")

    # --- a blank field on the other client may be filled, a set one may not -----------------
    filled = edit(client_name="Gamma", client_email="someone@else.com", client_phone="999")
    check(filled.status_code == 200, f"re-pointing at Gamma failed: {filled.text}")
    check(contact("Gamma") == [("gamma@x.com", "999")], f"Gamma should keep its email and gain a phone: {contact('Gamma')}")

    # --- correcting the CURRENT client's own details still works ----------------------------
    fixed = edit(client_name="Gamma", client_email="fixed@gamma.com", client_phone="888")
    check(fixed.status_code == 200, f"correcting the current client failed: {fixed.text}")
    check(contact("Gamma") == [("fixed@gamma.com", "888")], f"the current client's details were not updated: {contact('Gamma')}")

    # --- a wildcard name is just a name ------------------------------------------------------
    for wildcard in ("%", "_"):
        made = edit(client_name=wildcard, client_email="wild@x.com", client_phone="000")
        check(made.status_code == 200, f"creating a client called {wildcard!r} failed: {made.text}")
    check(contact("%") == [("wild@x.com", "000")], f"a client called '%' was not created on its own: {contact('%')}")
    check(contact("Acme") == [("acme@x.com", "111")], "a wildcard name overwrote Acme")
    check(contact("Beta") == [("beta@x.com", "222")], "a wildcard name overwrote Beta")


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
    print("OK - editing an advert never overwrites another client's contact details")
