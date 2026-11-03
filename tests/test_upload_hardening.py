"""Uploads and the mail status route, hardened.

  * A screen capture must be an image: a file named x.html used to be stored, and on local disk
    served from /uploads on the API's own origin.
  * GET /placements/email-status needs a signed-in user, and answers instead of crashing when
    mail is not configured (it called a mailer function that did not exist, so it returned 500
    in exactly the state it is asked about).
"""
import os
import pathlib
import sys
import tempfile
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-uploads-{uuid.uuid4().hex}.db"
_uploads = tempfile.TemporaryDirectory(prefix="olrac-uploads-dir-")
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-upload-hardening")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")
for name in ("SMTP_HOST", "SMTP_FROM", "SMTP_USER"):
    os.environ.pop(name, None)

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run() -> None:
    db = database.SessionLocal()
    org = models.Organization(name="Venue", slug=f"v-{uuid.uuid4().hex[:8]}", status="active")
    db.add(org)
    db.flush()
    db.add(models.User(organization_id=org.id, username="owner", email="ow@x.t",
                       hashed_password=get_password_hash("x"), role="owner", is_active=True))
    db.commit()
    db.close()

    client = TestClient(app)

    # --- email-status -----------------------------------------------------------------------
    check(client.get("/api/placements/email-status").status_code == 401,
          "email-status answered without a signed-in user")
    headers = {"Authorization": f"Bearer {create_access_token({'sub': 'owner'})}"}
    status = client.get("/api/placements/email-status", headers=headers)
    check(status.status_code == 200, f"email-status crashed when mail is unset ({status.status_code}): {status.text[:120]}")
    if status.status_code == 200:
        body = status.json()
        check(body["is_configured"] is False and body["sender"] is None, f"unexpected status: {body}")
        check("SMTP_HOST" in body["missing_variables"], f"the missing settings were not named: {body}")


if __name__ == "__main__":
    try:
        run()
    finally:
        database.engine.dispose()
        _db_file.unlink(missing_ok=True)
        _uploads.cleanup()
    if FAILURES:
        print("FAIL")
        for failure in FAILURES:
            print("  -", failure)
        sys.exit(1)
    print("OK - email-status is signed-in and does not crash")
