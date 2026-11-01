"""Admin audit trail: one append-only row per thing a platform operator did.

`record` only adds the row; the caller's own commit writes it, so an action and its audit
entry land together or not at all.
"""
import json
from typing import Any, Optional
from urllib.parse import unquote

from fastapi import Request
from sqlalchemy.orm import Session

from .. import models
from ..limiter import client_key

# The console's confirm dialog sends the operator's stated reason here (URI-encoded, since
# header values are latin-1), so no route needs a reason field in its body.
REASON_HEADER = "X-Admin-Reason"

_SECRET_KEYS = {
    "password", "hashed_password", "pin", "lock_pin", "maintenance_pin", "master_pin",
    "device_secret", "device_secret_hash", "token", "access_token", "secret",
}
REDACTED = "[redacted]"


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (REDACTED if str(key).lower() in _SECRET_KEYS and item is not None else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _dump(value: Any) -> Optional[str]:
    if value is None:
        return None
    return json.dumps(redact(value), default=str, sort_keys=True)


def reason_from(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    raw = request.headers.get(REASON_HEADER)
    if not raw:
        return None
    return unquote(raw).strip()[:500] or None


def record(
    db: Session,
    *,
    actor: models.User,
    action: str,
    request: Optional[Request] = None,
    target_org_id: Optional[int] = None,
    target_type: Optional[str] = None,
    target_id: Any = None,
    before: Any = None,
    after: Any = None,
    reason: Optional[str] = None,
    acting_org_id: Optional[int] = None,
    batch_id: Optional[str] = None,
) -> models.AdminAuditEvent:
    event = models.AdminAuditEvent(
        actor_user_id=actor.id,
        actor_username=actor.username,
        acting_org_id=acting_org_id,
        target_org_id=target_org_id,
        target_type=target_type,
        target_id=str(target_id) if target_id is not None else None,
        action=action,
        reason=reason or reason_from(request),
        before_json=_dump(before),
        after_json=_dump(after),
        ip=client_key(request)[:64] if request is not None else None,
        user_agent=(request.headers.get("user-agent") or "")[:300] or None if request is not None else None,
        batch_id=batch_id,
    )
    db.add(event)
    return event
