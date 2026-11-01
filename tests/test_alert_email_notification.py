"""Phase 3.8: a newly-opened critical or warning alert emails the owners and editors who
can act on it, exactly once, and only on a plan that actually sells "Remote control & alerts".

worker.reconcile_alerts recorded nothing in Alert.notified and nothing in an inbox --
"delivery to phones and inboxes" was an explicit TODO left for later. notify_alert_opened
is the hook for it, called from the one place every alert is raised (reconcile_org_alerts),
so both the arq reconciler and the in-process health monitor get it for free.

mailer.send is monkeypatched to capture its calls rather than actually sending, following
the pattern in test_invoice_brand_signoff.py -- SMTP is never configured in tests. Pins:
  * an owner and an editor with an email each get one message; a viewer gets none
  * a plan without remote_control sends nothing
  * a second reconcile pass for the same still-open alert does not send a second email
  * Alert.notified records "email" once sent
"""
import json
import os
import pathlib
import sys
import tempfile
import uuid
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-alertmail-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-alert-email")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")
# notify_alert_opened checks mailer.is_configured(); these make that true without a real
# SMTP server. mailer.send itself is monkeypatched below, so nothing actually connects.
os.environ.setdefault("SMTP_HOST", "smtp.example.com")
os.environ.setdefault("SMTP_FROM", "alerts@example.com")

from backend import database, mailer, models  # noqa: E402
import backend.main  # noqa: E402,F401  (import order; see test_subscription_expiry)
from backend.routers.auth import get_password_hash  # noqa: E402
from backend.worker import reconcile_org_alerts  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def make_org(db, *, remote_control: bool, slug: str):
    plan = models.Plan(
        name="Alert Plan", slug=f"{slug}-plan",
        feature_flags_json=json.dumps({"remote_control": remote_control}),
        is_active=True, max_storage_bytes=10 * 1024 ** 3,
    )
    db.add(plan)
    db.flush()
    org = models.Organization(name=f"Org {slug}", slug=f"{slug}-{uuid.uuid4().hex[:8]}",
                              status="active", plan_id=plan.id)
    db.add(org)
    db.flush()
    db.add(models.User(organization_id=org.id, username=f"owner-{slug}", email=f"owner-{slug}@x.t",
                       hashed_password=get_password_hash("x"), role="owner", is_active=True))
    db.add(models.User(organization_id=org.id, username=f"editor-{slug}", email=f"editor-{slug}@x.t",
                       hashed_password=get_password_hash("x"), role="editor", is_active=True))
    db.add(models.User(organization_id=org.id, username=f"viewer-{slug}", email=f"viewer-{slug}@x.t",
                       hashed_password=get_password_hash("x"), role="viewer", is_active=True))
    db.add(models.Screen(name="Lobby TV", device_id=f"{slug}-dev", organization_id=org.id,
                         status="online", last_seen=datetime(2020, 1, 1, tzinfo=timezone.utc)))
    db.commit()
    return org.id


def run() -> None:
    sent: list[dict] = []

    def fake_send(**kwargs):
        sent.append(kwargs)

    original = mailer.send
    mailer.send = fake_send
    try:
        db = database.SessionLocal()
        with_flag = make_org(db, remote_control=True, slug="withflag")
        without_flag = make_org(db, remote_control=False, slug="noflag")
        now = datetime.now(timezone.utc)

        # --- a plan WITH remote_control emails the owner and editor, not the viewer --------
        raised, _ = reconcile_org_alerts(db, with_flag, now)
        check(len(raised) == 1, f"expected one offline alert, got {len(raised)}")
        to_addresses = sorted(m["to"] for m in sent)
        check(to_addresses == ["editor-withflag@x.t", "owner-withflag@x.t"],
              f"expected exactly the owner and editor, got {to_addresses}")

        db.expire_all()
        alert = db.query(models.Alert).filter(models.Alert.organization_id == with_flag).one()
        check(alert.notified == ["email"], f"Alert.notified should record the send, got {alert.notified!r}")

        # --- a second pass for the SAME still-open alert sends nothing more -----------------
        sent.clear()
        raised_again, _ = reconcile_org_alerts(db, with_flag, now)
        check(not raised_again, "the already-open alert should not be raised a second time")
        check(not sent, f"a second pass should send no further email, got {sent}")

        # --- a plan WITHOUT remote_control sends nothing at all ------------------------------
        sent.clear()
        raised_bare, _ = reconcile_org_alerts(db, without_flag, now)
        check(len(raised_bare) == 1, f"expected one offline alert on the bare org too, got {len(raised_bare)}")
        check(not sent, f"a plan without remote_control should send no email, got {sent}")

        db.close()
    finally:
        mailer.send = original

    database.engine.dispose()

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Alert emails: owners/editors notified once on remote_control plans only, viewers never")


if __name__ == "__main__":
    run()
