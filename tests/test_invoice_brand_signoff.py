"""The emailed invoice and report sign off as the tenant's own brand, not the raw org name.

_render_invoice already fell back to brand_name on the PDF itself; the email route's sign-off
line used organization.name regardless, so a tenant who set a brand name for their clients
still had every emailed invoice end "Regards, Default Organization".

mailer.send is monkeypatched to capture its call rather than actually sending -- SMTP is
never configured in tests, so without this every email route 503s before the body is built.
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-invoice-signoff-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-invoice-signoff")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, mailer, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import get_password_hash, login_token  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run() -> None:
    db = database.SessionLocal()
    plan = models.Plan(
        name="Full", slug=f"full-{uuid.uuid4().hex[:8]}",
        feature_flags_json=json.dumps({"payslips": True}), is_active=True, max_storage_bytes=10 * 1024 ** 3,
    )
    db.add(plan)
    db.flush()
    org = models.Organization(
        name="Default Organization", brand_name="Lobby Media Co", slug=f"org-{uuid.uuid4().hex[:8]}",
        status="active", plan_id=plan.id,
    )
    db.add(org)
    db.flush()
    owner = models.User(organization_id=org.id, username=f"owner-{uuid.uuid4().hex[:6]}", email=None,
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    content = models.Content(organization_id=org.id, type="image", file_url="/uploads/1/a.jpg", name="Ad")
    client_row = models.Client(organization_id=org.id, name="Acme", client_code=f"C{uuid.uuid4().hex[:6].upper()}",
                               email="acme@example.com")
    db.add_all([owner, content, client_row])
    db.flush()
    now = models.utcnow()
    placement = models.AdPlacement(
        organization_id=org.id, content_id=content.id, advertiser="Acme", client_id=client_row.id,
        starts_at=now - timedelta(days=1), ends_at=now + timedelta(days=10),
    )
    db.add(placement)
    db.commit()
    token = login_token(owner)
    placement_id = placement.id
    db.close()

    captured = {}

    def fake_send(**kwargs):
        captured.update(kwargs)

    original = mailer.send
    mailer.send = fake_send
    try:
        client = TestClient(app)
        resp = client.post(f"/api/placements/{placement_id}/email?document=invoice",
                           headers={"Authorization": f"Bearer {token}"})
        check(resp.status_code == 200, f"emailing the invoice should succeed: {resp.status_code} {resp.text}")
    finally:
        mailer.send = original

    body = captured.get("body", "")
    check("Lobby Media Co" in body, f"the sign-off should use the brand name: {body!r}")
    check("Default Organization" not in body, f"the raw organisation name should not leak into a branded sign-off: {body!r}")

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
    print("OK - the emailed invoice signs off as the tenant's brand, not the raw organisation name")
