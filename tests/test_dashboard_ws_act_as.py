"""The dashboard websocket follows the workspace a platform operator stepped into, not
their own organisation.

A browser WebSocket cannot send the X-Act-As-Org header the HTTP API uses, so the operator
console passes the same organisation id as an `act_as` query parameter. Before this the
socket read only user.organization_id, so a super_admin who entered a tenant's workspace
still subscribed to their OWN org's dashboard channel -- alert toasts and live screen/
playlist updates silently never arrived while acting inside a customer's workspace, exactly
when an operator most needs to see what is happening there. Pins:
  * a super_admin with no act_as subscribes to their own organisation's channel
  * a super_admin with act_as=<tenant> subscribes to THAT tenant's channel instead
  * a non-super-admin's act_as is ignored outright, same as the HTTP header
  * act_as naming an organisation that does not exist closes the handshake rather than
    silently falling back to the operator's own channel
"""
import os
import pathlib
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

_db_file = pathlib.Path(tempfile.gettempdir()) / f"olrac-wsactas-{uuid.uuid4().hex}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file}"
os.environ.setdefault("SECRET_KEY", "pytest-ws-act-as")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers import websockets  # noqa: E402
from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


class FakeRedis:
    """Instant and inert: this test is about which channel is joined, not about delivery."""

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

    db = database.SessionLocal()
    operator_org = models.Organization(name="Platform", slug=f"platform-{uuid.uuid4().hex[:8]}", status="active")
    tenant_org = models.Organization(name="Tenant", slug=f"tenant-{uuid.uuid4().hex[:8]}", status="active")
    db.add_all([operator_org, tenant_org])
    db.flush()
    admin = models.User(organization_id=operator_org.id, username="ws-admin", email="ws-admin@x.t",
                        hashed_password=get_password_hash("x"), role="super_admin", is_active=True)
    tenant_owner = models.User(organization_id=tenant_org.id, username="ws-owner", email="ws-owner@x.t",
                               hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add_all([admin, tenant_owner])
    db.commit()
    operator_org_id, tenant_org_id = operator_org.id, tenant_org.id
    db.close()

    admin_token = create_access_token({"sub": "ws-admin"})
    owner_token = create_access_token({"sub": "ws-owner"})
    client = TestClient(app)

    # --- a super_admin with no act_as gets their OWN org's channel --------------------------
    with client.websocket_connect(f"/api/ws/dashboard/ws?token={admin_token}"):
        check(wait_for(lambda: subscribed_to(f"dashboard:{operator_org_id}")),
              "a super_admin with no act_as should subscribe to their own org's channel")

    # --- a super_admin WITH act_as gets the TENANT's channel instead -------------------------
    with client.websocket_connect(f"/api/ws/dashboard/ws?token={admin_token}&act_as={tenant_org_id}"):
        check(wait_for(lambda: subscribed_to(f"dashboard:{tenant_org_id}")),
              "a super_admin acting inside a workspace should subscribe to THAT workspace's channel")

    # --- a non-super-admin's act_as is ignored outright --------------------------------------
    other_org = models.Organization(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}", status="active")
    db = database.SessionLocal()
    db.add(other_org)
    db.commit()
    other_org_id = other_org.id
    db.close()
    with client.websocket_connect(f"/api/ws/dashboard/ws?token={owner_token}&act_as={other_org_id}"):
        check(wait_for(lambda: subscribed_to(f"dashboard:{tenant_org_id}")),
              "a tenant's own act_as query param should be ignored, same as the HTTP header")
        check(not subscribed_to(f"dashboard:{other_org_id}"),
              f"SECURITY: a tenant should never be able to subscribe to another org's channel via act_as")

    # --- act_as naming an organisation that does not exist refuses the handshake -------------
    try:
        with client.websocket_connect(f"/api/ws/dashboard/ws?token={admin_token}&act_as=999999"):
            check(False, "act_as for a non-existent organisation should close the handshake, not connect")
    except Exception:
        pass  # starlette's test client raises when the server closes during the handshake

    if FAILURES:
        print("FAILED:")
        for failure in FAILURES:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("Dashboard WS: act_as follows the operator into a workspace, ignored for a tenant, invalid ids refused")


if __name__ == "__main__":
    try:
        run()
    finally:
        database.engine.dispose()
        _db_file.unlink(missing_ok=True)
