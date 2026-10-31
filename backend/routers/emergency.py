import json
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import database, models, schemas
from ..tenancy import TenantScope, require_feature, require_tenant_roles

router = APIRouter()

class BroadcastRequest(BaseModel):
    target_type: str # 'all', 'group', 'screen'
    target_id: int | None = None
    playlist_id: int


def _make_next_sync_fresh(db: Session, organization_id: int, req: BroadcastRequest) -> str | None:
    """Make the affected screens' next sync a full one, and return a target device id if any.

    A screen asks "anything since X?" and is told 204 unless its marker -- the newest of its
    assignment time, its playlist, its group and the newest app release -- has moved past X.
    Starting or cancelling an emergency broadcast changed none of them: it only writes an
    EmergencyBroadcast row. So a screen that had settled kept answering 204 and never heard
    that it should switch (or switch back), until something unrelated happened to move its
    marker.

    A group or org-wide broadcast bumps every live screen in the workspace rather than
    working out which are descended from the group: a redundant sync of an unaffected screen
    returns the same playlist, and the bump is a single UPDATE.
    """
    screens = db.query(models.Screen).filter(
        models.Screen.organization_id == organization_id,
        models.Screen.deleted_at.is_(None),
    )
    device_id = None
    if req.target_type == "screen":
        screens = screens.filter(models.Screen.id == req.target_id)
        target = screens.first()
        device_id = target.device_id if target else None
    screens.update({models.Screen.assignment_updated_at: models.utcnow()}, synchronize_session=False)
    return device_id


def _push_sync(organization_id: int, req: BroadcastRequest, device_id: str | None) -> None:
    """Tell connected screens to sync now, with the message the player already handles."""
    from .websockets import trigger_screen_sync

    if req.target_type == "screen":
        if device_id:
            trigger_screen_sync(screen_device_id=device_id)
    else:
        # A group-targeted broadcast reaches descendant groups too, and a screen listens only to
        # its own group's channel, so the whole workspace is told.
        trigger_screen_sync(organization_id=organization_id)


@router.get("/active")
def get_active_broadcasts(
    tenant: TenantScope = Depends(require_tenant_roles("owner", "editor", "viewer")),
    db: Session = Depends(database.get_db)
):
    broadcasts = db.query(models.EmergencyBroadcast).filter(
        models.EmergencyBroadcast.organization_id == tenant.organization_id,
        models.EmergencyBroadcast.is_active == True
    ).all()
    return [
        {
            "id": b.id,
            "target_type": b.target_type,
            "target_id": b.target_id,
            "playlist_id": b.playlist_id,
            "updated_at": b.updated_at
        } for b in broadcasts
    ]

@router.post("/broadcast")
async def trigger_emergency_broadcast(
    req: BroadcastRequest,
    tenant: TenantScope = Depends(require_tenant_roles("owner", "editor")),
    _feature: TenantScope = Depends(require_feature("emergency_alert")),
    db: Session = Depends(database.get_db)
):
    if req.target_type not in ("all", "group", "screen"):
        raise HTTPException(status_code=400, detail="Invalid target_type")

    broadcast = db.query(models.EmergencyBroadcast).filter(
        models.EmergencyBroadcast.organization_id == tenant.organization_id,
        models.EmergencyBroadcast.target_type == req.target_type,
        models.EmergencyBroadcast.target_id == req.target_id,
        models.EmergencyBroadcast.is_active == True
    ).first()

    if broadcast:
        broadcast.playlist_id = req.playlist_id
        broadcast.updated_at = models.utcnow()
    else:
        broadcast = models.EmergencyBroadcast(
            organization_id=tenant.organization_id,
            target_type=req.target_type,
            target_id=req.target_id,
            playlist_id=req.playlist_id,
            is_active=True
        )
        db.add(broadcast)

    device_id = _make_next_sync_fresh(db, tenant.organization_id, req)
    db.commit()

    # Publish to Redis
    try:
        redis = database.get_redis()
        msg = {"type": "emergency_override", "playlist_id": req.playlist_id}
        if req.target_type == "all":
            await redis.publish(f"org:{tenant.organization_id}", json.dumps(msg))
        elif req.target_type == "group":
            await redis.publish(f"group:{req.target_id}", json.dumps(msg))
        elif req.target_type == "screen" and device_id:
            # The channel a screen listens on is its DEVICE id. This used the screen's integer
            # id, which no socket subscribes to, so a screen-targeted broadcast was never pushed.
            await redis.publish(f"screen:{device_id}", json.dumps(msg))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to publish to redis: {e}")

    _push_sync(tenant.organization_id, req, device_id)

    return {"status": "ok", "message": "Broadcast triggered"}

@router.post("/cancel")
async def cancel_emergency_broadcast(
    req: BroadcastRequest,
    tenant: TenantScope = Depends(require_tenant_roles("owner", "editor")),
    _feature: TenantScope = Depends(require_feature("emergency_alert")),
    db: Session = Depends(database.get_db)
):
    broadcast = db.query(models.EmergencyBroadcast).filter(
        models.EmergencyBroadcast.organization_id == tenant.organization_id,
        models.EmergencyBroadcast.target_type == req.target_type,
        models.EmergencyBroadcast.target_id == req.target_id,
        models.EmergencyBroadcast.is_active == True
    ).first()

    device_id = None
    if broadcast:
        broadcast.is_active = False
        device_id = _make_next_sync_fresh(db, tenant.organization_id, req)
        db.commit()
    elif req.target_type == "screen":
        screen = db.query(models.Screen).filter(
            models.Screen.id == req.target_id,
            models.Screen.organization_id == tenant.organization_id,
        ).first()
        device_id = screen.device_id if screen else None

    # Publish to Redis to tell TVs to reload normal playlist
    try:
        redis = database.get_redis()
        msg = {"type": "emergency_cancel"}
        if req.target_type == "all":
            await redis.publish(f"org:{tenant.organization_id}", json.dumps(msg))
        elif req.target_type == "group":
            await redis.publish(f"group:{req.target_id}", json.dumps(msg))
        elif req.target_type == "screen" and device_id:
            await redis.publish(f"screen:{device_id}", json.dumps(msg))
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to publish to redis: {e}")

    if broadcast:
        _push_sync(tenant.organization_id, req, device_id)

    return {"status": "ok", "message": "Broadcast cancelled"}
