"""Screen Telemetry, Versioning & Remote Command Management Service."""

import logging
import os
import time
from typing import Optional, Dict, List
from sqlalchemy.orm import Session

from .base import BaseService
from ..repositories.screen_repo import ScreenRepository
from .. import models, schemas, database, rollout

logger = logging.getLogger(__name__)

# How long a command waits for an offline TV before it is dropped rather than delivered
# late. "Offline" here means not currently syncing -- a screen with no network for a week
# should not suddenly open an app or re-check for an update it has long since missed the
# point of; it should still sign out if it was genuinely removed, which is why that one
# gets by far the longest hold.
_COMMAND_HOLD_SECONDS: Dict[str, int] = {
    "bring_to_front": 24 * 3600,
    "check_update": 7 * 24 * 3600,
    "deregister": 30 * 24 * 3600,
    # A super admin's recovery lever for a tenant genuinely stuck behind a lost PIN. Held
    # as long as bring_to_front: a screen that was offline when this was queued
    # should still get it once it's back, not only if it happens to reconnect within the
    # hour.
    "remote_unlock": 24 * 3600,
}
_DEFAULT_HOLD_SECONDS = 300
_MAX_HOLD_SECONDS = max(_COMMAND_HOLD_SECONDS.values())

# device_id -> {command: expiry unix timestamp}. Consulted only when Redis itself is
# unreachable -- this process's own memory does not help a screen whose sync lands on a
# different API instance, but it is what every other best-effort path here already falls
# back to rather than failing a TV's request outright.
_IN_MEMORY_COMMANDS: Dict[str, Dict[str, float]] = {}

# HGETALL then DEL as one round trip: a plain pair of calls would hand the hash's contents
# to one sync and then let a command queued a moment later be deleted by that same sync's
# cleanup instead of surviving to the next one.
_POP_SCRIPT = """
local vals = redis.call('HGETALL', KEYS[1])
redis.call('DEL', KEYS[1])
return vals
"""


def _commands_key(device_id: str) -> str:
    return f"pending_commands:{device_id}"


def player_sync_interval_seconds() -> int:
    try:
        configured = int(os.getenv("PLAYER_SYNC_INTERVAL_SECONDS", "10"))
    except ValueError:
        configured = 10
    return max(5, min(configured, 3600))


def screen_offline_after_seconds() -> int:
    """Delegated to alerting, which owns the definition.

    Two copies of this drifted apart once already -- the listing called a screen offline at
    ninety seconds while the alert fired at sixty, so the dashboard showed "screen is
    offline" beside a green Online badge. It lives in alerting because that module imports
    nothing but the standard library, so the dependency can only point this way.
    """
    from ..alerting import offline_after_seconds

    return offline_after_seconds()


def _release_response(release: models.AppRelease) -> schemas.AppVersionResponse:
    return schemas.AppVersionResponse(
        version_code=release.version_code,
        version_name=release.version_name,
        apk_url=release.apk_url,
        sha256=release.sha256,
        mandatory=release.mandatory,
    )


def current_app_version(
    db: Session, target_version_code: Optional[int] = None
) -> schemas.AppVersionResponse:
    """Resolve the appropriate app release version for a TV screen."""
    if target_version_code:
        release = (
            db.query(models.AppRelease)
            .filter(models.AppRelease.version_code == target_version_code)
            .first()
        )
        if release:
            return _release_response(release)

    # Fallback to the latest promoted release
    release = (
        rollout.eligible_for_fallback(db.query(models.AppRelease))
        .order_by(models.AppRelease.version_code.desc())
        .first()
    )
    if release:
        return _release_response(release)

    return schemas.AppVersionResponse(
        version_code=int(os.getenv("PLAYER_VERSION_CODE", "1")),
        version_name=os.getenv("PLAYER_VERSION_NAME", "1.0"),
        apk_url=os.getenv("PLAYER_APK_URL") or None,
        sha256=os.getenv("PLAYER_APK_SHA256") or None,
        mandatory=os.getenv("PLAYER_UPDATE_MANDATORY", "false").lower() == "true",
    )


async def queue_device_command(device_id: str, command: str, ttl_seconds: Optional[int] = None) -> bool:
    """Queue a remote command for a screen, to be delivered on its next sync or heartbeat
    even if it is offline right now.

    One slot per command type, not one slot per screen: queuing "check_update" must not
    discard an already-queued "deregister", or a screen an operator just removed stays
    paired because an unrelated update check landed on top of it. A second call for the
    SAME command just refreshes its hold -- there is nothing to merge.
    """
    if not device_id:
        return False
    hold = ttl_seconds if ttl_seconds is not None else _COMMAND_HOLD_SECONDS.get(command, _DEFAULT_HOLD_SECONDS)
    expiry = time.time() + hold
    try:
        redis = database.get_redis()
        await redis.hset(_commands_key(device_id), command, str(expiry))
        # The hash's own TTL is only a backstop against an orphaned key for a screen that
        # never comes back -- correctness comes from the per-command expiry checked in
        # pop_device_commands, not from this. Reset to the longest possible hold on every
        # write so an earlier, shorter-held command already in the hash is not cut off.
        await redis.expire(_commands_key(device_id), _MAX_HOLD_SECONDS)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Could not queue command %r in Redis for device %s: %s (using in-memory fallback)",
            command,
            device_id,
            exc,
        )
        _IN_MEMORY_COMMANDS.setdefault(device_id, {})[command] = expiry
        return True


async def pop_device_commands(device_id: str) -> List[str]:
    """Take every pending command for this device that has not exceeded its hold time,
    consuming all of them.

    Redis first; the in-memory fallback is consulted only when Redis itself failed to
    queue a command in the first place (it does not see commands queued by another API
    process, so it is never preferred).
    """
    if not device_id:
        return []
    now = time.time()
    commands: List[str] = []
    seen: set[str] = set()

    try:
        redis = database.get_redis()
        raw = await redis.eval(_POP_SCRIPT, 1, _commands_key(device_id))
        pairs = [v.decode("utf-8") if isinstance(v, bytes) else str(v) for v in (raw or [])]
        for command, expiry_raw in zip(pairs[0::2], pairs[1::2]):
            seen.add(command)
            if float(expiry_raw) > now:
                commands.append(command)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read pending commands from Redis for device %s: %s", device_id, exc)

    in_memory = _IN_MEMORY_COMMANDS.pop(device_id, None)
    if in_memory:
        for command, expiry in in_memory.items():
            if command not in seen and expiry > now:
                commands.append(command)

    return commands


async def pop_device_command(device_id: str) -> Optional[str]:
    """Back-compat single-command accessor for any caller not yet updated to the list."""
    commands = await pop_device_commands(device_id)
    return commands[0] if commands else None


class ScreenTelemetryService(BaseService):
    """Application Service for TV device telemetry, remote commands, and version checks."""

    def __init__(self, db: Session, repo: Optional[ScreenRepository] = None):
        super().__init__(db)
        self.repo = repo or ScreenRepository(db)

    def get_app_version(self, target_version_code: Optional[int] = None) -> schemas.AppVersionResponse:
        return current_app_version(self.db, target_version_code)

    async def queue_command(self, device_id: str, command: str, ttl: Optional[int] = None) -> bool:
        return await queue_device_command(device_id, command, ttl)

    async def pop_commands(self, device_id: str) -> List[str]:
        return await pop_device_commands(device_id)
