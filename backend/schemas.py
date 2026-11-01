import re
from datetime import datetime, time, timedelta
from typing import Annotated, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from .media_urls import resolve_media_url


# "super_admin" is the platform operator, not a tenant role: it is the only role that may
# publish an AppRelease, and a release installs across every tenant's fleet.
#
# Role is the *output* type -- what a user may be. TenantRole is the *input* type: the
# roles an organisation's owner is allowed to hand out from the team page. They are
# deliberately different. Sharing one type let an owner POST {"role": "super_admin"} to
# /api/users and mint themselves a platform operator, turning tenant ownership into
# control of every other tenant's TVs. A super_admin is created only by
# `python -m backend.seed_admin --role super_admin`, which needs shell access to the host.
Role = Literal["super_admin", "owner", "editor", "viewer"]
TenantRole = Literal["owner", "editor", "viewer"]
PlaybackState = Literal["playing", "idle", "error"]
# Promotion order for a player build: draft -> canary -> released. Only "released"
# is offered to screens that carry no explicit target_version_code pin.
RolloutState = Literal["draft", "canary", "released"]
TransitionName = Literal[
    "none",
    "fade",
    "slide_left",
    "slide_right",
    "slide_up",
    "slide_down",
    "zoom",
]


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    role: TenantRole = "viewer"

    @field_validator("username")
    @classmethod
    def no_device_namespace(cls, value: str) -> str:
        # "device:<id>" is how a screen's token names its subject. A user called that would
        # share a token subject with a screen, so the colon is reserved.
        if ":" in value:
            raise ValueError("username cannot contain ':'")
        return value

    @field_validator("password")
    @classmethod
    def valid_bcrypt_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("password must be at most 72 UTF-8 bytes")
        return value


class UserUpdate(BaseModel):
    role: Optional[TenantRole] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def valid_optional_bcrypt_length(cls, value: Optional[str]) -> Optional[str]:
        if value and len(value.encode("utf-8")) > 72:
            raise ValueError("password must be at most 72 UTF-8 bytes")
        return value


class UserResponse(BaseModel):
    id: int
    organization_id: int
    username: str
    role: Role
    is_active: bool
    created_at: datetime
    full_name: Optional[str] = None
    email: Optional[EmailStr] = None
    # Read-only convenience for the account menu, which otherwise has only a numeric
    # organization_id to show. Resolved from the relationship, never written through here.
    organization_name: Optional[str] = None
    organization_status: Optional[str] = "active"

    # organization_name is resolved by User.organization_name on the model, so
    # from_attributes picks it up like any other column.
    model_config = ConfigDict(from_attributes=True)


class ProfileUpdate(BaseModel):
    """Self-service profile edit. Deliberately excludes role, is_active and password --
    privilege changes stay on the owner-gated /api/users routes, and a password change
    needs the current password (see PasswordChange)."""

    full_name: Optional[str] = Field(default=None, max_length=120)
    email: Optional[EmailStr] = None


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def valid_bcrypt_length(cls, value: str) -> str:
        if len(value.encode("utf-8")) > 72:
            raise ValueError("password must be at most 72 UTF-8 bytes")
        return value


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")

FitMode = Literal["contain", "cover"]
SyncRole = Literal["leader", "follower"]
OperatingMode = Literal["always", "hours", "never"]


class ScreenBase(BaseModel):
    name: Optional[str] = None
    orientation: int = 0
    device_id: Optional[str] = None
    group_id: Optional[int] = None
    @field_validator("orientation")
    @classmethod
    def valid_orientation(cls, value: int) -> int:
        if value not in (0, 90, 180, 270):
            raise ValueError("orientation must be 0, 90, 180, or 270")
        return value


class ScreenCreate(ScreenBase):
    pass


class ScreenSignInRequest(BaseModel):
    """Credentials typed on the TV itself, so a screen can join without a pairing code."""

    username: str
    password: str
    device_id: str
    name: Optional[str] = Field(default=None, max_length=120)
    installation_id: Optional[str] = None
    model: Optional[str] = None
    manufacturer: Optional[str] = None


class GoogleWebSignInRequest(BaseModel):
    """The authorization code a browser just came back from Google holding."""

    code: str
    # Echoed back to Google, which requires it to match the one used to obtain the code.
    redirect_uri: str


class GoogleDeviceStartRequest(BaseModel):
    """A TV asking for a Google code to put on screen."""

    device_id: str
    name: Optional[str] = Field(default=None, max_length=120)
    installation_id: Optional[str] = None
    model: Optional[str] = None
    manufacturer: Optional[str] = None


class GoogleDeviceStartResponse(BaseModel):
    """What the TV displays, plus the handle it polls with.

    `poll_token` is a short-lived JWT holding the device_code and the device_id it was
    issued for. Google's device_code is meant to live on the device, but on its own it
    says nothing about *which* screen is being claimed -- wrapping the pair means a code
    lifted off one TV cannot be redeemed to bind a different one.
    """

    user_code: str
    verification_url: str
    interval: int
    expires_in: int
    poll_token: str


class GoogleDevicePollRequest(BaseModel):
    poll_token: str


class PairedScreenPublic(BaseModel):
    """What a TV that just signed in with Google is told about its own screen.

    /google/poll is reachable with nothing but a poll token, which /google/start hands to
    anyone, so it must not return the full screen record: that carries the maintenance PIN,
    the hardware identity and the tenant's description, location and tags. The player reads
    the name and the one-time device secret and takes everything else from /sync.
    """

    name: Optional[str] = None
    status: str
    device_secret: Optional[str] = None


class GoogleDevicePollResponse(BaseModel):
    """Where the approval has got to.

    `slow_down` is passed through rather than swallowed: Google returns it when the TV is
    polling too fast, and the player has to widen its own interval or it will simply keep
    earning the same answer.
    """

    status: Literal["pending", "slow_down", "denied", "expired", "bound"]
    screen: Optional[PairedScreenPublic] = None
    detail: Optional[str] = None


