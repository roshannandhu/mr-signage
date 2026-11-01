import asyncio
import json
import logging
from typing import Dict, Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException
from jose import jwt, JWTError
from sqlalchemy.orm import Session

from .. import database, models
from ..routers.auth import ALGORITHM, get_secret_key
from ..database import get_redis

logger = logging.getLogger(__name__)

router = APIRouter()

def verify_device_token(device_id: str, token: str, db: Session) -> models.Screen:
    """Identity check for a screen's push socket.

    Kept deliberately identical to screens.verify_device_auth, including the staged legacy
    allowance -- this had the same hole, and a socket subscribed to `org:{id}` receives
    every fleet event for that tenant, so leaving it open while the REST side was closed
    would just move the leak.
    """
    from .screens import legacy_device_auth_allowed
    from ..services import token_is_for_device

    # Same archive check as screens.verify_device_auth. A socket subscribed to org:{id}
    # receives every fleet event for that tenant, so leaving it open to a removed screen
    # would keep streaming a customer's activity to a panel they had deliberately cut off.
    screen = (
        db.query(models.Screen)
        .filter(models.Screen.device_id == device_id, models.Screen.deleted_at.is_(None))
        .first()
    )
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    if not token:
        # Same staging rule as screens.verify_device_auth: gate on whether a credential was
        # presented, not on whether the screen has one, so a player built before this
        # release keeps working until the fleet has rotated.
        if not legacy_device_auth_allowed():
            raise HTTPException(status_code=401, detail="Authentication required")
        logger.warning(
            "Screen %s (device %s) opened a socket with no credential (legacy path)",
            screen.id, device_id,
        )
        screen.authenticated = False
        return screen

    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
        if not token_is_for_device(payload, device_id):
            raise HTTPException(status_code=401, detail="Token device mismatch")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # Mirrors the revocation check in screens.verify_device_auth, and for the same reason:
    # a socket subscribed to org:{id} receives every fleet event for that tenant, so a
    # revoked credential that still opened a socket would leak exactly what revoking it
    # was meant to stop.
    if screen.device_secret_hash is None:
        raise HTTPException(status_code=401, detail="Device credential has been revoked")

    screen.authenticated = True
    return screen


# Active in-memory websockets: channel -> set of WebSocket
ACTIVE_SUBSCRIPTIONS: dict[str, set[WebSocket]] = {}
SUBSCRIPTION_LOCK = asyncio.Lock()

async def register_ws(channel: str, ws: WebSocket):
    async with SUBSCRIPTION_LOCK:
        if channel not in ACTIVE_SUBSCRIPTIONS:
            ACTIVE_SUBSCRIPTIONS[channel] = set()
        ACTIVE_SUBSCRIPTIONS[channel].add(ws)

async def unregister_ws(channel: str, ws: WebSocket):
    async with SUBSCRIPTION_LOCK:
        if channel in ACTIVE_SUBSCRIPTIONS:
            ACTIVE_SUBSCRIPTIONS[channel].discard(ws)
            if not ACTIVE_SUBSCRIPTIONS[channel]:
                del ACTIVE_SUBSCRIPTIONS[channel]

async def broadcast_in_memory(channel: str, text: str):
    async with SUBSCRIPTION_LOCK:
        sockets = list(ACTIVE_SUBSCRIPTIONS.get(channel, []))
    for ws in sockets:
        try:
            await ws.send_text(text)
        except Exception:
            pass


async def broadcast_ws_event(channel: str, event_data: dict[str, Any]):
    """Broadcast an event to both in-memory sockets and Redis pubsub."""
    payload_str = json.dumps(event_data)
    await broadcast_in_memory(channel, payload_str)
    try:
        redis = database.get_redis()
        await redis.publish(channel, payload_str)
    except Exception:
        pass


