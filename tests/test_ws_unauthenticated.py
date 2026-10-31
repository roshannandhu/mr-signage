"""A device socket opened with no credential is receive-only and sees only its own commands.

A screen is identified by its device id, which is not a secret. A socket opened with only that
used to subscribe to org:{id} -- every fleet event for the tenant -- and to the group channel,
and could publish playback_progress into the tenant's dashboard channel. Now only a socket that
presented a device token joins the wider channels or publishes. Pins:
  * no token: subscribed to screen:<id> only
  * with a token: also org:<id> (and group:<id> when the screen is in one)
  * no token: playback_progress is not forwarded to the dashboard
  * with a token: it still is
"""
import os
import pathlib
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-wsauth-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-ws-unauthenticated")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")
os.environ.pop("ALLOW_LEGACY_DEVICE_AUTH", None)

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers import websockets  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


class FakeRedis:
    """Instant and inert: this test is about who may join and publish, not about delivery."""

    def __getattr__(self, _name):
        async def _noop(*_args, **_kwargs):
            return None

        return _noop


def wait_for(predicate, seconds=3.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def subscribed_to(channel: str) -> bool:
    return bool(websockets.ACTIVE_SUBSCRIPTIONS.get(channel))


def run() -> None:
    database.get_redis = lambda: FakeRedis()
    published: list[str] = []

    async def record(channel, payload):
        published.append(channel)

    websockets.broadcast_in_memory = record

    db = database.SessionLocal()
    org = models.Organization(name="Venue", slug=f"v-{uuid.uuid4().hex[:8]}", status="active")
    db.add(org)
    db.flush()
    group = models.ScreenGroup(organization_id=org.id, name="Lobby")
    db.add(group)
    db.flush()
    screen = models.Screen(organization_id=org.id, group_id=group.id, device_id="tv-w", status="online")
    db.add(screen)
    db.flush()
    from backend.services import issue_device_secret
    secret = issue_device_secret(screen)
    org_id, group_id = org.id, group.id
    db.commit()
    db.close()

    client = TestClient(app)
    progress = '{"type": "playback_progress", "item_id": 1}'

    # --- no credential -----------------------------------------------------------------------
    with client.websocket_connect("/api/ws/tv-w/ws") as socket:
        check(wait_for(lambda: subscribed_to("screen:tv-w")), "an unauthenticated socket did not get its own channel")
        check(not subscribed_to(f"org:{org_id}"), "SECURITY: an unauthenticated socket joined the workspace channel")
        check(not subscribed_to(f"group:{group_id}"), "SECURITY: an unauthenticated socket joined the group channel")
        socket.send_text(progress)
        time.sleep(0.4)
        check(not published, f"SECURITY: an unauthenticated socket published to {published}")

    # --- with the device's own token -------------------------------------------------------------
    token = client.post("/api/screens/auth", json={"device_id": "tv-w", "device_secret": secret}).json()["access_token"]
    with client.websocket_connect(f"/api/ws/tv-w/ws?token={token}") as socket:
        check(wait_for(lambda: subscribed_to(f"org:{org_id}")), "an authenticated socket did not join the workspace channel")
        check(subscribed_to(f"group:{group_id}"), "an authenticated socket did not join its group channel")
        socket.send_text(progress)
        check(wait_for(lambda: f"dashboard:{org_id}" in published),
              "an authenticated socket's playback progress was not forwarded to the dashboard")


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
    print("OK - a socket with no credential is receive-only and sees only its own commands")