class ScreenPatch(BaseModel):
    """Partial screen update: only the fields actually present are written.

    The PUT above takes a whole ScreenBase, so a caller editing just the name had
    to resend orientation and would reset a portrait display to landscape.
    """

    name: Optional[str] = None
    orientation: Optional[int] = None
    group_id: Optional[int] = None
    target_version_code: Optional[int] = None
    description: Optional[str] = None
    tags: Optional[str] = None
    # Set together: the label and the pin must always describe the same place.
    location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    place_id: Optional[str] = None
    timezone: Optional[str] = None
    fit_mode: Optional[FitMode] = None
    # Exactly four digits: the player prompts for it on a TV remote, where anything
    # longer or non-numeric is painful to type on a d-pad.
    maintenance_pin: Optional[str] = Field(default=None, pattern=r"^\d{4}$")
    sync_playback: Optional[bool] = None
    sync_role: Optional[SyncRole] = None
    leader_screen_id: Optional[int] = None
    operating_mode: Optional[OperatingMode] = None
    operating_hours: Optional[dict[str, list[str]]] = None
    # True keeps this screen on its own maintenance_pin even when the workspace has set a
    # shared lock PIN. See effective_maintenance_pin.
    uses_own_pin: Optional[bool] = None

    @field_validator("orientation")
    @classmethod
    def valid_orientation(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value not in (0, 90, 180, 270):
            raise ValueError("orientation must be 0, 90, 180, or 270")
        return value

    @field_validator("operating_hours")
    @classmethod
    def valid_hours(cls, value: Optional[dict[str, list[str]]]) -> Optional[dict[str, list[str]]]:
        """Each day maps to exactly [start, end] as HH:MM.

        Validated here rather than in the player: a malformed window is the difference
        between a screen that runs all day and one that never wakes up.
        """
        if value is None:
            return None
        for day, window in value.items():
            if day not in WEEKDAYS:
                raise ValueError(f"unknown day '{day}'")
            if len(window) != 2:
                raise ValueError(f"{day} must be [start, end]")
            for stamp in window:
                if not _HHMM.fullmatch(stamp):
                    raise ValueError(f"{day}: '{stamp}' is not HH:MM")
        return value


class LockPinUpdate(BaseModel):
    """Set or clear the whole workspace's shared maintenance PIN.

    Blank restores today's behaviour (every screen on its own PIN) rather than locking the
    fleet out with an empty code -- the same "blank means unset" the brand name field uses.
    """

    lock_pin: Optional[str] = Field(default=None, pattern=r"^\d{4}$")


class LockPinResponse(BaseModel):
    lock_pin: Optional[str] = None


class LockEnabledUpdate(BaseModel):
    lock_enabled: bool


class LockEnabledResponse(BaseModel):
    lock_enabled: bool


class KioskAttemptRequest(BaseModel):
    """A TV reporting one local PIN check, right or wrong, best-effort."""

    success: bool


class KioskUnlockEventResponse(BaseModel):
    id: int
    screen_id: Optional[int] = None
    screen_name: Optional[str] = None
    occurred_at: datetime
    method: str
    success: bool
    actor_username: Optional[str] = None


class RemoteUnlockResponse(BaseModel):
    queued: bool


class ScreenResponse(ScreenBase):
    id: int
    pair_code: Optional[str]
    status: str
    # Returned exactly once, by the routes that bind a screen (/pair, /sign-in, the Google
    # flows), so the device can store it and authenticate from then on. Only the hash is
    # persisted, and Screen has no `device_secret` attribute, so every other route that
    # serialises a Screen -- notably GET /api/screens/ for the dashboard -- leaves this
    # null rather than leaking a credential into the fleet list.
    device_secret: Optional[str] = None
    orientation_source: str = "auto"
    # Newest capture, attached by the list endpoint so the fleet grid can show a live
    # thumbnail without a request per card.
    latest_screenshot: Optional[str] = None
    # Update state, so the dashboard can monitor a rollout instead of guessing.
    # app_version above is what the TV reports it is actually running.
    target_version_code: Optional[int] = None
    # Capped but not enumerated: the backend must keep accepting heartbeats from APKs
    # older than whatever the current status vocabulary is, so an unrecognised value is
    # stored rather than rejected. The cap stops a malformed device filling the column.
    update_status: Optional[str] = Field(default=None, max_length=64)
    description: Optional[str] = None
    tags: Optional[str] = None
    location: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    place_id: Optional[str] = None
    fit_mode: FitMode = "contain"
    maintenance_pin: Optional[str] = None
    sync_playback: bool = False
    sync_role: SyncRole = "leader"
    leader_screen_id: Optional[int] = None
    operating_mode: OperatingMode = "always"
    operating_hours: Optional[dict[str, list[str]]] = None
    # NULL means the screen claimed this organisation from the TV and is waiting for an
    # operator to let it in. The dashboard reads this to build the approval queue.
    approved_at: Optional[datetime] = None
    installation_id: Optional[str] = None
    last_seen: datetime
    playlist_id: Optional[int]
    group_id: Optional[int]
    device_version: Optional[str]
    app_version: Optional[str] = None
    storage_used: Optional[str]
    playback_state: PlaybackState = "idle"
    current_item_id: Optional[int] = None
    last_error: Optional[str] = None
    last_error_at: Optional[datetime] = None
    effective_playlist_id: Optional[int]
    
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    refresh_rate: Optional[float] = None
    total_ram_mb: Optional[int] = None
    available_ram_mb: Optional[int] = None
    total_storage_mb: Optional[int] = None
    free_storage_mb: Optional[int] = None
    supported_video_codecs: Optional[list[str]] = None
    max_decode_width: Optional[int] = None
    max_decode_height: Optional[int] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    # "serial" | "android_id" | "random" | None (registered before this was recorded).
    # Decides whether this panel can be recognised again after being wiped, which is
    # otherwise invisible until a duplicate appears in the fleet.
    identity_source: Optional[str] = None
    # None = the player has not said yet (an older build). False = updates on this screen
    # wait for someone to tap a dialog.
    device_owner: Optional[bool] = None
    # The PIN that actually opens this screen's maintenance door: its own if it has one,
    # otherwise the platform-wide fallback. Distinct from maintenance_pin, which is the
    # screen's own value and what the settings dialog writes.
    effective_maintenance_pin: Optional[str] = None
    android_version: Optional[str] = None
    sdk_int: Optional[int] = None
    network_type: Optional[str] = None
    timezone: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ContentBase(BaseModel):
    name: str
    tags: Optional[str] = None


class MediaRenditionResponse(BaseModel):
    id: int
    resolution: str
    width: int
    height: int
    rotation: int
    duration_ms: Optional[int] = None
    codec: Optional[str] = None
    sha256: Optional[str] = None
    file_size_bytes: int
    file_url: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def absolutise_urls(self):
        self.file_url = resolve_media_url(self.file_url) or self.file_url
        return self


class ContentResponse(ContentBase):
    id: int
    type: str
    file_url: str
    thumbnail: Optional[str]
    uploaded_at: datetime
    file_size_bytes: int = 0
    sha256: Optional[str] = None
    duration_ms: Optional[int] = None
    expires_at: Optional[datetime] = None
    status: str
    failed_reason: Optional[str] = None
    renditions: List[MediaRenditionResponse] = []
    
    # 1:1 Ad & Client unified metadata
    client_id: Optional[int] = None
    client_name: Optional[str] = None
    client_email: Optional[str] = None
    client_phone: Optional[str] = None
    plan_id: Optional[int] = None
    plan_name: Optional[str] = None
    placement_id: Optional[int] = None
    placement_price_paise: Optional[int] = None
    placement_starts_at: Optional[datetime] = None
    placement_ends_at: Optional[datetime] = None
    # The booking's OWN run length in days. Not derivable from the two fields above:
    # placement_ends_at is the EFFECTIVE end, which a longer per-location window or an
    # extension pushes out, so subtracting them reports a 30-day sale as 50 whenever one
    # airport screen was sold 50. The editor seeds its duration box from this, and seeding
    # it from the effective end would have made simply opening and saving a booking stretch
    # it to its longest location.
    placement_duration_days: Optional[int] = None
    placement_notes: Optional[str] = None
    screen_ids: List[int] = []
    screen_names: List[str] = []
    # Per-location run lengths, keyed by screen id. Empty when every location follows the
    # booking. Returned so re-opening the booking shows the lengths actually sold rather
    # than defaulting back to one uniform run.
    screen_days: dict[int, int] = {}

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def absolutise_urls(self):
        """Make stored locations fetchable, wherever this response is embedded.

        Doing it here rather than in each router is deliberate: the playlists endpoint
        forgot to, so nested content came back as bare paths and every thumbnail in the
        playlist editor was blank while the same asset rendered fine in the library.
        """
        self.file_url = resolve_media_url(self.file_url) or self.file_url
        self.thumbnail = resolve_media_url(self.thumbnail)
        return self


class ContentUpdate(ContentBase):
    pass


class ContentClientAdUpdate(BaseModel):
    name: Optional[str] = None
    # Stripped before the length check, so a name of nothing but spaces is rejected rather
    # than creating a client called "" with a generated code that matches nothing. The
    # frontend disables Save on a blank name; anything reaching the API directly did not.
    client_name: Annotated[str, Field(min_length=1)]

    @field_validator("client_name", mode="before")
    @classmethod
    def _strip_client_name(cls, value):
        return value.strip() if isinstance(value, str) else value
    client_email: Optional[str] = None
    client_phone: Optional[str] = None
    plan_id: Optional[int] = None
    # What this booking is actually billed. Null means "leave it alone", which is what an
    # ordinary edit -- a corrected phone number, a renamed client -- has to send.
    #
    # A plan only ever fills this in as a DEFAULT, and only when the booking is created.
    # Without the field there was no way to state a price at all: a booking sold on no plan
    # was created at zero and stayed there, so "the client does not want a package, we
    # agreed a figure" was the one sale the editor could not record. Selecting a plan is a
    # convenience, not the only way to have a price.
    price_paise: Optional[int] = Field(default=None, ge=0)
    # How long the booking itself runs. Null leaves it alone; on creation the plan's own
    # duration fills in behind it.
    #
    # Without this the length of a booking sold on NO plan was the hardcoded 30 days in
    # update_content_client_ad and nothing could change it -- so an operator could name the
    # price of a custom sale but not its length, and the only way to express any other run
    # was to give every screen its own window one at a time. That is why the editor grew a
    # second "custom" control beside the first.
    duration_days: Optional[int] = Field(default=None, ge=1, le=3650)
    screen_ids: Optional[List[int]] = None
    # Per-location run lengths, keyed by screen id: {"12": 30, "13": 10, "14": 50}.
    # A screen absent from this map runs for the booking's own window, so the ordinary
    # sale sends nothing and behaves exactly as before.
    #
    # Keyed by id rather than positional against screen_ids because the two lists would
    # drift the first time either was reordered, and silently mis-assigning a client's
    # paid duration to the wrong location is not a failure anyone would notice.
    # Bounded exactly like PlacementTargetRef.days. Unbounded here, the same number was
    # refused when it created a target and accepted when it corrected one -- in the same
    # request.
    screen_days: Optional[dict[int, Annotated[int, Field(ge=1, le=3650)]]] = None
    # Wiping every location's bespoke window is its own instruction, not something an
    # empty map implies.
    #
    # `screen_days: {}` used to mean "clear them all", which is also exactly what a client
    # that has never heard of per-location windows sends. One such client shipped: the
    # dashboard's campaigns page built its payload from a placement and had no screen_days
    # to put in it, so opening that editor and pressing Save turned a booking sold as
    # "50 days at the airport, 30 in the mall" into a flat 30 everywhere -- on the playlist
    # items the players enforce, not just in the record.
    clear_screen_days: bool = False
    notes: Optional[str] = None


class ScheduleBase(BaseModel):
    days_of_week: List[int] = Field(default_factory=list)
    start_time: Optional[time] = None
    end_time: Optional[time] = None

    @field_validator("days_of_week", mode="before")
    @classmethod
    def valid_days(cls, value) -> List[int]:
        if isinstance(value, str):
            value = [int(day) for day in value.split(",") if day != ""]
        value = value or []
        if any(day < 0 or day > 6 for day in value):
            raise ValueError("days_of_week values must be between 0 (Monday) and 6 (Sunday)")
        return sorted(set(value))


class ScheduleResponse(ScheduleBase):
    id: int

    model_config = ConfigDict(from_attributes=True)


class PlaylistItemBase(BaseModel):
    content_id: int
    duration: int = Field(default=10, ge=1, le=86400)
    # None means "follow the screen's own orientation"; a number overrides it for this item.
    rotation: Optional[int] = None
    order: int = Field(default=0, ge=0)
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    transition: Optional[TransitionName] = None
    transition_ms: Optional[int] = Field(default=None, ge=100, le=3000)

    @field_validator("rotation")
    @classmethod
    def valid_rotation(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value not in (0, 90, 180, 270):
            raise ValueError("rotation must be 0, 90, 180, or 270")
        return value

    @model_validator(mode="after")
    def valid_date_range(self):
        if "duration" in self.model_fields_set and self.duration is None:
            raise ValueError("duration cannot be null")
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class PlaylistItemCreate(PlaylistItemBase):
    schedule: Optional[ScheduleBase] = None


class PlaylistItemUpdate(BaseModel):
    duration: Optional[int] = Field(default=None, ge=1, le=86400)
    # None clears the override so the item follows the screen's own orientation again.
    rotation: Optional[int] = None
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None
    schedule: Optional[ScheduleBase] = None
    transition: Optional[TransitionName] = None
    transition_ms: Optional[int] = Field(default=None, ge=100, le=3000)

    @field_validator("rotation")
    @classmethod
    def valid_rotation(cls, value: Optional[int]) -> Optional[int]:
        if value is not None and value not in (0, 90, 180, 270):
            raise ValueError("rotation must be 0, 90, 180, or 270")
        return value

    @model_validator(mode="after")
    def valid_date_range(self):
        if "duration" in self.model_fields_set and self.duration is None:
            raise ValueError("duration cannot be null")
        if self.start_at and self.end_at and self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class PlaylistItemResponse(PlaylistItemBase):
    id: int
    content: ContentResponse
    schedule: Optional[ScheduleResponse] = None

    model_config = ConfigDict(from_attributes=True)


class PlaylistBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    default_transition: TransitionName = "fade"
    default_transition_ms: int = Field(default=600, ge=100, le=3000)


class PlaylistCreate(PlaylistBase):
    pass


class PlaylistUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    default_transition: Optional[TransitionName] = None
    default_transition_ms: Optional[int] = Field(default=None, ge=100, le=3000)

    @model_validator(mode="after")
    def has_update(self):
        if not self.model_fields_set:
            raise ValueError("at least one playlist field is required")
        return self


class PlaylistTransitionUpdate(BaseModel):
    transition: TransitionName
    transition_ms: int = Field(default=600, ge=100, le=3000)
    apply_to_all: bool = False


class PlaylistResponse(PlaylistBase):
    id: int
    created_at: datetime
    updated_at: datetime
    items: List[PlaylistItemResponse] = []

    model_config = ConfigDict(from_attributes=True)


class ScreenGroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: Optional[int] = None
    is_dynamic: bool = False
    dynamic_criteria: Optional[dict] = None


class ScreenGroupUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    parent_id: Optional[int] = None
    is_dynamic: bool = False
    dynamic_criteria: Optional[dict] = None


class ScreenGroupMembersUpdate(BaseModel):
    screen_ids: List[int]


class ScreenGroupResponse(BaseModel):
    id: int
    name: str
    parent_id: Optional[int] = None
    is_dynamic: bool
    dynamic_criteria: Optional[dict] = None
    playlist_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    screen_count: int = 0


class RegisterRequest(BaseModel):
    device_id: str
    installation_id: Optional[str] = None
    hardware_name: Optional[str] = None
    device_model: Optional[str] = None
    manufacturer: Optional[str] = None
    # Which identifier the panel could actually produce: "serial" survives a factory reset,
    # "android_id" survives a reinstall but not a reset, "random" survives neither. Recorded
    # so the fleet can show which screens would come back as duplicates if they were wiped,
    # which was previously knowable only after it happened.
    identity_source: Optional[str] = None


class RegisterResponse(BaseModel):
    """
    Deliberately narrow: /screens/register is unauthenticated, so it must not reuse
    ScreenResponse. That schema carries tenant configuration — including
    maintenance_pin — and anyone who knows a device_id can call this route.
    Only what the TV needs to finish pairing goes here.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: Optional[str] = None
    name: Optional[str] = None
    status: str
    pair_code: Optional[str] = None
    pair_code_expires_at: Optional[datetime] = None
    # One-time delivery of the device credential, and the only way a pair-code screen can
    # ever receive one: /pair is called by the DASHBOARD, so its response never reaches the
    # TV. The device polls /register while it waits to be claimed, so the secret is handed
    # over on the first poll after pairing and never again -- see collect_device_secret.
    device_secret: Optional[str] = None


class PairRequest(BaseModel):
    pair_code: str


class EnrollRequest(BaseModel):
    device_id: str
    enrollment_token: str
    installation_id: Optional[str] = None


class EnrollResponse(BaseModel):
    device_id: str
    device_secret: str
    organization_id: int
    screen_id: int


class DeviceAuthRequest(BaseModel):
    device_id: str
    device_secret: str

class DeviceTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class HeartbeatRequest(BaseModel):
    device_id: str
    device_version: Optional[str] = None
    # True when the player is Android's device owner and can therefore install an update
    # unattended. Reported every beat, because a panel can be provisioned long after it was
    # first paired.
    device_owner: Optional[bool] = None
    storage_used: Optional[str] = None
    playback_state: Optional[PlaybackState] = None
    current_item_id: Optional[int] = None
    last_error: Optional[str] = Field(default=None, max_length=1000)
    app_version: Optional[str] = Field(default=None, max_length=100)
    version_code: Optional[int] = None
    update_status: Optional[str] = None
    screen_width: Optional[int] = None
    screen_height: Optional[int] = None
    refresh_rate: Optional[float] = None
    orientation: Optional[int] = None
    total_ram_mb: Optional[int] = None
    available_ram_mb: Optional[int] = None
    total_storage_mb: Optional[int] = None
    free_storage_mb: Optional[int] = None
    supported_video_codecs: Optional[list[str]] = None
    max_decode_width: Optional[int] = None
    max_decode_height: Optional[int] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    android_version: Optional[str] = None
    sdk_int: Optional[int] = None
    network_type: Optional[str] = None
    timezone: Optional[str] = None
    
    model_config = ConfigDict(extra="ignore")

    @field_validator("last_error")
    @classmethod
    def clean_last_error(cls, value: Optional[str]) -> Optional[str]:
        cleaned = value.strip() if value else None
        return cleaned or None


class AppVersionResponse(BaseModel):
    version_code: int
    version_name: str
    apk_url: Optional[str] = None
    sha256: Optional[str] = None
    mandatory: bool = False


class AppReleaseCreate(BaseModel):
    version_code: int = Field(ge=1)
    version_name: str = Field(min_length=1, max_length=50)
    apk_url: str = Field(max_length=2048)
    # Mandatory, and it was not always so. The device verifies the APK it downloads
    # against this digest; when it was optional a release published without one was
    # installed unverified, which made the checksum a decoration rather than a control.
    # There is no legitimate reason to publish a build whose bytes you cannot pin.
    sha256: str = Field(min_length=64, max_length=64)
    mandatory: bool = False
    # New builds land as drafts. Publishing one used to make it live for every unpinned
    # screen the instant it was created, which left no way to try a build on five TVs
    # first. Promote with PATCH /api/releases/{version_code} once the ring looks healthy.
    rollout_state: RolloutState = "draft"

    @field_validator("apk_url")
    @classmethod
    def https_only(cls, value: str) -> str:
        # Plain HTTP would let anyone on the path swap the APK. The digest below would
        # catch that, but defence in depth is cheap here and the player installs this
        # file silently when it is device owner.
        if not value.startswith("https://"):
            raise ValueError("apk_url must be an https:// URL")
        return value

    @field_validator("sha256")
    @classmethod
    def hex_digest(cls, value: str) -> str:
        candidate = value.strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", candidate):
            raise ValueError("sha256 must be 64 hexadecimal characters")
        return candidate


class AppReleaseResponse(AppReleaseCreate):
    id: int
    created_at: datetime
    # Widened back to optional purely for rows written before the digest was mandatory.
    # Serialising one of those against the stricter parent raised ValidationError, which
    # surfaced as a 500 on the releases list rather than as the legacy row it is. The
    # player treats a null digest as "refuse to install", so an unpinned old release is
    # inert rather than dangerous.
    sha256: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class AppReleasePatch(BaseModel):
    """Promote (or demote) a build. The only mutable field: version_code, apk_url and
    sha256 are the identity of an artefact that screens may already be pinned to, and
    editing them would silently repoint those screens at different bytes."""

    rollout_state: RolloutState


class SyncResponse(BaseModel):
    playlist: Optional[PlaylistResponse] = None
    playlist_updated_at: Optional[datetime] = None
    status: Optional[str] = None
    app_version: Optional[AppVersionResponse] = None
    sync_interval_seconds: int = Field(default=60, ge=15, le=3600)
    # How the player should scale content that does not match the panel's aspect ratio.
    # Per screen rather than per item, which is how an operator thinks about a display.
    fit_mode: FitMode = "contain"
    # Cached by the player so the maintenance screen still opens with no network.
    maintenance_pin: Optional[str] = None
    # The platform operator's master code. Opens any screen, alongside -- never instead of --
    # the screen's own, so per-screen containment is unchanged: a leaked tenant PIN still
    # opens exactly one screen. Withheld from an unauthenticated device call like the other.
    master_pin: Optional[str] = None
    # The player blanks itself outside these, using its own clock, so a shop TV goes dark
    # at closing time even when the network does not. Sent on every sync because the
    # player evaluates them locally -- it must keep working through an outage, which is
    # the whole point of the offline cache.
    operating_mode: OperatingMode = "always"
    operating_hours: Optional[dict[str, list[str]]] = None
    # The first queued command, for a player build that predates pending_commands below.
    pending_command: Optional[str] = None
    # Every command still within its hold time -- an offline screen can have more than
    # one waiting (a check_update queued yesterday and a bring_to_front just now), and
    # pending_command alone could only ever deliver the first.
    pending_commands: list[str] = Field(default_factory=list)
    # Why this screen is being served the demo reel instead of its workspace's adverts:
    # "pending_approval", "suspended", "rejected" or "expired". None on a normal sync.
    # The player shows a designed notice for it rather than a raw error -- a venue owner is
    # looking at this on a wall. Older builds ignore the field (Gson drops unknown keys), so
    # they keep the demo reel and lose only the caption.
    service_state: Optional[str] = None
    screen_id: Optional[int] = None
    organization_id: Optional[int] = None
    # The tenant's own brand, shown on the waiting/idle screen once a TV has paired. Raw
    # values, not the brand-or-workspace-name fallback the dashboard header and reports
    # use: a tenant who has never set one gets the platform's own "MR Signage" mark on the
    # TV rather than an internal workspace name never meant for a screen on a wall.
    brand_name: Optional[str] = None
    brand_logo_url: Optional[str] = None
    brand_color: Optional[str] = None
    # Whether this tenant's plan sells the TV lock feature at all. False (including a field
    # missing on an older server, which Gson reads as the Kotlin default) means stay locked
    # -- the safe failure, since the alternative is every TV on a lapsed plan unlocking
    # itself. True does not by itself mean "locked right now"; maintenance_pin above still
    # carries the code that actually opens it.
    kiosk_lock: bool = True


class PlanResponse(BaseModel):
    id: int
    name: str
    slug: str
    monthly_price_paise: int
    yearly_price_paise: int
    # One-time charge for `duration_days` of access — what the storefront sells on.
    price_paise: int = 0
    duration_days: int = 30
    # None = no screen limit (an "unlimited" package). 0 is a real limit of zero. Typed int,
    # this made /api/billing/summary and /plans answer 500 for any tenant on an unlimited plan.
    max_screens: Optional[int] = None
    max_clients: int = 0
    # Same convention as max_clients: 0 is a real package granting no ad slots, matching
    # Plan.max_ad_slots's own column.
    max_ad_slots: int = 0
    max_storage_bytes: int
    feature_flags: dict[str, bool] = Field(default_factory=dict)


class SubscriptionResponse(BaseModel):
    status: str
    billing_period: str
    current_period_start: Optional[datetime] = None
    current_period_end: Optional[datetime] = None
    grace_period_end: Optional[datetime] = None
    provider: Optional[str] = None
    provider_subscription_id: Optional[str] = None


class BillingSummaryResponse(BaseModel):
    plan: PlanResponse
    subscription: SubscriptionResponse
    screens_used: int
    # The limit actually enforced for this tenant -- the override if one is set, else the
    # plan's own number -- not plan.max_screens/max_ad_slots/max_clients, which an admin's
    # per-tenant override can diverge from. None means unlimited.
    max_screens: Optional[int] = None
    ad_slots_used: int = 0
    max_ad_slots: Optional[int] = None
    clients_used: int = 0
    max_clients: Optional[int] = None
    storage_used_bytes: int
    # organization.storage_quota_bytes, not plan.max_storage_bytes: an admin's direct
    # storage override writes this column without touching the plan. 0 means unlimited.
    storage_quota_bytes: int = 0
    is_read_only: bool


class CheckoutRequest(BaseModel):
    plan_id: int
    billing_period: Literal["monthly", "yearly"] = "monthly"


class CheckoutResponse(BaseModel):
    provider: str
    provider_subscription_id: str
    checkout_url: str


class PurchaseRequest(BaseModel):
    """Buy a standard package as a one-time charge for its access period."""

    plan_id: int


class PurchaseResponse(BaseModel):
    """Everything the client needs to open a payment sheet, or to auto-complete a mock buy.

    `provider` is "internal" for a free package (activated on the spot, no order),
    "mock" when PAYMENT_PROVIDER=mock (the client hits /billing/mock/confirm to finish), or
    "razorpay" (open Razorpay Checkout with `key_id` + `order_id` + `amount_paise`).
    """

    provider: str
    order_id: Optional[str] = None
    amount_paise: int = 0
    key_id: Optional[str] = None
    # Where to send the browser: a hosted page for a real provider, or an internal route
    # for the free/mock paths.
    checkout_url: Optional[str] = None


class MockConfirmRequest(BaseModel):
    """Finish a mock (test) purchase: the order the storefront was handed."""

    order_id: str


# The empty-string alternative matters: a cleared form field arrives as "", not absent, and
# without it an empty phone box would 422 a request that was otherwise perfectly fine.
PHONE_PATTERN = r"^(\+?[0-9 ]{7,15})?$"


class CustomPlanRequestCreate(BaseModel):
    """A tenant's ask for a bespoke package: the counts and period they want."""

    max_screens: int = Field(default=0, ge=0)
    max_clients: int = Field(default=0, ge=0)
    max_ad_slots: int = Field(default=0, ge=0)
    max_storage_bytes: int = Field(default=10 * 1024 * 1024 * 1024, ge=0)
    duration_days: int = Field(default=30, ge=1, le=3650)
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    notes: Optional[str] = Field(default=None, max_length=1000)
    # Who the operator actually reaches about this. All optional -- the organisation's own
    # owner is always a fallback -- but checked when given, the same way the mailto draft
    # the tenant is about to send is built from exactly these fields.
    company_name: Optional[str] = Field(default=None, max_length=200)
    contact_name: Optional[str] = Field(default=None, max_length=200)
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = Field(default=None, max_length=20, pattern=PHONE_PATTERN)

    @field_validator("contact_email", mode="before")
    @classmethod
    def blank_email_is_none(cls, value):
        # A cleared form field arrives as "", and EmailStr (unlike the phone pattern above)
        # has no way to say "or empty" -- it rejects "" as a malformed address outright.
        return None if value == "" else value


class CustomPlanRequestPrice(BaseModel):
    """The operator setting a price on a custom request."""

    price_paise: int = Field(ge=0)


class CustomPlanRequestUpdate(BaseModel):
    """The operator revising a bespoke package -- including one already paid for.

    Every field optional: a PATCH carries only what changed. Editing a paid request is the
    supported way to move a tenant already sitting on a custom package, because the caps are
    read through the plan rather than copied onto the org (see Organization.effective_max_*),
    so revising the plan moves the tenant with it.
    """

    max_screens: Optional[int] = Field(default=None, ge=0)
    max_clients: Optional[int] = Field(default=None, ge=0)
    max_ad_slots: Optional[int] = Field(default=None, ge=0)
    max_storage_bytes: Optional[int] = Field(default=None, ge=0)
    duration_days: Optional[int] = Field(default=None, ge=1, le=3650)
    feature_flags: Optional[dict[str, bool]] = None
    price_paise: Optional[int] = Field(default=None, ge=0)
    notes: Optional[str] = Field(default=None, max_length=1000)


class CustomPlanRequestResponse(BaseModel):
    id: int
    organization_id: int
    max_screens: int
    max_clients: int
    max_ad_slots: int
    max_storage_bytes: int
    duration_days: int
    feature_flags: dict[str, bool] = Field(default_factory=dict)
    price_paise: int
    status: str
    notes: Optional[str] = None
    created_at: datetime
    # Only on the operator's list, so a request can be attributed to a workspace.
    organization_name: Optional[str] = None
    company_name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None


class EnrollmentTokenCreate(BaseModel):
    description: Optional[str] = None
    expires_at: Optional[datetime] = None
    max_uses: Optional[int] = Field(default=None, ge=1)


class EnrollmentTokenResponse(BaseModel):
    id: int
    token: Optional[str] = None
    description: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    max_uses: Optional[int] = None
    use_count: int = 0
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class PlayEventItem(BaseModel):
    event_id: str
    media_id: Optional[int] = None
    playlist_id: Optional[int] = None
    campaign_id: Optional[int] = None
    
    device_started_at: datetime
    device_finished_at: datetime
    
    corrected_started_at: datetime
    corrected_finished_at: datetime
    
    duration_ms: int
    status: Literal["completed", "partial", "error"]
    error_message: Optional[str] = None
    # Null for a TV app built before this field existed, or a play it genuinely never
    # learned the answer for -- aggregation estimates those from the upload gap instead of
    # guessing per-event.
    played_offline: Optional[bool] = None


class PlayLogBatchRequest(BaseModel):
    device_id: str
    screen_id: Optional[int] = None
    organization_id: Optional[int] = None
    events: List[PlayEventItem] = Field(..., max_length=500)


# --- Ad bookings ---------------------------------------------------------------------

class BrandingUpdate(BaseModel):
    """What a tenant may put on the report they hand to their own client."""

    brand_name: Optional[str] = Field(default=None, max_length=120)
    # "#RRGGBB". Validated rather than trusted: it is written straight into a PDF colour
    # and into inline style on the dashboard, so anything else is both a render failure and
    # an injection surface.
    brand_color: Optional[str] = Field(default=None, pattern=r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


class BrandingResponse(BaseModel):
    brand_name: Optional[str] = None
    brand_color: Optional[str] = None
    # Resolved for display; the column holds the host-independent form.
    logo_url: Optional[str] = None
    # What the report actually prints, once the fallback to the workspace name is applied.
    effective_name: str


class ClientBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=40)
    notes: Optional[str] = None


class ClientCreate(ClientBase):
    # Optional: the router generates the next code for the tenant when it is not given, so
    # the common case is one field (a name) rather than inventing a reference by hand.
    client_code: Optional[str] = Field(default=None, max_length=20)


class ClientUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=200)
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=40)
    notes: Optional[str] = None