def trigger_screen_sync(
    organization_id: int | None = None,
    screen_device_id: str | None = None,
    group_id: int | None = None,
):
    """Synchronous / Async friendly helper to push real-time sync signals to screens."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    event_payload = {"type": "sync", "command": "sync"}

    async def _dispatch():
        if screen_device_id:
            await broadcast_ws_event(f"screen:{screen_device_id}", event_payload)
        if group_id:
            await broadcast_ws_event(f"group:{group_id}", event_payload)
        if organization_id:
            await broadcast_ws_event(f"org:{organization_id}", event_payload)
            await broadcast_ws_event(f"dashboard:{organization_id}", {"type": "playlist_updated"})

    if loop and loop.is_running():
        loop.create_task(_dispatch())
    else:
        try:
            asyncio.run(_dispatch())
        except Exception as exc:
            logger.debug("Failed to dispatch sync via asyncio.run: %s", exc)


# The literal dashboard route must be declared before the parameterised device route
# below: Starlette matches in definition order, so "/{device_id}/ws" otherwise claims
# /dashboard/ws with device_id="dashboard", fails the device lookup, and rejects the
# handshake before the dashboard handler is ever reached.
@router.websocket("/dashboard/ws")
async def dashboard_websocket(
    websocket: WebSocket,
    token: str = Query(...),
    act_as: str | None = Query(default=None),
):
    """Live fleet events for one dashboard user.

    A browser WebSocket cannot send the X-Act-As-Org header the HTTP API uses, so a super
    admin who entered a tenant's workspace passes the same organisation id as a query
    parameter instead. Without this the socket always subscribed to the operator's OWN
    organisation's channel, so alert toasts and the live playlist/screen updates never
    arrived while acting inside a customer's workspace -- exactly when an operator most
    needs to see what is happening there.
    """
    from ..routers.auth import get_current_user_ws
    from ..tenancy import resolve_acting_organization

    db = database.SessionLocal()
    try:
        user = await get_current_user_ws(token, db)
        organization_id = resolve_acting_organization(user, act_as, db) or user.organization_id
        username = user.username
    except HTTPException as e:
        await websocket.close(code=1008, reason=e.detail)
        return
    finally:
        db.close()

    if not organization_id:
        await websocket.close(code=1008, reason="User has no organization")
        return

    await websocket.accept()
    
    channel = f"dashboard:{organization_id}"
    await register_ws(channel, websocket)
    logger.info(f"Dashboard WS connected for user {username}, subscribed to {channel}")

    async def writer():
        try:
            redis = database.get_redis()
            pubsub = redis.pubsub()
            await pubsub.subscribe(channel)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    await websocket.send_text(data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Redis pubsub disabled or disconnected for dashboard WS: {e}")
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

    writer_task = asyncio.create_task(writer())
    
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        writer_task.cancel()
        await unregister_ws(channel, websocket)
        logger.info(f"Dashboard WS disconnected for user {username}")


@router.websocket("/{device_id}/ws")
async def screen_websocket(
    websocket: WebSocket,
    device_id: str,
    token: str = Query(None),
):
    """Push channel for one screen. Held open for the life of the device."""
    db = database.SessionLocal()
    try:
        screen = verify_device_token(device_id, token, db)
        screen_id = screen.id
        organization_id = screen.organization_id
        group_id = screen.group_id
        authenticated = bool(getattr(screen, "authenticated", False))
    except HTTPException as e:
        await websocket.close(code=1008, reason=e.detail)
        return
    finally:
        db.close()

    await websocket.accept()
    
    channels = [
        f"screen:{device_id}",
    ]
    # Only a socket that presented a device token joins the workspace-wide and group channels.
    # A screen on the legacy (no credential) path is identified by a device id alone, which is
    # not a secret, so letting it subscribe to org:{id} streamed a tenant's whole fleet
    # activity to anyone who knew one of its device ids. It still receives its own commands.
    if authenticated:
        if organization_id:
            channels.append(f"org:{organization_id}")
        if group_id:
            channels.append(f"group:{group_id}")
        
    for ch in channels:
        await register_ws(ch, websocket)
    logger.info(f"Screen {device_id} WS connected, subscribed to {channels}")

    async def reader():
        try:
            while True:
                data = await websocket.receive_text()
                try:
                    msg = json.loads(data)
                    # Forward playback progress to dashboard channel if applicable
                    # Receive-only unless authenticated: this publishes into the tenant's
                    # dashboard, so it must not be open to a caller holding only a device id.
                    if msg.get("type") == "playback_progress" and organization_id and authenticated:
                        msg["device_id"] = device_id
                        msg["screen_id"] = screen_id
                        payload_str = json.dumps(msg)
                        await broadcast_in_memory(f"dashboard:{organization_id}", payload_str)
                        try:
                            redis = database.get_redis()
                            await redis.publish(f"dashboard:{organization_id}", payload_str)
                        except Exception:
                            pass
                except json.JSONDecodeError:
                    pass
        except WebSocketDisconnect:
            pass

    async def writer():
        try:
            redis = database.get_redis()
            pubsub = redis.pubsub()
            await pubsub.subscribe(*channels)
            async for message in pubsub.listen():
                if message["type"] == "message":
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")
                    await websocket.send_text(data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Redis pubsub disabled or disconnected for screen WS: {e}")
            try:
                while True:
                    await asyncio.sleep(3600)
            except asyncio.CancelledError:
                pass

    reader_task = asyncio.create_task(reader())
    writer_task = asyncio.create_task(writer())

    done, pending = await asyncio.wait(
        [reader_task, writer_task],
        return_when=asyncio.FIRST_COMPLETED
    )

    for task in pending:
        task.cancel()

    for ch in channels:
        await unregister_ws(ch, websocket)
    logger.info(f"Screen {device_id} WS disconnected")
