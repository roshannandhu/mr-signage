import asyncio
import json
import logging
from datetime import timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from . import database, models

logger = logging.getLogger(__name__)

MAX_OUTBOX_RETRIES = 5
BASE_RETRY_DELAY_SECONDS = 2


def queue_outbox_event(
    db: Session,
    channel: str,
    event_type: str,
    payload: Dict[str, Any],
) -> models.OutboxEvent:
    """Queue an outbox event in the same ACID transaction as primary table updates."""
    event = models.OutboxEvent(
        channel=channel,
        event_type=event_type,
        payload_json=json.dumps(payload),
        status="PENDING",
        retry_count=0,
        next_retry_at=models.utcnow(),
        created_at=models.utcnow(),
    )
    db.add(event)
    return event


async def dispatch_outbox_event(event_id: int) -> bool:
    """Immediate post-commit asynchronous dispatch to in-memory sockets and Redis."""
    from .routers.websockets import broadcast_ws_event

    db = database.SessionLocal()
    try:
        event = db.query(models.OutboxEvent).filter(models.OutboxEvent.id == event_id).first()
        if not event or event.status == "DELIVERED":
            return False

        try:
            payload = json.loads(event.payload_json)
        except Exception:
            payload = {"raw": event.payload_json}

        await broadcast_ws_event(event.channel, {
            "type": event.event_type,
            "outbox_id": event.id,
            "channel": event.channel,
            **payload,
        })

        event.status = "DELIVERED"
        event.processed_at = models.utcnow()
        db.commit()
        return True
    except Exception as exc:
        logger.warning("Immediate outbox dispatch failed for event %s: %s", event_id, exc)
        try:
            if event:
                event.retry_count += 1
                delay = min(60, BASE_RETRY_DELAY_SECONDS ** event.retry_count)
                event.next_retry_at = models.utcnow() + timedelta(seconds=delay)
                event.last_error = str(exc)
                if event.retry_count >= MAX_OUTBOX_RETRIES:
                    event.status = "DEAD_LETTER"
                db.commit()
        except Exception:
            pass
        return False
    finally:
        db.close()


def schedule_immediate_outbox_dispatch(event_id: int) -> None:
    """Safely schedule an asyncio task for zero-latency post-commit dispatch."""
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            loop.create_task(dispatch_outbox_event(event_id))
    except RuntimeError:
        pass


def process_outbox_batch(db: Session, batch_size: int = 50) -> int:
    """Worker loop pass querying pending outbox events using SKIP LOCKED."""
    now = models.utcnow()
    query = (
        db.query(models.OutboxEvent)
        .filter(models.OutboxEvent.status == "PENDING", models.OutboxEvent.next_retry_at <= now)
        .order_by(models.OutboxEvent.id.asc())
        .limit(batch_size)
    )

    # Use FOR UPDATE SKIP LOCKED if supported (PostgreSQL)
    try:
        events = query.with_for_update(skip_locked=True).all()
    except Exception:
        # Fallback for SQLite in local test suites
        events = query.all()

    if not events:
        return 0

    processed = 0
    from .routers.websockets import broadcast_ws_event

    for event in events:
        try:
            payload = json.loads(event.payload_json)
            # Synchronous wrapper to broadcast if running in loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(broadcast_ws_event(event.channel, {
                        "type": event.event_type,
                        "outbox_id": event.id,
                        "channel": event.channel,
                        **payload,
                    }))
                else:
                    asyncio.run(broadcast_ws_event(event.channel, {
                        "type": event.event_type,
                        "outbox_id": event.id,
                        "channel": event.channel,
                        **payload,
                    }))
            except Exception as e:
                logger.debug("Broadcast in process_outbox_batch: %s", e)

            event.status = "DELIVERED"
            event.processed_at = models.utcnow()
            processed += 1
        except Exception as exc:
            event.retry_count += 1
            delay = min(60, BASE_RETRY_DELAY_SECONDS ** event.retry_count)
            event.next_retry_at = models.utcnow() + timedelta(seconds=delay)
            event.last_error = str(exc)
            if event.retry_count >= MAX_OUTBOX_RETRIES:
                event.status = "DEAD_LETTER"

    db.commit()
    return processed
