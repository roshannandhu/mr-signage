"""Phase 3.8: an offline TV holds more than one queued command, each for its own hold time.

Pins:
  * two distinct commands queued for the same offline screen both survive to the next sync
  * each command is consumed exactly once -- a second sync has nothing left
  * a command past its own hold time is dropped rather than delivered late
"""
import json
import os
import pathlib
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_path = pathlib.Path(tempfile.gettempdir()) / f"olrac-pendingcmds-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
os.environ.setdefault("SECRET_KEY", "pytest-pending-commands")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379")

from fastapi.testclient import TestClient  # noqa: E402

from backend.main import app  # noqa: E402
from backend import database, models  # noqa: E402
from backend.routers.auth import get_password_hash  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _test_plans import grant_plan  # noqa: E402

client = TestClient(app)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def run() -> None:
    db = database.SessionLocal()
    org = models.Organization(name="Pending Cmds Co", slug=f"pending-cmds-{uuid.uuid4().hex[:8]}", status="active")
    db.add(org)
    db.flush()
    grant_plan(db, org, remote_control=True)
    db.add(models.User(organization_id=org.id, username="pc-owner", email="pc-owner@x.t",
                       hashed_password=get_password_hash("x"), role="owner", is_active=True))
    device_id = f"pc-device-{uuid.uuid4().hex[:8]}"
    db.add(models.Screen(name="PC Screen", device_id=device_id, organization_id=org.id,
                         status="online", approved_at=models.utcnow(), last_seen=models.utcnow()))
    db.commit()
    org_id = org.id
    db.close()

    login = client.post("/api/auth/token", data={"username": "pc-owner@x.t", "password": "x"})
    check(login.status_code == 200, f"login failed: {login.status_code} {login.text}")
    auth = {"Authorization": f"Bearer {login.json()['access_token']}"}

    screen_id = client.get("/api/screens", headers=auth).json()[0]["id"]

    # --- two distinct commands queued while the screen is offline both survive --------------
    from backend.services.screen_telemetry_service import queue_device_command
    import asyncio
    btf = client.post(f"/api/screens/{screen_id}/bring-to-front", headers=auth)
    check(btf.status_code == 200, f"bring-to-front failed: {btf.status_code} {btf.text}")
    asyncio.run(queue_device_command(device_id, "check_update"))

    sync = client.get(f"/api/screens/{device_id}/sync")
    check(sync.status_code == 200, f"sync failed: {sync.status_code} {sync.text}")
    body = sync.json()
    delivered = set(body.get("pending_commands") or [])
    check(delivered == {"bring_to_front", "check_update"},
          f"expected both commands queued, got {delivered}")
    check(body.get("pending_command") in delivered, f"pending_command should be one of the queued ones, got {body.get('pending_command')!r}")

    # --- each command is consumed exactly once: nothing left on the next sync ---------------
    sync2 = client.get(f"/api/screens/{device_id}/sync")
    check(sync2.status_code in (200, 204), f"second sync failed unexpectedly: {sync2.status_code}")
    if sync2.status_code == 200:
        check(not (sync2.json().get("pending_commands") or []), "a command survived a second sync")

    # --- a command past its own hold time is dropped, not delivered late --------------------
    from backend.services.screen_telemetry_service import queue_device_command, pop_device_commands
    import asyncio

    async def _expired_then_fresh():
        await queue_device_command(device_id, "check_update", ttl_seconds=0)
        time.sleep(0.05)
        await queue_device_command(device_id, "bring_to_front", ttl_seconds=60)
        return await pop_device_commands(device_id)

    survivors = asyncio.run(_expired_then_fresh())
    check(survivors == ["bring_to_front"], f"an expired command should be dropped, got {survivors}")

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Pending commands: multiple distinct commands queue, survive one sync, expire past their hold")


if __name__ == "__main__":
    run()
