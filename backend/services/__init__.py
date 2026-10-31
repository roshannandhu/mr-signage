"""Clean Architecture Services Package."""

from .base import BaseService
from .playlist_service import PlaylistService, bump_playlist, set_schedule
from .device_auth_service import (
    DeviceAuthService,
    verify_device_auth,
    park_device_secret,
    collect_device_secret,
    legacy_device_auth_allowed,
    issue_device_secret,
    token_is_for_device,
)
from .screen_content_service import (
    ScreenContentService,
    resolve_screen_playlist,
    groups_by_id,
    MAX_GROUP_DEPTH,
)
from .screen_telemetry_service import (
    ScreenTelemetryService,
    current_app_version,
    queue_device_command,
    pop_device_command,
    player_sync_interval_seconds,
    screen_offline_after_seconds,
)
from .screen_pairing_service import (
    ScreenPairingService,
    generate_pair_code,
    as_aware_utc,
)
from .placement_service import (
    PlacementService,
    REPORTING_GRACE,
    effective_ends_at,
    total_price_paise,
    settlement,
    refresh_paid_state,
    playlist_for_target,
    place_advert,
    unplace_advert,
    repair_orphaned_targets,
    reconcile_unplaced_bookings,
)
from .subscription_service import expire_due_subscriptions

__all__ = [
    "expire_due_subscriptions",
    "BaseService",
    "PlaylistService",
    "bump_playlist",
    "set_schedule",
    "DeviceAuthService",
    "verify_device_auth",
    "park_device_secret",
    "collect_device_secret",
    "legacy_device_auth_allowed",
    "issue_device_secret",
    "token_is_for_device",
    "ScreenContentService",
    "resolve_screen_playlist",
    "groups_by_id",
    "MAX_GROUP_DEPTH",
    "ScreenTelemetryService",
    "current_app_version",
    "queue_device_command",
    "pop_device_command",
    "player_sync_interval_seconds",
    "screen_offline_after_seconds",
    "ScreenPairingService",
    "generate_pair_code",
    "as_aware_utc",
    "PlacementService",
    "REPORTING_GRACE",
    "effective_ends_at",
    "total_price_paise",
    "settlement",
    "refresh_paid_state",
    "playlist_for_target",
    "place_advert",
    "unplace_advert",
    "repair_orphaned_targets",
    "reconcile_unplaced_bookings",
]
