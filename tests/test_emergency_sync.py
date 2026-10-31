"""Starting or cancelling an emergency broadcast must reach screens that have already settled.

A screen asks "anything since X?" and is answered 204 unless its sync marker moved. Starting or
cancelling a broadcast wrote an EmergencyBroadcast row and changed no marker, so a settled
screen never heard about it. It was also pushed on `screen:<integer id>`, a channel no socket
listens on (sockets listen on the device id). This pins:
  * after a broadcast, a screen that was up to date gets a fresh sync carrying the emergency playlist
  * after a cancel, the same screen gets a fresh sync carrying its normal playlist again
  * a screen-targeted broadcast bumps only that screen and is pushed on its DEVICE channel
"""
import json
import os
import pathlib
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-emsync-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-emergency-sync")
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


class FakeRedis:
    """Records what is published, and is otherwise inert and instant."""

    def __init__(self):
        self.published: list[tuple[str, dict]] = []

    async def publish(self, channel, payload):
        self.published.append((channel, json.loads(payload)))

    def __getattr__(self, _name):
        async def _noop(*_args, **_kwargs):
            return None

        return _noop


REDIS = FakeRedis()


def setup():
    db = database.SessionLocal()
    plan = models.Plan(name="Business", slug=f"biz-{uuid.uuid4().hex[:6]}",
                       feature_flags_json=json.dumps({"emergency_alert": True}), is_active=True,
                       max_storage_bytes=10 * 1024 ** 3)
    db.add(plan)
    db.flush()
    org = models.Organization(name="Venue", slug=f"v-{uuid.uuid4().hex[:8]}", status="active", plan_id=plan.id)
    db.add(org)
    db.flush()
    db.add(models.User(organization_id=org.id, username="owner", email="ow@x.t",
                       hashed_password=get_password_hash("x"), role="owner", is_active=True))
    normal = models.Playlist(organization_id=org.id, name="Normal")
    emergency = models.Playlist(organization_id=org.id, name="EMERGENCY")
    db.add_all([normal, emergency])
    db.flush()
    first = models.Screen(organization_id=org.id, device_id="tv-1", status="online", playlist_id=normal.id)
    second = models.Screen(organization_id=org.id, device_id="tv-2", status="online", playlist_id=normal.id)
    db.add_all([first, second])
    db.flush()
    from backend.services import issue_device_secret
    secrets_by_device = {"tv-1": issue_device_secret(first), "tv-2": issue_device_secret(second)}
    ids = {"normal": normal.id, "emergency": emergency.id, "tv-2": second.id}
    db.commit()
    db.close()
    return secrets_by_device, ids


def run() -> None:
    database.get_redis = lambda: REDIS
    secrets_by_device, ids = setup()
    client = TestClient(app)
    owner = {"Authorization": f"Bearer {create_access_token({'sub': 'owner'})}"}

    def device_headers(device_id):
        token = client.post("/api/screens/auth", json={
            "device_id": device_id, "device_secret": secrets_by_device[device_id]}).json()["access_token"]
        return {"Authorization": f"Bearer {token}"}

    headers = {device: device_headers(device) for device in ("tv-1", "tv-2")}

    def sync(device, since=None):
        params = {"since": since} if since else {}
        return client.get(f"/api/screens/{device}/sync", headers=headers[device], params=params)

    def settled(device):
        """A screen that is fully up to date: returns its marker."""
        first = sync(device)
        check(first.status_code == 200, f"{device} first sync failed: {first.text[:100]}")
        marker = first.json()["playlist_updated_at"]
        check(sync(device, marker).status_code == 204, f"{device} should be settled (204) before the broadcast")
        return marker

    marker = settled("tv-1")
    marker2 = settled("tv-2")

    # Sub-second timestamps: make sure the bump lands strictly after the marker we hold.
    time.sleep(0.05)

    # --- org-wide broadcast -------------------------------------------------------------------
    started = client.post("/api/emergency/broadcast", headers=owner,
                          json={"target_type": "all", "playlist_id": ids["emergency"]})
    check(started.status_code == 200, f"broadcast failed: {started.text}")
    heard = sync("tv-1", marker)
    check(heard.status_code == 200, f"a settled screen was told 204 after a broadcast ({heard.status_code})")
    if heard.status_code == 200:
        check(heard.json()["playlist"]["id"] == ids["emergency"], "the settled screen did not get the emergency playlist")
        marker_after = heard.json()["playlist_updated_at"]
        check(sync("tv-1", marker_after).status_code == 204, "the screen should settle again after hearing it")
    else:
        marker_after = marker

    time.sleep(0.05)

    # --- cancelling it ------------------------------------------------------------------------
    cancelled = client.post("/api/emergency/cancel", headers=owner,
                            json={"target_type": "all", "playlist_id": ids["emergency"]})
    check(cancelled.status_code == 200, f"cancel failed: {cancelled.text}")
    back = sync("tv-1", marker_after)
    check(back.status_code == 200, f"a screen on the emergency playlist was told 204 after the cancel ({back.status_code})")
    if back.status_code == 200:
        check(back.json()["playlist"]["id"] == ids["normal"], "the screen did not return to its normal playlist")

    # --- a broadcast to one screen ----------------------------------------------------------------
    marker1 = settled("tv-1")
    marker2 = settled("tv-2")
    time.sleep(0.05)
    REDIS.published.clear()
    one = client.post("/api/emergency/broadcast", headers=owner,
                      json={"target_type": "screen", "target_id": ids["tv-2"], "playlist_id": ids["emergency"]})
    check(one.status_code == 200, f"screen-targeted broadcast failed: {one.text}")
    check(sync("tv-1", marker1).status_code == 204, "an untargeted screen was woken by another screen's broadcast")
    targeted = sync("tv-2", marker2)
    check(targeted.status_code == 200 and targeted.json()["playlist"]["id"] == ids["emergency"],
          "the targeted screen did not get the emergency playlist")
    channels = {channel for channel, _msg in REDIS.published}
    check("screen:tv-2" in channels, f"the broadcast was not pushed on the screen's device channel: {channels}")
    check(f"screen:{ids['tv-2']}" not in channels, "the broadcast was pushed on a channel nothing listens to")


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
    print("OK - starting and cancelling an emergency reaches screens that had already settled")
