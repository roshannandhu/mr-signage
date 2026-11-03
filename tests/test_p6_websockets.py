"""P6 realtime checks: python tests/test_p6_websockets.py

Covers hierarchical group playlist resolution, emergency broadcast override, and the
WebSocket push channel — plus the rule that push is never the only path.
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Own throwaway Postgres database, created before backend is imported.
#
# This file previously used sqlite:///./test_p6.db, which both dropped a stray database
# file into the repo root and tested a different engine from the one production runs on —
# emergency/group resolution SQL that works in SQLite can still fail on Postgres.
import psycopg2  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

TEST_DB = "olrac_test_p6"
try:
    _admin = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    _admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    _admin.cursor().execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    _admin.cursor().execute(f"CREATE DATABASE {TEST_DB} OWNER olrac")
    _admin.close()
except Exception:
    pass

DATABASE_URL = f"postgresql://olrac:olrac_password@localhost:5432/{TEST_DB}"
os.environ["DATABASE_URL"] = DATABASE_URL
os.environ["REDIS_URL"] = "redis://localhost:6379/1"
os.environ["SECRET_KEY"] = "testsecret"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from backend.database import Base, get_db  # noqa: E402
from backend.main import app
from backend.models import User, Organization, Screen, ScreenGroup, Playlist, EmergencyBroadcast, Plan, utcnow
from backend.routers.auth import get_password_hash, create_access_token
from backend.routers.screens import verify_device_auth

engine = create_engine(DATABASE_URL)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def override_get_db():
    try:
        db = TestingSessionLocal()
        yield db
    finally:
        db.close()

app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)

def setup_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    
    # Emergency broadcast is behind a plan feature gate, so the org needs a plan that
    # grants it. Without one the broadcast 403s and this test fails on billing rather than
    # on the playlist resolution it is actually about -- the gate itself is covered by
    # test_plan_purchase.
    plan = Plan(
        name="Test Plan",
        slug="p6-test-plan",
        max_screens=10,
        max_storage_bytes=10 * 1024 ** 3,
        feature_flags_json='{"emergency_alert": true}',
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)

    org = Organization(name="Test Org", slug="test-org", plan_id=plan.id)
    db.add(org)
    db.commit()
    db.refresh(org)
    
    user = User(username="test_owner", hashed_password=get_password_hash("pwd"), role="owner", organization_id=org.id)
    db.add(user)
    db.commit()
    
    # Hierarchical Groups
    parent_group = ScreenGroup(name="Parent Group", organization_id=org.id)
    db.add(parent_group)
    db.commit()
    
    child_group = ScreenGroup(name="Child Group", organization_id=org.id, parent_id=parent_group.id)
    db.add(child_group)
    db.commit()
    
    # Playlists
    pl_parent = Playlist(name="Parent PL", organization_id=org.id)
    pl_child = Playlist(name="Child PL", organization_id=org.id)
    pl_screen = Playlist(name="Screen PL", organization_id=org.id)
    pl_emergency = Playlist(name="Emergency PL", organization_id=org.id)
    db.add_all([pl_parent, pl_child, pl_screen, pl_emergency])
    db.commit()
    
    parent_group.playlist_id = pl_parent.id
    db.commit()
    
    # Screen
    # Already live in the field; screens default to pending. See test_screen_approval.py.
    screen = Screen(name="Test Screen", organization_id=org.id, group_id=child_group.id, device_id="testdev", device_secret_hash="hash", approved_at=utcnow())
    db.add(screen)
    db.commit()
    db.refresh(screen)

    # Read every value out while the session is still open. Returning live ORM instances
    # and then closing the session raises DetachedInstanceError on first attribute access,
    # because each commit above expires the objects.
    from types import SimpleNamespace

    snapshot = (
        org.id,
        SimpleNamespace(id=screen.id, device_id=screen.device_id),
        SimpleNamespace(id=pl_parent.id),
        SimpleNamespace(id=pl_child.id),
        SimpleNamespace(id=pl_screen.id),
        SimpleNamespace(id=pl_emergency.id),
    )
    db.close()
    return snapshot

def check_hierarchical_playlist_resolution():
    org_id, screen, pl_parent, pl_child, pl_screen, pl_emergency = setup_db()
    
    # Issue a device token
    from jose import jwt
    device_token = jwt.encode({"sub": f"device:{screen.device_id}"}, os.getenv("SECRET_KEY"), algorithm="HS256")
    
    # 1. Screen has no direct playlist, child group has no playlist, parent group HAS playlist
    # Sync should return Parent PL
    response = client.get(f"/api/screens/{screen.device_id}/sync", headers={"Authorization": f"Bearer {device_token}"})
    assert response.status_code == 200
    assert response.json()["playlist"]["id"] == pl_parent.id
    
    # 2. Child group gets a playlist -> Should override Parent PL
    db = TestingSessionLocal()
    child = db.query(ScreenGroup).filter_by(name="Child Group").first()
    child.playlist_id = pl_child.id
    db.commit()
    db.close()
    
    response = client.get(f"/api/screens/{screen.device_id}/sync", headers={"Authorization": f"Bearer {device_token}"})
    assert response.json()["playlist"]["id"] == pl_child.id
    
    # 3. Emergency broadcast (org-wide)
    user_token = create_access_token({"sub": "test_owner"})
    resp_emerg = client.post("/api/emergency/broadcast", json={
        "target_type": "all",
        "playlist_id": pl_emergency.id
    }, headers={"Authorization": f"Bearer {user_token}"})
    assert resp_emerg.status_code == 200
    
    # Sync should now return Emergency PL
    response = client.get(f"/api/screens/{screen.device_id}/sync", headers={"Authorization": f"Bearer {device_token}"})
    assert response.json()["playlist"]["id"] == pl_emergency.id
    
def check_websocket_pubsub():
    org_id, screen, pl_parent, pl_child, pl_screen, pl_emergency = setup_db()
    
    # Issue a device token
    from jose import jwt
    device_token = jwt.encode({"sub": f"device:{screen.device_id}"}, os.getenv("SECRET_KEY"), algorithm="HS256")
    
    user_token = create_access_token({"sub": "test_owner"})
    
    # Since fastapi TestClient doesn't fully run background tasks or full async loops perfectly with pubsub,
    # we can just test the WebSocket connection accepts the token.
    with client.websocket_connect(f"/api/ws/{screen.device_id}/ws?token={device_token}") as websocket:
        # Trigger an emergency broadcast
        resp_emerg = client.post("/api/emergency/broadcast", json={
            "target_type": "all",
            "playlist_id": pl_emergency.id
        }, headers={"Authorization": f"Bearer {user_token}"})
        assert resp_emerg.status_code == 200
        
        # We should receive the messages on WS (both sync push and emergency_override arrive on org:{id})
        received = []
        for _ in range(2):
            msg = json.loads(websocket.receive_text())
            received.append(msg)
            if msg.get("type") == "emergency_override":
                break
        emergency_msg = next((m for m in received if m.get("type") == "emergency_override"), None)
        assert emergency_msg is not None, f"Expected emergency_override in {received}"
        assert emergency_msg["playlist_id"] == pl_emergency.id


def check_dashboard_websocket_is_not_shadowed_by_device_route():
    """/api/ws/dashboard/ws must reach the dashboard handler, not the device one.

    Both live on the same router and Starlette matches in definition order, so with
    "/{device_id}/ws" declared first it claimed this path with device_id="dashboard",
    failed the screen lookup, and rejected the handshake. Every dashboard connection
    died that way while this file only ever exercised the device route.
    """
    setup_db()
    user_token = create_access_token({"sub": "test_owner"})

    # websocket_connect raises if the handshake is refused, so completing it is the assertion.
    with client.websocket_connect(f"/api/ws/dashboard/ws?token={user_token}"):
        pass

    rejected = False
    try:
        with client.websocket_connect("/api/ws/dashboard/ws?token=not-a-real-token"):
            pass
    except Exception:
        rejected = True
    assert rejected, "the dashboard socket must refuse an invalid token"


def check_polling_still_converges_without_websocket():
    """Push must never be the only path.

    The spec is explicit: if the socket is down the TV must still converge via the
    ordinary poll. This exercises the same emergency change with no WebSocket connected
    at all, so a regression that makes delivery depend on the socket is caught here.
    """
    org_id, screen, pl_parent, pl_child, pl_screen, pl_emergency = setup_db()

    from jose import jwt
    device_token = jwt.encode(
        {"sub": f"device:{screen.device_id}"}, os.getenv("SECRET_KEY"), algorithm="HS256"
    )
    user_token = create_access_token({"sub": "test_owner"})

    broadcast = client.post(
        "/api/emergency/broadcast",
        json={"target_type": "all", "playlist_id": pl_emergency.id},
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert broadcast.status_code == 200, broadcast.text

    # No websocket_connect anywhere in this test — plain sync only.
    response = client.get(
        f"/api/screens/{screen.device_id}/sync",
        headers={"Authorization": f"Bearer {device_token}"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["playlist"]["id"] == pl_emergency.id, (
        "emergency did not reach the device through polling — push must not be the only path"
    )


def _redis_available() -> bool:
    try:
        import redis

        redis.Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=2).ping()
        return True
    except Exception:
        return False


def run():
    check_hierarchical_playlist_resolution()
    print("  hierarchical group resolution + emergency override: OK")

    check_polling_still_converges_without_websocket()
    print("  emergency reaches device via polling with no socket: OK")

    if _redis_available():
        check_dashboard_websocket_is_not_shadowed_by_device_route()
        print("  dashboard websocket route resolves: OK")
    else:
        print("  dashboard websocket route: SKIPPED (Redis not reachable)")

    # The push test needs Redis pub/sub. The suite has to stay green with Redis stopped,
    # so report a skip rather than failing on an infrastructure absence.
    if _redis_available():
        check_websocket_pubsub()
        print("  websocket push delivery: OK")
    else:
        print("  websocket push delivery: SKIPPED (Redis not reachable)")

    print("P6 realtime checks passed")


# No test_* wrapper is collected by pytest for this file — conftest.py runs it as a
# subprocess so it owns its database. Without this block the script would import, define
# functions, call none of them, exit 0, and be reported as a pass.
if __name__ == "__main__":
    run()
