"""Device Authentication Application Service for Android TV hardware devices."""

import logging
import os
import secrets as _secrets
from typing import Optional
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from redis.exceptions import ResponseError
from sqlalchemy.orm import Session

from .base import BaseService
from ..repositories.screen_repo import ScreenRepository
from .. import models, database

logger = logging.getLogger(__name__)


def _pending_secret_key(device_id: str) -> str:
    return f"screen_pending_secret:{device_id}"


async def park_device_secret(device_id: str, secret: str, ttl_seconds: int = 300) -> None:
    """Temporarily park a secret in Redis for a newly paired device to collect."""
    if not device_id:
        return
    try:
        await database.get_redis().setex(_pending_secret_key(device_id), ttl_seconds, secret)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not park device secret for %s: %s", device_id, exc)


async def collect_device_secret(device_id: str) -> Optional[str]:
    """Take the credential waiting for this device, if any. Reading it consumes it."""
    if not device_id:
        return None
    try:
        redis = database.get_redis()
        key = _pending_secret_key(device_id)
        try:
            # One step, so two simultaneous callers cannot both read it before either
            # deletes it. GETDEL needs Redis 6.2; an older server falls back to two steps.
            value = await redis.getdel(key)
        except ResponseError:
            value = await redis.get(key)
            if value is not None:
                await redis.delete(key)
        if value is None:
            return None
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read pending device secret for %s: %s", device_id, exc)
        return None


def legacy_device_auth_allowed() -> bool:
    """Whether a screen holding no device secret may still call the device endpoints.

    ⚠️ Defaults to ON, which is the wrong posture: a deployment that never sets the
    variable -- a new environment, a restored one, a developer's laptop -- accepts
    unauthenticated device calls from anyone who knows or guesses a device id. A security
    allowance should be switched on deliberately rather than inherited by forgetting.

    It has NOT been flipped yet because the blast radius is a live fleet: any panel still
    holding no device secret stops syncing the moment it changes, and over ten test files
    drive the device endpoints with no credential. Flip it by setting
    ALLOW_LEGACY_DEVICE_AUTH=false once no screen logs the legacy-path warning -- the
    warning below is what tells you that.
    """
    return os.getenv("ALLOW_LEGACY_DEVICE_AUTH", "true").strip().lower() in {"1", "true", "yes"}


# The `typ` claim a screen's token carries. The server signs several kinds of token with one
# key, and a device check that looked only at `sub` accepted all of them: the public Google
# sign-in routes mint tokens whose subject is `device:<id>` for anyone who asks, and one of
# those, used as a Bearer on /sync, returned that screen's maintenance PIN and the operator's
# master PIN.
DEVICE_TOKEN_TYPE = "device"


def require_device_token_type() -> bool:
    """Whether a token with no `typ` is refused too.

    Off until the fleet runs a build that re-mints on 401: today's player treats a rejected
    heartbeat as "this screen was removed" and wipes itself, so refusing untyped tokens
    early would sign out every TV holding a token minted before the deploy. Flip it with
    REQUIRE_DEVICE_TOKEN_TYPE=true once they have all rotated (tokens last an hour).
    """
    return os.getenv("REQUIRE_DEVICE_TOKEN_TYPE", "").strip().lower() in {"1", "true", "yes"}


def token_is_for_device(payload: dict, device_id: str) -> bool:
    """Whether decoded token claims are a screen's own token for this device.

    Tokens minted for something else -- the Google sign-in poll and OAuth-state tokens, and
    ordinary web logins -- carry a different `typ` (or, for logins, none) and are refused.
    """
    if payload.get("sub") != f"device:{device_id}":
        return False
    token_type = payload.get("typ")
    if token_type is None:
        return not require_device_token_type()
    return token_type == DEVICE_TOKEN_TYPE


def issue_device_secret(screen: models.Screen) -> str:
    """Give this screen a credential of its own and return the plaintext once."""
    from ..routers.auth import get_password_hash

    device_secret = _secrets.token_hex(32)
    screen.device_secret_hash = get_password_hash(device_secret)
    return device_secret


def verify_device_auth(
    device_id: str, credentials: Optional[HTTPAuthorizationCredentials], db: Session
) -> models.Screen:
    """Verify hardware authentication for device endpoints."""
    screen = (
        db.query(models.Screen)
        .filter(models.Screen.device_id == device_id, models.Screen.deleted_at.is_(None))
        .first()
    )
    if not screen:
        raise HTTPException(status_code=404, detail="Screen not found")

    if not credentials:
        if not legacy_device_auth_allowed():
            raise HTTPException(status_code=401, detail="Authentication required")
        logger.warning(
            "Screen %s (device %s) authenticated with no credential (legacy path)",
            screen.id,
            device_id,
        )
        screen.authenticated = False
        return screen

    try:
        from ..routers.auth import ALGORITHM, get_secret_key
        from jose import jwt, JWTError

        payload = jwt.decode(credentials.credentials, get_secret_key(), algorithms=[ALGORITHM])
        if not token_is_for_device(payload, device_id):
            raise HTTPException(status_code=401, detail="Token device mismatch")
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")

    if screen.device_secret_hash is None:
        raise HTTPException(status_code=401, detail="Device credential has been revoked")

    screen.authenticated = True
    return screen


class DeviceAuthService(BaseService):
    """Application Service for TV hardware device authentication."""

    def __init__(self, db: Session, repo: Optional[ScreenRepository] = None):
        super().__init__(db)
        self.repo = repo or ScreenRepository(db)

    def verify_auth(
        self, device_id: str, credentials: Optional[HTTPAuthorizationCredentials]
    ) -> models.Screen:
        return verify_device_auth(device_id, credentials, self.db)

    def issue_secret(self, screen: models.Screen) -> str:
        return issue_device_secret(screen)