class ClientResponse(ClientBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    client_code: str
    active_campaigns_count: int = 0
    total_spent_paise: int = 0
    created_at: datetime


class TenantPlanBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = None
    duration_days: int = Field(default=30, ge=1, le=3650)
    max_locations: int = Field(default=1, ge=1)
    ad_slots: int = Field(default=1, ge=1)
    price_paise: int = Field(default=0, ge=0)
    support_tier: str = Field(default="Basic Support", max_length=40)


class TenantPlanCreate(TenantPlanBase):
    pass


class TenantPlanUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = None
    duration_days: Optional[int] = Field(default=None, ge=1, le=3650)
    max_locations: Optional[int] = Field(default=None, ge=1)
    ad_slots: Optional[int] = Field(default=None, ge=1)
    price_paise: Optional[int] = Field(default=None, ge=0)
    support_tier: Optional[str] = Field(default=None, max_length=40)
    is_active: Optional[bool] = None


class TenantPlanResponse(TenantPlanBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime


class PlanQuoteRequest(BaseModel):
    """A custom shape to price: one entry per location, holding that location's days.

    Null entries mean "the plan's own duration", which is what a target with no window of
    its own already does -- so the ordinary booking prices correctly with an empty-ish body
    rather than having to spell the default out per screen.
    """

    days: List[Optional[int]] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def positive_days(self):
        if any(d is not None and d < 1 for d in self.days):
            raise ValueError("a location runs for at least one day")
        return self


class PlanQuoteResponse(BaseModel):
    """What a custom shape costs on a plan, and how far past the package it goes.

    Advisory: no booking is priced from this. It exists so an operator agreeing "50 days at
    the airport" can see that it is 1.6x the package before they say yes, rather than after
    the campaign has run.
    """

    plan_id: int
    plan_name: str
    locations: int
    # locations x days, the unit both the request and the plan are measured in.
    screen_days: int
    capacity_screen_days: int
    plan_price_paise: int
    quoted_price_paise: int
    # Zero when the shape fits inside the package -- underuse is never a discount.
    extra_price_paise: int
    # The one hard limit: ensure_plan_locations will refuse this booking outright.
    exceeds_locations: bool


class ExtensionCreate(BaseModel):
    """Extend a booking's run. `extended_from` defaults to where the booking currently ends."""

    extended_to: datetime
    extended_from: Optional[datetime] = None
    additional_price_paise: int = Field(default=0, ge=0)
    is_paid: bool = False
    notes: Optional[str] = None


class ExtensionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    extended_from: datetime
    extended_to: datetime
    additional_price_paise: int
    is_paid: bool
    notes: Optional[str] = None
    created_at: datetime


class PlacementTargetRef(BaseModel):
    """One place a booking runs. Exactly one of the two ids is set."""

    screen_id: Optional[int] = None
    group_id: Optional[int] = None
    # How long the advert runs HERE, when this location was sold its own length. None
    # means "as long as the booking", which is the ordinary case and stores nothing.
    #
    # Days rather than an end date because that is how the deal is struck -- "30 in the
    # mall, 10 in the shop" -- and it is resolved against this location's own start, so a
    # screen added mid-campaign still gets the full run it was sold rather than being cut
    # short by a date fixed at the campaign's beginning.
    days: Optional[int] = Field(default=None, ge=1, le=3650)

    @model_validator(mode="after")
    def exactly_one(self):
        if (self.screen_id is None) == (self.group_id is None):
            raise ValueError("a target must name either a screen or a group, not both")
        return self


class PlacementCreate(BaseModel):
    content_id: int
    # Still accepted, and still what is stored on the row, but a client_id overrides it:
    # naming a client is the supported path and the string then follows the client record.
    advertiser: Optional[str] = Field(default=None, min_length=1, max_length=200)
    client_id: Optional[int] = None
    # Naming a plan fills in price and ends_at from it when those are not given. The values
    # are COPIED, never read through -- repricing a plan must not rebill a sold booking.
    plan_id: Optional[int] = None
    # How many screens this booking may cover when no plan is named. 0 = no cap, which is
    # what a custom sale has always meant. A package states its own limit and overrides it.
    max_locations: int = Field(default=0, ge=0, le=10000)
    price_paise: int = Field(default=0, ge=0)
    is_paid: bool = False
    starts_at: datetime
    # Optional only when a plan is named: the plan's duration_days then sets it, which is
    # the point of selling by package rather than by date.
    ends_at: Optional[datetime] = None
    notes: Optional[str] = None
    targets: List[PlacementTargetRef] = []

    @model_validator(mode="after")
    def valid_window(self):
        if self.ends_at is None and self.plan_id is None:
            # A bespoke sale states its length per location -- "30 in the mall, 10 in the
            # shop" -- so the longest of those IS the booking's length and asking for it
            # again is asking the operator to do arithmetic the request already contains.
            # Refusing here meant a booking sold entirely by per-screen days had to carry a
            # separate end date, and nothing kept the two agreeing.
            longest = max((t.days for t in self.targets if t.days), default=None)
            if longest is None:
                raise ValueError(
                    "give an end date, a plan to take the duration from, "
                    "or per-location days to take it from"
                )
            self.ends_at = self.starts_at + timedelta(days=longest)
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("the end date must be after the start date")
        if not self.advertiser and self.client_id is None:
            raise ValueError("a booking needs either a client or an advertiser name")
        return self


class PlacementUpdate(BaseModel):
    advertiser: Optional[str] = Field(default=None, min_length=1, max_length=200)
    client_id: Optional[int] = None
    plan_id: Optional[int] = None
    price_paise: Optional[int] = Field(default=None, ge=0)
    is_paid: Optional[bool] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    notes: Optional[str] = None


class PlacementSplit(BaseModel):
    """Screens to drop when converting a group booking into per-screen bookings."""

    exclude_screen_ids: List[int] = []


class PlacementTargetResponse(BaseModel):
    id: int
    screen_id: Optional[int]
    group_id: Optional[int]
    name: str
    location: Optional[str] = None
    kind: Literal["screen", "group"]
    # False when the playlist item was deleted by hand on the screen page: the deal is
    # still recorded, it just is not on air there any more.
    is_placed: bool
    # This location's own window, when it was sold one. Null means it follows the booking.
    # Reported so the dashboard can show "Airport TV — 50 days" beside a 30-day campaign
    # rather than making the operator infer it from dates.
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    days: Optional[int] = None


class PlanOption(BaseModel):
    """One plan a booking could move to, and what moving would mean."""
    plan: "TenantPlanResponse"
    is_current: bool
    # Whether it covers the screens this booking ALREADY runs on. A plan that does not fit
    # is still listed -- the operator may intend to drop a screen -- but it cannot be
    # applied without the cap refusing it, so the UI can say why up front.
    fits: bool
    # No "price difference" and no "extra days". Changing a plan replaces the price outright
    # and moves no dates, so a difference is not a thing that gets billed -- quoting one was
    # what framed a correction as an upsell. The plan's own price and duration are on `plan`.
    recommended: bool = False


class PlanChange(BaseModel):
    """Move a booking onto a different plan, or off one onto a custom price.

    A change REPLACES. The booking's plan and price become what is named here; nothing is
    billed on top and no date moves. Selling the client more time is a different sale and
    goes through `PlacementExtension`.

    This used to charge the price *difference* as an extension and push the end date out by
    the new plan's length, so correcting a booking onto the right plan quietly became an
    upsell: the client's total read old price + difference over a run nobody had agreed.
    """
    # Nullable so a booking can come OFF a package onto a negotiated price -- a real
    # correction that had no route: the endpoint refused a null and the dashboard therefore
    # offered no way back to a custom sale.
    plan_id: Optional[int] = None
    # The booking's new TOTAL price, replacing what it cost. Negotiable, because that is how
    # these are actually sold -- tenant and client agree a figure and the tenant fixes it.
    # Omitted means the plan's list price, or, moving to custom, whatever it already costs.
    price_paise: Optional[int] = Field(default=None, ge=0)
    # The custom screen cap, re-cut here because a renegotiation is exactly when it moves --
    # "same money, one more screen". Only meaningful off a package; omitted leaves it alone,
    # and 0 lifts it. A change that would leave the booking over the new cap is refused.
    max_locations: Optional[int] = Field(default=None, ge=0, le=10000)


# Validated here rather than as a database enum so adding one is a deploy, not a migration.
PAYMENT_METHODS = ("cash", "upi", "bank_transfer", "cheque", "card", "other")


class PaymentWrite(BaseModel):
    """One receipt against a booking -- not the booking's whole settlement.

    Posting this ADDS to what the client has paid. A second instalment is a second call.
    """
    # gt, not ge. A receipt for nothing is a mis-click: it records no money and yet moves
    # the booking into "part paid", which reads worse than the unpaid it really is.
    amount_paise: int = Field(gt=0)
    method: str
    reference: Optional[str] = Field(default=None, max_length=80)
    # Defaults to now in the router. Backdating matters: a cheque banked on Friday and
    # entered on Monday was received on Friday.
    paid_at: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("method")
    @classmethod
    def known_method(cls, value: str) -> str:
        normalised = (value or "").strip().lower()
        if normalised not in PAYMENT_METHODS:
            raise ValueError(f"Payment method must be one of: {', '.join(PAYMENT_METHODS)}")
        return normalised


class PaymentEdit(BaseModel):
    """Corrections to a receipt already recorded: a mistyped amount, the wrong method, a
    missing UTR, the date it actually landed.

    Every field optional -- a PATCH states only what changed. Correcting one used to mean
    deleting the receipt and retyping it whole, so an operator re-entered a row that was
    mostly right and lost the record of who had taken it.
    """
    amount_paise: Optional[int] = Field(default=None, gt=0)
    method: Optional[str] = None
    reference: Optional[str] = Field(default=None, max_length=80)
    paid_at: Optional[datetime] = None
    notes: Optional[str] = None

    @field_validator("method")
    @classmethod
    def known_method(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        normalised = value.strip().lower()
        if normalised not in PAYMENT_METHODS:
            raise ValueError(f"Payment method must be one of: {', '.join(PAYMENT_METHODS)}")
        return normalised


class PaymentResponse(BaseModel):
    id: int
    amount_paise: int
    method: str
    reference: Optional[str] = None
    paid_at: datetime
    notes: Optional[str] = None
    recorded_by: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PlacementResponse(BaseModel):
    id: int
    content_id: int
    creative_name: Optional[str] = None
    creative_thumbnail_url: Optional[str] = None
    advertiser: str
    client: Optional[ClientResponse] = None
    plan: Optional[TenantPlanResponse] = None
    extensions: List[ExtensionResponse] = []
    # starts_at/ends_at stay as SOLD; this is where the run actually finishes once
    # extensions are counted, and it is what the dashboard should show as "ends".
    effective_ends_at: Optional[datetime] = None
    days_remaining: Optional[int] = None
    total_price_paise: Optional[int] = None
    price_paise: int
    is_paid: bool
    # The money, derived once on the server (see placements.settlement) instead of being
    # re-subtracted by every page that shows it. `amount_paid_paise` is what the client has
    # actually handed over, `balance_due_paise` what is still outstanding against the TOTAL
    # -- extensions and upgrades included -- and `payment_status` the one word for it.
    amount_paid_paise: int = 0
    balance_due_paise: int = 0
    payment_status: Literal["unpaid", "part_paid", "paid"] = "unpaid"
    starts_at: datetime
    ends_at: datetime
    notes: Optional[str] = None
    created_at: datetime
    targets: List[PlacementTargetResponse] = []
    # How much of the plan this booking is actually delivering, groups expanded. Over is a
    # 409 and never reaches here; UNDER is reported, because a client paying for five
    # screens and running on three is owed two and nothing was telling anyone.
    # `plan_max_locations` is the cap in force -- the package's, or the booking's own when
    # it is sold off a package -- and 0 when nothing caps it. Kept under that name because
    # four dashboard views already read it; `plan` above says which of the two it came from.
    screens_used: int = 0
    plan_max_locations: int = 0
    screens_unused: int = 0
    # The booking's OWN cap as stored, so "Change plan" can show the number back for editing
    # rather than inferring it from a field that may be quoting the package instead.
    max_locations: int = 0
    # How the booking was settled. `is_paid` above says whether; these say what, when and
    # by which method, oldest first, and the list is empty until someone records one.
    # A list because clients pay deposits: `amount_paid_paise` is their sum.
    payments: List[PaymentResponse] = []


class ResolveLinkRequest(BaseModel):
    """A Google Maps share link, pasted by an operator."""
    link: str


class ResolveLinkResponse(BaseModel):
    latitude: float
    longitude: float
    name: Optional[str] = None


AlertSeverity = Literal["critical", "warning"]


class AlertResponse(BaseModel):
    id: int
    kind: str
    severity: AlertSeverity
    title: str
    detail: Optional[str] = None
    screen_id: Optional[int] = None
    content_id: Optional[int] = None
    raised_at: datetime
    resolved_at: Optional[datetime] = None
    acknowledged_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class AlertSummaryResponse(BaseModel):
    """Counts for the header badge, so it does not have to fetch every alert."""

    total: int
    critical: int
    warning: int
    unacknowledged: int


# ScreenResponse is defined below the Google models that reference it, so the forward
# reference is resolved here rather than left to be resolved on first use.
GoogleDevicePollResponse.model_rebuild()
