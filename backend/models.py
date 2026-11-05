import secrets
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, Time, UniqueConstraint, JSON, Float, case, func, select
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import relationship, backref
from sqlalchemy.types import TypeDecorator

from .database import Base
from .pin_crypto import encrypt_pin


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


# Ceiling on any walk up ScreenGroup.parent_id. Cycles are rejected on write, so this
# only ever bounds rows that predate that check -- but a walk with no ceiling is an
# infinite loop holding a database connection, so it keeps one.
MAX_GROUP_DEPTH = 32


class UtcDateTime(TypeDecorator):
    """A DateTime that always reads back as timezone-aware UTC.

    Postgres with timezone=True already does this, but SQLite silently drops the
    tzinfo, so the same row served from a dev database came back naive and every
    JSON client re-read it as *local* time. In IST that shifted last_seen by
    5h30m, which made healthy screens look hours stale. Normalising on read keeps
    SQLite and Postgres serving identical instants.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        # SQLite stores the naive part, so convert to UTC first rather than
        # letting a non-UTC offset be truncated into the wrong wall time.
        if isinstance(value, datetime) and value.tzinfo is not None:
            return value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class Plan(Base):
    __tablename__ = "plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, index=True, nullable=False)
    monthly_price_paise = Column(Integer, nullable=False, default=0)
    yearly_price_paise = Column(Integer, nullable=False, default=0)
    # NULL = no screen limit. 0 stays a real limit of zero, so a package that grants no
    # screens is still expressible -- unlike max_ad_slots and max_clients below, where 0 IS
    # the unlimited marker. The asymmetry is deliberate and the reason NULL had to be added:
    # there was previously no value meaning "unlimited screens" at all.
    max_screens = Column(Integer, nullable=True)
    # 0 = unlimited, matching max_ad_slots and max_clients rather than meaning "no storage",
    # which no package has ever wanted and which would refuse every upload.
    max_storage_bytes = Column(BigInteger, nullable=False)
    # 0 = unlimited, matching Organization.max_ad_slots. A package carries the default;
    # Organization.max_ad_slots overrides it for one tenant without editing the package.
    max_ad_slots = Column(Integer, nullable=False, default=0, server_default="0")
    # Advertisers (Client rows) this package lets the tenant keep. 0 = unlimited, matching
    # every other cap here.
    max_clients = Column(Integer, nullable=False, default=0, server_default="0")
    # The storefront sells access as a ONE-TIME charge for a fixed window, not a
    # monthly/yearly subscription: `price_paise` buys `duration_days` days. The older
    # recurring columns (monthly/yearly_price_paise) stay for the subscription checkout that
    # predates this, but the storefront and the one-time order path read these two.
    # duration_days backfills to 30 so an existing package keeps a sane window.
    price_paise = Column(BigInteger, nullable=False, default=0, server_default="0")
    duration_days = Column(Integer, nullable=False, default=30, server_default="30")
    feature_flags_json = Column(Text, nullable=False, default="{}")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)

    organizations = relationship("Organization", back_populates="plan")
    subscriptions = relationship("Subscription", back_populates="plan")


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    slug = Column(String, unique=True, index=True, nullable=False)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=True)
    status = Column(String, nullable=False, default="active") # pending_approval, active, suspended, rejected
    approved_at = Column(UtcDateTime, nullable=True)
    approved_by_user_id = Column(Integer, nullable=True)
    rejection_reason = Column(String, nullable=True)
    # Removed by an admin, and purged for good 30 days later by purge_removed_tenants.
    # Distinct from status="suspended", which blocks access and changes nothing else: this
    # is a countdown to destruction, and the window exists so it can be undone.
    deleted_at = Column(UtcDateTime, nullable=True, index=True)
    storage_quota_bytes = Column(BigInteger, nullable=False, default=10 * 1024 * 1024 * 1024)
    # Per-tenant OVERRIDES set by Super Admin. 0 = "no override, use the package".
    # Read these through effective_max_screens / effective_max_ad_slots rather than
    # directly; on their own they say nothing about what the tenant is actually allowed.
    max_screens = Column(Integer, nullable=False, default=0)
    max_ad_slots = Column(Integer, nullable=False, default=0)
    max_clients = Column(Integer, nullable=False, default=0, server_default="0")

    # What a CLIENT sees at the top of their campaign report. `name` is the workspace name
    # an operator picked when signing up ("Roshan's Workspace"); it is not necessarily the
    # trading name they want printed on a document they hand to an advertiser. Null falls
    # back to `name`, so a tenant that sets none loses nothing.
    brand_name = Column(String, nullable=True)
    # Stored like every other asset: "s3://<key>" or "/uploads/<path>", resolved on read by
    # media_urls.resolve_media_url. Never an absolute URL baked in at upload time -- that is
    # what left every media row pointing at a stale localhost.
    logo_url = Column(String, nullable=True)
    brand_color = Column(String(9), nullable=True)

    # One PIN for the whole fleet, set by an owner on the Screens page. Null means no
    # workspace PIN has been set, so every screen keeps using its own -- today's only
    # behaviour, unchanged until an owner opts into a shared one. A screen can still opt
    # out per-TV (Screen.uses_own_pin) for a panel that needs a different code, a lobby
    # display a cleaner also has to open, say, without handing them the whole fleet's PIN.
    lock_pin = Column(String(255), nullable=True)
    credentials_version = Column(Integer, nullable=False, default=0, server_default="0")
    # The tenant's own on/off switch for the lock -- separate from whether their PLAN
    # includes the feature at all (plan_features(plan)["kiosk_lock"]). Defaults off: a
    # tenant whose plan just started selling TV lock is not retroactively trapped on
    # every paired screen the moment an admin flips their plan; they turn it on when
    # they're ready. The effective state a TV actually enforces is both of these ANDed
    # together -- see routers/screens.kiosk_lock_enabled.
    lock_enabled = Column(Boolean, nullable=False, default=False, server_default="false")

    created_at = Column(UtcDateTime, nullable=False, default=utcnow)

    users = relationship("User", back_populates="organization")

    @property
    def effective_max_screens(self) -> int | None:
        """Screens this tenant may actually have. None means no limit.

        None rather than 0 for "unlimited", because 0 already means something else on each
        of the two columns and they disagree: `Organization.max_screens = 0` is "no
        override set", while `Plan.max_screens = 0` is a real limit of zero screens -- a
        package that grants none. Collapsing both to 0 turned a zero-screen package into an
        unlimited one, which test_tenant_isolation catches by enrolling against exactly
        that plan.

        The one number that answers the question, because the two columns behind it
        disagreed constantly. `max_screens` is an override that only `_apply_plan` ever
        writes, while THREE other paths set `plan_id` without it -- the free-plan backfill
        in billing.ensure_billing_catalog, a self-serve plan change at checkout, and
        approving a tenant with no package chosen. In production every organisation ended
        up with a plan and `max_screens = 0`.

        That split had the enforced limit and the displayed limit reading different
        fields: the admin tenants list showed `max_screens` (0, rendered as an infinity
        sign) while ensure_screen_quota silently fell through to the package's number. An
        operator who set a 5-screen package saw no limit in the console and got whatever
        the package said -- or, for a tenant approved with no package at all, no limit at
        all.

        Derived rather than copied so it cannot drift again: editing a package now moves
        every tenant on it, and an override still wins where one is set.
        """
        if self.max_screens and self.max_screens > 0:
            return self.max_screens
        if self.plan is not None:
            # Including 0: a package may legitimately grant no screens at all. A negative value
            # is the API's "unlimited" marker that an older save could leave on the row; it
            # is read as no limit rather than as a cap every screen exceeds.
            limit = self.plan.max_screens
            return None if limit is not None and limit < 0 else limit
        return None

    @property
    def effective_max_ad_slots(self) -> int | None:
        """Ad slots this tenant may actually sell. None (or 0) means unlimited."""
        if self.max_ad_slots and self.max_ad_slots > 0:
            return self.max_ad_slots
        if self.plan is not None and self.plan.max_ad_slots and self.plan.max_ad_slots > 0:
            return self.plan.max_ad_slots
        return None

    @property
    def effective_max_clients(self) -> int | None:
        """Advertisers this tenant may keep. None (or 0) means unlimited.

        Same override-then-package derivation as effective_max_screens, so the enforced
        limit and the one the console shows can never read different columns.
        """
        if self.max_clients and self.max_clients > 0:
            return self.max_clients
        if self.plan is not None and self.plan.max_clients and self.plan.max_clients > 0:
            return self.plan.max_clients
        return None

    @property
    def owner_email(self) -> str | None:
        """Address that names this tenant's storage folder. See media_urls.storage_prefix.

        The owner is the account that created the workspace. Falls back to any member with
        an address, because a workspace seeded by `seed_admin` has an owner with no email
        set and the folder should still be legible.
        """
        members = sorted(self.users, key=lambda user: user.id)
        for candidate in members:
            if user_email := (candidate.email or "").strip():
                if candidate.role == "owner":
                    return user_email
        for candidate in members:
            if user_email := (candidate.email or "").strip():
                return user_email
        return None
    screens = relationship("Screen", back_populates="organization")
    groups = relationship("ScreenGroup", back_populates="organization")
    content = relationship("Content", back_populates="organization")
    playlists = relationship("Playlist", back_populates="organization", cascade="all, delete-orphan")
    plan = relationship("Plan", back_populates="organizations")
    subscription = relationship("Subscription", back_populates="organization", uselist=False)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=False)
    value = Column(Text, nullable=False)
    description = Column(String, nullable=True)
    updated_at = Column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)



class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), unique=True, nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("plans.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="active")
    billing_period = Column(String, nullable=False, default="monthly")
    current_period_start = Column(UtcDateTime, nullable=True)
    current_period_end = Column(UtcDateTime, nullable=True)
    grace_period_end = Column(UtcDateTime, nullable=True)
    provider = Column(String, nullable=True)
    provider_subscription_id = Column(String, unique=True, nullable=True, index=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)
    updated_at = Column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    organization = relationship("Organization", back_populates="subscription")
    plan = relationship("Plan", back_populates="subscriptions")


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String, nullable=False)
    provider_event_id = Column(String, unique=True, nullable=False, index=True)
    event_type = Column(String, nullable=False)
    received_at = Column(UtcDateTime, nullable=False, default=utcnow)


class PlatformPayment(Base):
    """One receipt for what a tenant paid THE PLATFORM -- not to be confused with AdPayment,
    which is what a tenant's own client paid them.

    Written once, by the same shared activation path every purchase already goes through
    (_activate_plan's callers: activate_purchase for a standard or custom one-time buy,
    the subscription.charged webhook branch for a recurring renewal), from the amount the
    PROVIDER reports -- never computed from the plan's list price, which can be stale or
    wrong the moment a price changes. No foreign keys to plan/custom_request: a payment is a
    historical fact and must survive the package or request it was for being edited or
    deleted later.
    """

    __tablename__ = "platform_payments"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    # "plan" (a standard package) | "custom" (a priced custom request) | "subscription" (a
    # recurring renewal charge).
    kind = Column(String(20), nullable=False)
    plan_id = Column(Integer, nullable=True)
    custom_request_id = Column(Integer, nullable=True)
    amount_paise = Column(BigInteger, nullable=False)
    currency = Column(String(8), nullable=False, default="INR")
    provider = Column(String(20), nullable=False)
    provider_order_id = Column(String, nullable=True, index=True)
    provider_payment_id = Column(String, nullable=True)
    status = Column(String(20), nullable=False, default="captured")
    paid_at = Column(UtcDateTime, nullable=False, default=utcnow, index=True)


class CustomPlanRequest(Base):
    """A tenant asking for a bespoke package: the counts they want, and what the operator
    priced it at.

    Standard tiers are bought straight off the shelf. A custom shape cannot be, because only
    the platform operator sets its price -- so it is a small request queue rather than a
    per-org row bolted onto the global `plans` catalogue, which is shared and must not gain a
    tenant-specific entry every time someone asks.

    Lifecycle: 'requested' (tenant submitted) -> 'priced' (operator set price_paise) ->
    'paid' (a one-time order for it was captured) | 'rejected'. On payment the requested caps
    are copied onto the organisation, exactly as approving a tenant does.
    """

    __tablename__ = "custom_plan_requests"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(
        Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    max_screens = Column(Integer, nullable=False, default=0)
    max_clients = Column(Integer, nullable=False, default=0)
    max_ad_slots = Column(Integer, nullable=False, default=0)
    max_storage_bytes = Column(BigInteger, nullable=False, default=10 * 1024 * 1024 * 1024)
    duration_days = Column(Integer, nullable=False, default=30)
    feature_flags_json = Column(Text, nullable=False, default="{}")
    # Smallest currency unit, like every other price here, so money never touches a float.
    # 0 until the operator prices it; `status` carries whether that has happened yet.
    price_paise = Column(BigInteger, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="requested", index=True)
    notes = Column(String, nullable=True)
    # Who to actually reach about this. Nullable: a request made before this column existed,
    # or one the tenant left blank, falls back to whoever owns the organisation already --
    # these never replace that, only add a specific contact when one was given.
    company_name = Column(String, nullable=True)
    contact_name = Column(String, nullable=True)
    contact_email = Column(String, nullable=True)
    contact_phone = Column(String, nullable=True)
    # The one-time order raised to pay for this request, so the webhook can find it back.
    provider_order_id = Column(String, unique=True, nullable=True, index=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)
    updated_at = Column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    organization = relationship("Organization")

    __table_args__ = (
        CheckConstraint(
            "status IN ('requested', 'priced', 'paid', 'rejected')",
            name="ck_custom_plan_requests_status",
        ),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, nullable=False, default="viewer")
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)
    # Profile fields. Nullable because every existing account predates them and username
    # stays the login identifier -- these are display/contact only, so nothing breaks when
    # they are unset.
    full_name = Column(String, nullable=True)
    email = Column(String, unique=True, index=True, nullable=True)
    google_sub = Column(String, unique=True, index=True, nullable=True)
    picture = Column(String, nullable=True)
    auth_provider = Column(String, nullable=False, default="local")
    last_login_at = Column(UtcDateTime, nullable=True)
    token_version = Column(Integer, nullable=False, default=0, server_default="0")

    organization = relationship("Organization", back_populates="users", foreign_keys=[organization_id])

    @property
    def organization_name(self) -> str | None:
        """Exposed so UserResponse can show the tenant by name instead of a bare id."""
        return self.organization.name if self.organization else None

    @property
    def organization_status(self) -> str:
        """The workspace status every access gate reads.

        Removal is reported here rather than checked separately at each gate, because
        `deleted_at` is not a value `status` can hold and every caller that already refuses
        a suspended workspace must refuse a removed one on exactly the same path.
        """
        if not self.organization:
            return "active"
        if self.organization.deleted_at is not None:
            return "removed"
        return self.organization.status


class ScreenGroup(Base):
    __tablename__ = "screen_groups"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    parent_id = Column(Integer, ForeignKey("screen_groups.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String, nullable=False)
    playlist_id = Column(Integer, ForeignKey("playlists.id", ondelete="SET NULL"), nullable=True)
    target_version_code = Column(Integer, ForeignKey("app_releases.version_code", ondelete="SET NULL"), nullable=True)
    is_dynamic = Column(Boolean, nullable=False, default=False, server_default="false")
    dynamic_criteria = Column(JSON, nullable=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)
    updated_at = Column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    playlist = relationship("Playlist", back_populates="groups")
    # serialize_group reports screen_count as len(group.screens), so listing groups loaded
    # every screen of every group one group at a time. Batched, that is one query for the
    # whole page instead of one per row.
    screens = relationship("Screen", back_populates="group", lazy="selectin")
    organization = relationship("Organization", back_populates="groups")
    children = relationship("ScreenGroup", backref=backref('parent', remote_side=[id]))


class Screen(Base):
    __tablename__ = "screens"

    id = Column(Integer, primary_key=True, index=True)
    # Unpaired registrations are deliberately unbound until an authenticated
    # organization claims their pairing code.
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=True, index=True)
    device_id = Column(String, unique=True, index=True, nullable=True)
    device_secret_hash = Column(String, nullable=True)
    # Archived, not deleted. play_logs and play_log_hourly_rollups both carry a NOT NULL
    # foreign key to this row, and the booking report attributes plays to a screen BY NAME
    # -- so removing the row would either fail outright or, with the constraint relaxed,
    # silently drop the evidence an advertiser was billed on. The fleet loses the screen;
    # the history keeps it.
    deleted_at = Column(UtcDateTime, nullable=True, index=True)
    # How strong this screen's hardware identity is: "serial" | "android_id" | "random".
    # Decides whether it can be recognised again after a wipe, so it is reported rather than
    # left as something an operator discovers by finding a duplicate.
    identity_source = Column(String, nullable=True)
    # Whether this panel can install an update without a human confirming it. Android permits
    # a silent install only for the device owner, so this is the single fact that decides
    # whether a published release reaches the screen or sits behind a dialog nobody is
    # standing in front of. NULL until the player reports it.
    device_owner = Column(Boolean, nullable=True)
    installation_id = Column(String, nullable=True)
    pair_code = Column(String, unique=True, index=True, nullable=True)
    pair_code_expires_at = Column(UtcDateTime, nullable=True)
    name = Column(String, nullable=True)
    orientation = Column(Integer, default=0)
    # "auto" = whatever the panel last reported; "manual" = an operator set it and the
    # heartbeat must stop overwriting it. Without this an override silently reverts on the
    # next heartbeat, which reads as "rotation keeps resetting itself".
    orientation_source = Column(String, nullable=False, default="auto")
    description = Column(String, nullable=True)
    # Free-form comma-separated labels, the same shape Content.tags uses.
    tags = Column(String, nullable=True)
    # Where the screen physically is. Stored together with the coordinates that produced
    # it so a report's label and its map pin can never disagree; place_id lets a later
    # lookup re-resolve the same place without re-searching by name.
    location = Column(String, nullable=True, index=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    place_id = Column(String, nullable=True)
    # How the player letterboxes content whose aspect ratio does not match the panel.
    # "contain" shows the whole frame, "cover" fills the panel and crops.
    fit_mode = Column(String, nullable=False, default="contain")
    # Gate on the player's on-TV maintenance screen, reached by a remote key sequence.
    # Per screen rather than per org so one leaked pin does not open the whole fleet;
    # the player caches it locally because the screen exists to fix connectivity and
    # must therefore work with the server unreachable.
    maintenance_pin = Column(
        String(255), nullable=False, default=lambda: encrypt_pin(f"{secrets.randbelow(10000):04d}")
    )
    synced_credentials_version = Column(Integer, nullable=False, default=0, server_default="0")
    # True keeps this one screen on its own maintenance_pin even when the workspace has set
    # a shared lock PIN -- see effective_maintenance_pin, the only place that reads this
    # alongside Organization.lock_pin. Defaults false so setting a workspace PIN immediately
    # covers every existing screen, which is the point of having one.
    uses_own_pin = Column(Boolean, nullable=False, default=False, server_default="false")
    # Screen-level kiosk lock state (True = locked, False = unlocked, None = inherit org.lock_enabled)
    is_locked = Column(Boolean, nullable=True, default=None)
    # Video-wall support: followers take their playback clock from a leader screen.
    sync_playback = Column(Boolean, nullable=False, default=False)
    sync_role = Column(String, nullable=False, default="leader")
    leader_screen_id = Column(Integer, ForeignKey("screens.id", ondelete="SET NULL"), nullable=True)
    # When an operator let this screen into the fleet. NULL means it has claimed an
    # organisation but nobody has confirmed it yet, so it syncs nothing.
    #
    # Its own column rather than a `status` value: status is rewritten to "online" by
    # every heartbeat (see the heartbeat route), so an approval state parked there would
    # be erased within a minute of the screen powering on.
    #
    # Only the self-service routes leave this NULL. /pair and /enroll already prove intent
    # -- one needs an operator at the dashboard, the other a token an operator issued --
    # so re-confirming them would be a second lock on the same door.
    approved_at = Column(UtcDateTime, nullable=True)
    # Mon..Sun operating windows as {"mon": ["00:00", "23:59"], ...}; null means always on.
    operating_hours = Column(JSON, nullable=True)
    # "always" | "hours" | "never" — kept separate so clearing the schedule does not
    # lose the windows an operator already typed in.
    operating_mode = Column(String, nullable=False, default="always")
    status = Column(String, default="offline")
    last_seen = Column(UtcDateTime, nullable=False, default=utcnow, index=True)
    device_version = Column(String, nullable=True)
    app_version = Column(String, nullable=True)
    storage_used = Column(String, nullable=True)
    
    # TV Capabilities
    screen_width = Column(Integer, nullable=True)
    screen_height = Column(Integer, nullable=True)
    refresh_rate = Column(Integer, nullable=True) # Integer, or should we use Float? The spec says Float in Kotlin. We can use Float in DB or just Integer for simplicity. Let's use sqlalchemy.Float
    total_ram_mb = Column(Integer, nullable=True)
    available_ram_mb = Column(Integer, nullable=True)
    total_storage_mb = Column(Integer, nullable=True)
    free_storage_mb = Column(Integer, nullable=True)
    supported_video_codecs = Column(JSON, nullable=True)
    max_decode_width = Column(Integer, nullable=True)
    max_decode_height = Column(Integer, nullable=True)
    manufacturer = Column(String, nullable=True)
    model = Column(String, nullable=True)
    android_version = Column(String, nullable=True)
    sdk_int = Column(Integer, nullable=True)
    network_type = Column(String, nullable=True)
    timezone = Column(String, nullable=True)
    
    playback_state = Column(String, nullable=False, default="idle")
    current_item_id = Column(Integer, nullable=True)
    last_error = Column(String, nullable=True)
    last_error_at = Column(UtcDateTime, nullable=True)
    playlist_id = Column(Integer, ForeignKey("playlists.id", ondelete="SET NULL"), nullable=True)
    group_id = Column(Integer, ForeignKey("screen_groups.id", ondelete="SET NULL"), nullable=True)
    assignment_updated_at = Column(UtcDateTime, nullable=False, default=utcnow)
    
    target_version_code = Column(Integer, ForeignKey("app_releases.version_code", ondelete="SET NULL"), nullable=True)
    update_status = Column(String, nullable=True) # pending, downloading, installing, success, failed, rolled_back
    # Consecutive failed install attempts of target_version_code. Reset on success and on
    # every re-pin. Once it reaches rollout.ROLLBACK_THRESHOLD the pin is dropped, which
    # is the automatic rollback the fleet-operations spec asks for: without this a screen
    # retried a build that could never install, forever, on every heartbeat.
    update_failure_count = Column(Integer, nullable=False, default=0)

    playlist = relationship("Playlist", back_populates="screens")
    group = relationship("ScreenGroup", back_populates="screens")
    organization = relationship("Organization", back_populates="screens")

    def resolve_playlist_id(self, groups=None):
        """The playlist this screen is configured to play, or None.

        Its own assignment wins, then the nearest ancestor group that has one, then the
        first dynamic group whose criteria it matches. Emergency broadcasts outrank all
        of it, but they are a temporary takeover rather than configuration, so the one
        caller that cares (the player sync) layers them on top of this.

        `groups` is the organization's groups as {id: ScreenGroup}. Pass it whenever
        more than one screen is being resolved: without it every ancestor hop is a lazy
        load, which on a fleet listing is a query per screen per level. It is also the
        only way dynamic groups can be considered at all, since nothing links one to a
        screen to be walked.
        """
        if self.playlist_id:
            return self.playlist_id

        # Bounded: parent_id is operator-settable, so a cycle (A -> B -> A) would spin
        # here forever holding a connection. routers/groups.py rejects cycles on write;
        # this is the backstop for rows that predate that check.
        group = groups.get(self.group_id) if groups is not None else self.group
        for _ in range(MAX_GROUP_DEPTH):
            if group is None:
                break
            if group.playlist_id:
                return group.playlist_id
            group = groups.get(group.parent_id) if groups is not None else group.parent

        for group in (groups or {}).values():
            # A caller resolving a whole fleet may pass several tenants' groups at once
            # (a platform operator listing every screen), and a dynamic group must never
            # reach outside its own organization.
            if group.organization_id != self.organization_id:
                continue
            if not (group.is_dynamic and group.playlist_id and group.dynamic_criteria):
                continue
            if all(getattr(self, key, None) == value for key, value in group.dynamic_criteria.items()):
                return group.playlist_id

        return None

    @property
    def effective_playlist_id(self):
        """resolve_playlist_id for a screen being serialized on its own.

        Sees the screen's own assignment and its ancestor groups, but not dynamic groups:
        those need the organization's group set, and querying for it from a serialized
        property would be one query per screen on every list endpoint. Endpoints that
        serialize a fleet resolve with the map and override this field.
        """
        return self.resolve_playlist_id()


class AppRelease(Base):
    __tablename__ = "app_releases"

    id = Column(Integer, primary_key=True, index=True)
    version_code = Column(Integer, unique=True, index=True, nullable=False)
    version_name = Column(String(50), nullable=False)
    apk_url = Column(String(2048), nullable=False)
    sha256 = Column(String(64), nullable=True)
    mandatory = Column(Boolean, nullable=False, default=False)
    # "draft" | "canary" | "released". This is what makes a staged rollout possible.
    #
    # The global fallback in current_app_version hands every unpinned screen the highest
    # version_code it can find. While every release was implicitly live, publishing one
    # *was* shipping it to the entire fleet, so a canary ring could not exist: the 5 test
    # TVs and the other 495 were offered the same build the moment it was created.
    #
    # Only "released" rows are eligible for that fallback. A draft or canary build reaches
    # a screen solely through an explicit target_version_code pin, which is how a ring is
    # built -- 5 screens, then 20, then promote to "released" for the rest.
    rollout_state = Column(String, nullable=False, default="draft", index=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint(
            "rollout_state IN ('draft', 'canary', 'released')",
            name="ck_app_releases_rollout_state",
        ),
    )


class Campaign(Base):
    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)
    updated_at = Column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    organization = relationship("Organization")
    playlists = relationship("Playlist", back_populates="campaign")


class Content(Base):
    __tablename__ = "content"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    type = Column(String)
    file_url = Column(String)
    thumbnail = Column(String, nullable=True)
    name = Column(String)
    tags = Column(String, nullable=True)
    file_size_bytes = Column(BigInteger, nullable=False, default=0)
    sha256 = Column(String(64), nullable=True)
    # Real length of the source video, filled in by the worker from ffprobe. Without it
    # a playlist item falls back to a flat 10 seconds, so a 30-second advert is cut off
    # at 10 on every screen. Null for images, which have no intrinsic duration.
    duration_ms = Column(Integer, nullable=True)
    uploaded_at = Column(UtcDateTime, default=utcnow)
    status = Column(String, nullable=False, default="processing")
    # When the current processing attempt began. The reaper uses this to find rows whose
    # worker died mid-job; without it a killed worker leaves a permanent spinner.
    processing_started_at = Column(UtcDateTime, nullable=True)
    processing_retries = Column(Integer, nullable=False, default=0)
    failed_reason = Column(String, nullable=True)

    # Both of these are eager on purpose, and it is the single biggest thing separating a
    # fast library page from a slow one.
    #
    # ContentResponse serialises `renditions` directly and `expires_at` below, which walks
    # `playlist_items`. Left lazy, building one response therefore cost TWO extra queries
    # per row -- so GET /api/content/ ran 53 queries for 25 items instead of 3. On a
    # co-located database that is invisible; against a managed one a region away at ~114 ms
    # a round trip it is six seconds on the page /dashboard redirects to.
    #
    # Set here rather than with .options(selectinload(...)) at each call site because the
    # cost is created by the SCHEMA, not by any one route: the playlist editor and the TV's
    # /sync both embed ContentResponse too, and each would have had to remember the same
    # hint. "selectin" issues one extra IN query per batch of parents no matter how many
    # there are, so this is O(1) queries, not O(rows).
    # Deliberately NOT eager, unlike renditions. PlaylistItem.content is eager, so making
    # this eager too closes a cycle (item -> content -> items) that SQLAlchemy resolves by
    # falling back to one query per parent -- which put the per-row cost straight back into
    # the playlist editor. The library route, which is the one that needs expires_at for a
    # whole page, asks for it explicitly with selectinload instead.
    playlist_items = relationship("PlaylistItem", back_populates="content")
    organization = relationship("Organization", back_populates="content")
    renditions = relationship(
        "MediaRendition", back_populates="content", cascade="all, delete-orphan",
        lazy="selectin",
    )
    ad_placements = relationship(
        "AdPlacement", back_populates="content", cascade="all, delete-orphan",
        lazy="selectin",
    )

    @property
    def expires_at(self):
        expiries = [item.end_at for item in self.playlist_items if item.end_at]
        return min(expiries) if expiries else None


class MediaRendition(Base):
    __tablename__ = "media_renditions"

    id = Column(Integer, primary_key=True, index=True)
    content_id = Column(Integer, ForeignKey("content.id", ondelete="CASCADE"), nullable=False, index=True)
    resolution = Column(String, nullable=False)
    width = Column(Integer, nullable=False)
    height = Column(Integer, nullable=False)
    rotation = Column(Integer, nullable=False, default=0)
    duration_ms = Column(Integer, nullable=True)
    codec = Column(String, nullable=True)
    sha256 = Column(String(64), nullable=True)
    file_size_bytes = Column(BigInteger, nullable=False, default=0)
    file_url = Column(String, nullable=False)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)

    content = relationship("Content", back_populates="renditions")


class Playlist(Base):
    __tablename__ = "playlists"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True)
    name = Column(String, index=True)
    default_transition = Column(String, nullable=False, default="fade")
    default_transition_ms = Column(Integer, nullable=False, default=600)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)
    updated_at = Column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    screens = relationship("Screen", back_populates="playlist")
    groups = relationship("ScreenGroup", back_populates="playlist")
    organization = relationship("Organization", back_populates="playlists")
    campaign = relationship("Campaign", back_populates="playlists")
    items = relationship(
        "PlaylistItem",
        back_populates="playlist",
        order_by="PlaylistItem.order",
        cascade="all, delete-orphan",
        # PlaylistResponse always serialises its items, so a lazy load here was one query
        # per playlist and nothing ever wanted a playlist without them.
        lazy="selectin",
    )


class PlaylistItem(Base):
    __tablename__ = "playlist_items"

    id = Column(Integer, primary_key=True, index=True)
    playlist_id = Column(Integer, ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False)
    content_id = Column(Integer, ForeignKey("content.id", ondelete="CASCADE"), nullable=False)
    duration = Column(Integer, default=10)
    order = Column(Integer, default=0)
    start_at = Column(UtcDateTime, nullable=True)
    end_at = Column(UtcDateTime, nullable=True)
    # NULL means inherit the containing playlist's transition setting.
    transition = Column(String, nullable=True)
    transition_ms = Column(Integer, nullable=True)
    # Per-item rotation override in degrees (0/90/180/270). Null means "follow the
    # screen", which is what almost every item should be.
    rotation = Column(Integer, nullable=True)

    playlist = relationship("Playlist", back_populates="items")
    # The item IS the content as far as every response is concerned -- PlaylistItemResponse
    # embeds a full ContentResponse -- so this was loaded one row at a time, and each of
    # those loads then triggered Content's own two. That compounding is why the playlist
    # editor cost 244 queries for 60 items where the library cost 5: four per row rather
    # than one. Batching here is what lets Content's selectin loaders batch as well.
    content = relationship("Content", back_populates="playlist_items", lazy="selectin")
    schedule = relationship(
        "Schedule",
        back_populates="playlist_item",
        uselist=False,
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class EmergencyBroadcast(Base):
    __tablename__ = "emergency_broadcasts"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    target_type = Column(String, nullable=False)  # 'all', 'group', 'screen'
    target_id = Column(Integer, nullable=True)
    playlist_id = Column(Integer, ForeignKey("playlists.id", ondelete="CASCADE"), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)
    updated_at = Column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    organization = relationship("Organization")
    playlist = relationship("Playlist")


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, index=True)
    playlist_item_id = Column(
        Integer,
        ForeignKey("playlist_items.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    days_of_week = Column(String, nullable=True)
    start_time = Column(Time, nullable=True)
    end_time = Column(Time, nullable=True)

    playlist_item = relationship("PlaylistItem", back_populates="schedule")


class EnrollmentToken(Base):
    __tablename__ = "enrollment_tokens"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    token = Column(String, unique=True, index=True, nullable=False)
    description = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    expires_at = Column(UtcDateTime, nullable=True)
    max_uses = Column(Integer, nullable=True)
    use_count = Column(Integer, nullable=False, default=0)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)

    organization = relationship("Organization")


class Client(Base):
    """The advertiser a booking is sold to, and how to reach them.

    Bookings carried the client as a free-text `advertiser` string, which was enough to
    label a row and nothing else: the same customer spelled two ways became two customers,
    there was nowhere to keep the address a report has to be emailed to, and "everything we
    ran for this client" could not be asked at all.

    Tenant-scoped, because one tenant's client list is not another's -- two tenants may
    legitimately both sell to "BrightMart" and neither should see the other's record.
    """

    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    # Shown on the report as the client's reference. Unique per tenant rather than
    # globally: it is a label for the customer's own filing, not a system identifier.
    client_code = Column(String(20), nullable=False)
    email = Column(String, nullable=True)
    phone = Column(String(40), nullable=True)
    notes = Column(String, nullable=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)
    updated_at = Column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    organization = relationship("Organization")

    __table_args__ = (
        UniqueConstraint("organization_id", "client_code", name="uq_clients_org_code"),
    )


class TenantPlan(Base):
    """A package a tenant sells to its own clients.

    Distinct from `Plan`, which is what MR Signage bills the TENANT. This is the other side of
    the business: what the tenant in turn sells to an advertiser. Sharing one table would
    have let a tenant edit the plan they are billed on, and shown MR Signage's pricing to their
    customers.

    A booking COPIES price and duration from here rather than reading through to it, so
    repricing a plan never changes what a client was already billed. See routers/placements.
    """

    __tablename__ = "tenant_plans"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    duration_days = Column(Integer, nullable=False, default=30)
    max_locations = Column(Integer, nullable=False, default=1)
    ad_slots = Column(Integer, nullable=False, default=1)
    price_paise = Column(BigInteger, nullable=False, default=0)
    support_tier = Column(String(40), nullable=False, default="Basic Support")
    # Retired rather than deleted: a plan that has been sold must keep resolving for the
    # bookings that name it, so the list hides it instead of removing the row.
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)
    updated_at = Column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    organization = relationship("Organization")


class AdPlacement(Base):
    """An advert sold to a client: what runs, for whom, when, and for how much.

    Deliberately a thin record. It does not schedule anything itself — it owns the
    playlist items it created (see AdPlacementTarget), so the playlist stays the single
    thing the player, proof-of-play and rendition selection all read from. Two schedulers
    would mean two answers to "what is on that screen right now".
    """

    __tablename__ = "ad_placements"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    content_id = Column(Integer, ForeignKey("content.id", ondelete="CASCADE"), nullable=False, index=True)
    # Kept alongside client_id, not replaced by it. It is NOT NULL on every existing row,
    # and the report still has to name somebody when a booking predates the clients table
    # or its client was removed. Written from the client on save, so the two agree.
    advertiser = Column(String, nullable=False)
    client_id = Column(Integer, ForeignKey("clients.id", ondelete="SET NULL"), nullable=True, index=True)
    # SET NULL for the same reason: deleting a plan must not delete the bookings sold on
    # it. The commercial terms were copied onto the booking anyway.
    plan_id = Column(Integer, ForeignKey("tenant_plans.id", ondelete="SET NULL"), nullable=True, index=True)
    # How many screens this booking may cover when it is NOT on a package. 0 means no cap,
    # which is what every booking sold before this existed carries.
    #
    # A package states its own limit and wins, so this is only read for a custom sale --
    # which until now was uncapped by definition. "Three screens, negotiated price" was a
    # deal the tenant could strike and nothing could hold them to: the fourth screen went on
    # from the booking page, the screen page or the playlist, and the client received more
    # than they paid for with nobody able to see it. See placements.location_cap.
    max_locations = Column(Integer, nullable=False, default=0, server_default="0")
    # Stored in the smallest currency unit so money never touches a float.
    price_paise = Column(BigInteger, nullable=False, default=0)
    is_paid = Column(Boolean, nullable=False, default=False)
    starts_at = Column(UtcDateTime, nullable=False)
    ends_at = Column(UtcDateTime, nullable=False)
    notes = Column(String, nullable=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)
    updated_at = Column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    @hybrid_property
    def effective_ends_at(self):
        """When the run actually finishes, extensions and per-location windows counted.

        starts_at/ends_at stay as SOLD -- they are the original deal and an invoice should
        still be able to show it. On the model rather than in one router because the alert
        that warns "this campaign ends soon" has to agree with the report about when that
        is; reading `ends_at` there would have warned about an extended campaign on its
        original date and then never again.
        """
        latest = self.ends_at
        for extension in self.extensions:
            if extension.extended_to > latest:
                latest = extension.extended_to
        # A location may be sold a longer run than the booking's own window -- 50 days at
        # an airport on a 30-day campaign. The campaign is not over until its last location
        # is, or the "ending soon" alert fires while a screen is still contractually
        # playing and the report closes the campaign early.
        for target in self.targets:
            if target.ends_at is not None and target.ends_at > latest:
                latest = target.ends_at
        return latest

    @effective_ends_at.expression
    def effective_ends_at(cls):
        """The same answer, computed in SQL.

        Without this it was a plain property, so every DB-level "is this still running"
        filter had to fall back to the raw `ends_at` column -- and each of them then
        under-counted a booking kept alive by an extension or by a longer per-location
        window. The ad-slot quota freed a slot that was still occupied, the alert
        reconciler stopped loading a campaign that was still running, and the expiry sweep
        could not filter in SQL at all and walked every target ever created instead.

        Built from nested CASE rather than GREATEST: GREATEST is Postgres-only and the
        in-process suite runs on SQLite, so the one place this could silently diverge
        between test and production is exactly the place not to use it.
        """
        latest_extension = (
            select(func.max(AdPlacementExtension.extended_to))
            .where(AdPlacementExtension.placement_id == cls.id)
            .correlate(cls)
            .scalar_subquery()
        )
        latest_target = (
            select(func.max(AdPlacementTarget.ends_at))
            .where(AdPlacementTarget.placement_id == cls.id)
            .correlate(cls)
            .scalar_subquery()
        )
        # coalesce first: a booking with no extensions and no per-location window must fall
        # back to its own end, not to NULL, or every comparison against it goes unknown and
        # the row silently vanishes from the filter.
        by_extension = func.coalesce(latest_extension, cls.ends_at)
        by_target = func.coalesce(latest_target, cls.ends_at)
        later_of_the_two = case((by_extension > by_target, by_extension), else_=by_target)
        return case((later_of_the_two > cls.ends_at, later_of_the_two), else_=cls.ends_at)

    content = relationship("Content", back_populates="ad_placements")
    organization = relationship("Organization")
    client = relationship("Client")
    plan = relationship("TenantPlan")
    targets = relationship("AdPlacementTarget", back_populates="placement", cascade="all, delete-orphan")
    extensions = relationship(
        "AdPlacementExtension",
        back_populates="placement",
        cascade="all, delete-orphan",
        order_by="AdPlacementExtension.extended_from",
    )
    # Every receipt taken against this booking, oldest first -- a client who pays a deposit
    # in March and the balance in May has two, and overwriting the first with the second is
    # how ₹5,000 of a ₹10,000 campaign used to vanish. delete-orphan because a deleted
    # booking's receipts have nothing left to be receipts for.
    payments = relationship(
        "AdPayment",
        back_populates="placement",
        cascade="all, delete-orphan",
        order_by="AdPayment.paid_at",
    )


class AdPlacementTarget(Base):
    """One place a booked advert runs, and the playlist item it put there.

    Exactly one of screen_id / group_id is set. Holding playlist_item_id is what makes
    "take this ad off that one screen" a single precise delete instead of a guess.
    """

    __tablename__ = "ad_placement_targets"

    id = Column(Integer, primary_key=True, index=True)
    placement_id = Column(Integer, ForeignKey("ad_placements.id", ondelete="CASCADE"), nullable=False, index=True)
    screen_id = Column(Integer, ForeignKey("screens.id", ondelete="CASCADE"), nullable=True, index=True)
    group_id = Column(Integer, ForeignKey("screen_groups.id", ondelete="CASCADE"), nullable=True, index=True)
    # SET NULL rather than CASCADE: if an operator deletes the item by hand on the screen
    # page, the booking should survive as a record of what was sold, just no longer placed.
    playlist_item_id = Column(Integer, ForeignKey("playlist_items.id", ondelete="SET NULL"), nullable=True)
    # When this place was actually added to the booking, which is not the same as when the
    # booking starts. A screen added on the 10th of a campaign that began on the 1st plays
    # from the 10th, and its report figures have to be divided by the days it really ran --
    # otherwise it reads as an underperforming location rather than a late addition.
    assigned_at = Column(UtcDateTime, nullable=False, default=utcnow)
    # This location's own run window. NULL on both means "inherit the booking", which is
    # every row that existed before per-location durations, so nothing changes for them.
    #
    # One client routinely buys different lengths in different places -- 30 days in a mall,
    # 10 in a shop, 50 at an airport -- because the sites are worth different amounts to
    # them. Modelled here rather than as three separate bookings so it stays ONE commercial
    # record: one invoice, one extension, one report.
    #
    # Note the booking's own starts_at/ends_at are unchanged and still the default. They
    # are also what an invoice shows as sold, and price stays on the booking: these two
    # columns are the delivery schedule, not the commercial terms.
    starts_at = Column(UtcDateTime, nullable=True)
    ends_at = Column(UtcDateTime, nullable=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)

    @property
    def effective_starts_at(self):
        """When this location actually begins carrying the advert.

        max() against assigned_at for the reason _place already documents: a screen added
        on day 10 of a campaign must not be told it started on day 1.
        """
        base = self.starts_at or (self.placement.starts_at if self.placement else None)
        if base is None:
            return self.assigned_at
        return max(base, self.assigned_at) if self.assigned_at else base

    @property
    def effective_ends_at(self):
        """When this location stops, its own window winning over the booking's."""
        if self.ends_at is not None:
            return self.ends_at
        return self.placement.effective_ends_at if self.placement else None

    __table_args__ = (
        CheckConstraint(
            "(screen_id IS NOT NULL) <> (group_id IS NOT NULL)",
            name="ck_placement_target_exactly_one",
        ),
    )

    placement = relationship("AdPlacement", back_populates="targets")
    screen = relationship("Screen")
    group = relationship("ScreenGroup")
    playlist_item = relationship("PlaylistItem")


class AdPlacementExtension(Base):
    """One paid extension of a booking's run.

    A row per extension rather than an `extended_to` column on the booking, because a
    campaign that does well is extended more than once and the client's invoice has to
    show each one. Collapsing them into a single field would overwrite the record of the
    first sale every time a second was made.

    The booking's own starts_at/ends_at stay as SOLD. The effective end is the latest
    extension's `extended_to`, which is what the report and the placed playlist items use.
    """

    __tablename__ = "ad_placement_extensions"

    id = Column(Integer, primary_key=True, index=True)
    placement_id = Column(Integer, ForeignKey("ad_placements.id", ondelete="CASCADE"), nullable=False, index=True)
    extended_from = Column(UtcDateTime, nullable=False)
    extended_to = Column(UtcDateTime, nullable=False)
    # Smallest currency unit, like AdPlacement.price_paise, so money never touches a float.
    additional_price_paise = Column(BigInteger, nullable=False, default=0)
    is_paid = Column(Boolean, nullable=False, default=False)
    notes = Column(String, nullable=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)

    placement = relationship("AdPlacement", back_populates="extensions")

    __table_args__ = (
        CheckConstraint("extended_to > extended_from", name="ck_extension_window_forward"),
    )


class AdPayment(Base):
    """What the client actually paid, and how.

    `AdPlacement.is_paid` was the whole of this: one boolean, flipped through the same
    generic PUT that edits dates and price. It could say a campaign was paid without
    recording the amount, the date, the method, the reference number, or who entered it --
    so "did Brightmart pay by UPI or is that the cheque that bounced?" had no answer
    anywhere in the system, and a mis-click was indistinguishable from a receipt.

    A row per receipt, not per booking. It was one-per-booking (a unique constraint on
    placement_id) on the reasoning that this records a settled payment rather than a
    ledger -- but clients pay deposits, and recording the second ₹5,000 of a ₹10,000
    campaign overwrote the first and filed the client as having paid half. What the client
    has handed over is now the SUM of these rows; see placements.settlement().

    A row can be corrected in place (PATCH) when the money arrived but was typed wrong --
    an amount, a method, a missing UTR. That replaced delete-and-retype, which forced an
    operator to re-enter a receipt that was mostly right and discarded `recorded_by` and
    the original entry time with it. Money that never arrived is still DELETED, not edited
    down to zero, so the ledger never shows a payment that did not happen.

    `method` is validated in the schema against PAYMENT_METHODS rather than by a database
    enum, so accepting a new one is a deploy and not a migration.
    """

    __tablename__ = "ad_payments"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    placement_id = Column(Integer, ForeignKey("ad_placements.id", ondelete="CASCADE"), nullable=False, index=True)
    # Smallest currency unit, like AdPlacement.price_paise, so money never touches a float.
    amount_paise = Column(BigInteger, nullable=False, default=0)
    method = Column(String(20), nullable=False)
    # UTR, cheque number, card slip -- whatever the tenant needs to find it in their bank.
    reference = Column(String(80), nullable=True)
    # When the money arrived, which is not when the row was typed. Backdating a payment
    # entered on Monday for a cheque banked on Friday has to be possible.
    paid_at = Column(UtcDateTime, nullable=False, default=utcnow)
    # SET NULL: a staff member leaving must not delete the record of a payment they took.
    recorded_by_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    notes = Column(String, nullable=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)
    updated_at = Column(UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow)

    placement = relationship("AdPlacement", back_populates="payments")
    recorded_by = relationship("User")

    __table_args__ = (
        # A receipt for nothing is a mis-click, not a payment, and it drags a booking into
        # "part paid" while adding no money to it.
        CheckConstraint("amount_paise > 0", name="ck_ad_payments_amount_positive"),
    )


class PlayLog(Base):
    __tablename__ = "play_logs"

    event_id = Column(String, primary_key=True, index=True)
    screen_id = Column(Integer, ForeignKey("screens.id"), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    # No foreign keys on these three on purpose. This is an append-only audit log whose
    # referents -- content, playlists, campaigns -- get deleted in normal operation, and a
    # device can still be holding queued events for a deleted row. With FKs, that insert
    # raised IntegrityError, the device retried its oldest batch forever, and every later
    # play on that screen was lost behind the wedge. Indexes stay for the report queries.
    media_id = Column(Integer, nullable=True, index=True)
    playlist_id = Column(Integer, nullable=True, index=True)
    campaign_id = Column(Integer, nullable=True, index=True)

    # Device local time (raw)
    device_started_at = Column(UtcDateTime, nullable=False)
    device_finished_at = Column(UtcDateTime, nullable=False)

    # Corrected time (adjusted for server clock drift)
    corrected_started_at = Column(UtcDateTime, nullable=False)
    corrected_finished_at = Column(UtcDateTime, nullable=False)

    duration_ms = Column(Integer, nullable=False)

    # "completed", "partial", "error"
    status = Column(String, nullable=False)
    error_message = Column(Text, nullable=True)

    received_at = Column(UtcDateTime, nullable=False, default=utcnow)
    aggregated = Column(Boolean, nullable=False, default=False, index=True)
    # Whether the TV had a live connection the moment this play finished. Null when the app
    # that sent it predates this column, or hasn't learned its own answer yet (a row queued
    # before the device's own app update) -- aggregate_play_logs_sync then estimates it from
    # how long the play sat on the device before finally being uploaded.
    played_offline = Column(Boolean, nullable=True)

from sqlalchemy import Index, func
Index("ix_play_logs_org_started_at", PlayLog.organization_id, PlayLog.corrected_started_at)
Index("ix_play_logs_media_started_at", PlayLog.media_id, PlayLog.corrected_started_at)


class Alert(Base):
    """Something wrong with the fleet, recorded so it can be delivered and reviewed.

    Persisted rather than derived on demand because an alert has a life beyond the moment
    it is true: it has to be deliverable to a phone, acknowledgeable by whoever picked it
    up, and answerable afterwards ("when did that screen actually drop?"). The dashboard
    computed all of this in the browser, so none of that was possible.
    """

    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)

    kind = Column(String(40), nullable=False, index=True)
    severity = Column(String(10), nullable=False)

    # No foreign keys, for the same reason PlayLog carries none: an alert outlives the row
    # it describes. Deleting the screen that failed must not delete the record that it did.
    screen_id = Column(Integer, nullable=True, index=True)
    content_id = Column(Integer, nullable=True, index=True)

    title = Column(String(300), nullable=False)
    detail = Column(Text, nullable=True)

    # Identity of the situation ("screen_offline:42"), not of one observation. The partial
    # unique index below uses it to make re-raising an already-open alert impossible at the
    # database level, rather than relying on the reconciler to check first -- which would
    # be a race every time two workers swept at once.
    dedupe_key = Column(String(80), nullable=False)

    raised_at = Column(UtcDateTime, nullable=False, default=utcnow, index=True)
    # Set when the condition stops being true. Null means "still wrong right now".
    resolved_at = Column(UtcDateTime, nullable=True, index=True)
    acknowledged_at = Column(UtcDateTime, nullable=True)
    acknowledged_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    # Channels this alert has already been delivered through, so a retry or a second worker
    # cannot send the same message to a phone twice.
    notified = Column(JSON, nullable=False, default=list)


# Partial, so the constraint applies only to alerts that are still open. Without the WHERE
# clause a screen that goes offline, recovers, and goes offline again could never raise a
# second alert -- the resolved one from last week would block it forever.
Index(
    "ix_alerts_open_unique",
    Alert.organization_id,
    Alert.dedupe_key,
    unique=True,
    postgresql_where=Alert.resolved_at.is_(None),
    sqlite_where=Alert.resolved_at.is_(None),
)


class KioskUnlockEvent(Base):
    """One attempt to get out of a locked TV, kept so a super admin can see it and -- when a
    tenant is genuinely stuck -- act on it.

    Two methods write this: a TV reports every local PIN attempt (right or wrong, best
    effort, offline-tolerant -- the PIN check itself never needs the server, only this log
    entry does) and the admin console records the moment it queues a remote unlock. No
    foreign key to Screen for the same reason PlayLog and Alert carry none: this outlives
    the screen row it describes.
    """

    __tablename__ = "kiosk_unlock_events"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    screen_id = Column(Integer, nullable=True, index=True)

    occurred_at = Column(UtcDateTime, nullable=False, default=utcnow, index=True)
    # "pin": entered locally on the TV. "remote": a super admin queued it from the console.
    method = Column(String(10), nullable=False)
    success = Column(Boolean, nullable=False)
    # Who queued a remote unlock. Null for a local PIN attempt -- the TV has no way to know
    # which human typed it, and a device-signed report must not be allowed to claim one.
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class AdminAuditEvent(Base):
    """One thing a platform operator did, append-only.

    No foreign keys on purpose: the trail has to outlive what it describes -- a purged
    tenant, a deleted user -- or the record of who destroyed something is destroyed with it.
    The actor's username is snapshotted for the same reason.
    """

    __tablename__ = "admin_audit_events"

    id = Column(Integer, primary_key=True, index=True)
    occurred_at = Column(UtcDateTime, nullable=False, default=utcnow, index=True)
    actor_user_id = Column(Integer, nullable=True, index=True)
    actor_username = Column(String(150), nullable=True)
    # Set when the change was made from inside a tenant's workspace (X-Act-As-Org).
    acting_org_id = Column(Integer, nullable=True)
    target_org_id = Column(Integer, nullable=True, index=True)
    target_type = Column(String(40), nullable=True)
    target_id = Column(String(64), nullable=True)
    action = Column(String(80), nullable=False, index=True)
    reason = Column(String(500), nullable=True)
    before_json = Column(Text, nullable=True)
    after_json = Column(Text, nullable=True)
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(300), nullable=True)
    batch_id = Column(String(36), nullable=True, index=True)


class PlayLogHourlyRollup(Base):
    __tablename__ = "play_log_hourly_rollups"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True)
    # Same reasoning as PlayLog: derived from an append-only log whose referents get
    # deleted. Here it matters more -- the rollup is written by aggregate_play_logs, whose
    # exception handler only prints (worker.py), so one FK violation would stop aggregation
    # for the whole fleet and every report would read zero with no error surfaced.
    campaign_id = Column(Integer, nullable=True, index=True)
    screen_id = Column(Integer, ForeignKey("screens.id"), nullable=False, index=True)
    media_id = Column(Integer, nullable=True, index=True)

    date_hour = Column(UtcDateTime, nullable=False, index=True)
    
    total_plays = Column(Integer, nullable=False, default=0)
    completed_plays = Column(Integer, nullable=False, default=0)
    partial_plays = Column(Integer, nullable=False, default=0)
    error_plays = Column(Integer, nullable=False, default=0)
    duration_ms = Column(BigInteger, nullable=False, default=0)
    # Of total_plays, how many happened with no live connection -- PlayLog.played_offline
    # TRUE, or NULL and uploaded more than 30 minutes after it finished (worker.py's
    # OFFLINE_UPLOAD_GAP). online = total_plays - offline_plays; never stored separately,
    # the same way completed/partial/error are never cross-checked against total either.
    offline_plays = Column(Integer, nullable=False, default=0, server_default="0")

# COALESCE, not the bare columns. campaign_id and media_id are both nullable, and Postgres
# treats NULLs as distinct in a unique index -- so on the bare columns this index enforced
# nothing at all for the rows most likely to collide: plays from a playlist with no campaign.
# The aggregation query is written NULL-safely (IS NOT DISTINCT FROM), so nothing has gone
# wrong in practice, but the constraint that was supposed to be the backstop was not one.
# -1 is safe as the sentinel because both columns are positive primary keys.
Index(
    "ix_play_log_hourly_rollups_unique",
    PlayLogHourlyRollup.organization_id,
    func.coalesce(PlayLogHourlyRollup.campaign_id, -1),
    PlayLogHourlyRollup.screen_id,
    func.coalesce(PlayLogHourlyRollup.media_id, -1),
    PlayLogHourlyRollup.date_hour,
    unique=True
)


class AuthEvent(Base):
    """Sign-in history, successful and failed logins across password and Google flows."""

    __tablename__ = "auth_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    attempted_username = Column(String(150), nullable=False)
    success = Column(Boolean, nullable=False, index=True)
    method = Column(String(30), nullable=False)  # "password" | "google"
    ip = Column(String(64), nullable=True)
    user_agent = Column(String(300), nullable=True)
    occurred_at = Column(UtcDateTime, nullable=False, default=utcnow, index=True)


class AttentionDismissal(Base):
    """Operator snooze/resolution records for Support Center attention queue items."""

    __tablename__ = "attention_dismissals"

    id = Column(Integer, primary_key=True, index=True)
    rule = Column(String(50), nullable=False, index=True)
    organization_id = Column(Integer, nullable=True, index=True)
    target_id = Column(String(100), nullable=True)
    snoozed_until = Column(UtcDateTime, nullable=True)
    resolved = Column(Boolean, nullable=False, default=False)
    dismissed_by = Column(Integer, nullable=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)


class TenantNote(Base):
    """Internal private operator notes for tenant workspaces. Never visible to tenant users."""

    __tablename__ = "tenant_notes"

    id = Column(Integer, primary_key=True, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    author_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_username = Column(String(150), nullable=True)
    body = Column(Text, nullable=False)
    pinned = Column(Boolean, nullable=False, default=False)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)


class OutboxEvent(Base):
    """Transactional outbox event for reliable, ordered, at-least-once push delivery."""
    __tablename__ = "outbox_events"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    channel = Column(String(100), nullable=False, index=True)
    event_type = Column(String(50), nullable=False)
    payload_json = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default="PENDING", index=True)  # PENDING, DELIVERED, DEAD_LETTER
    retry_count = Column(Integer, nullable=False, default=0)
    next_retry_at = Column(UtcDateTime, nullable=False, default=utcnow, index=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)
    processed_at = Column(UtcDateTime, nullable=True)


class DeviceCommandLog(Base):
    """Tracks physical execution lifecycle and acknowledgments of remote device commands."""
    __tablename__ = "device_command_logs"

    id = Column(String(36), primary_key=True)  # UUID
    device_id = Column(String(100), nullable=False, index=True)
    organization_id = Column(Integer, ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    screen_id = Column(Integer, ForeignKey("screens.id", ondelete="SET NULL"), nullable=True, index=True)
    command = Column(String(50), nullable=False)
    params_json = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="PENDING", index=True)  # PENDING, DELIVERED, EXECUTED, FAILED, EXPIRED
    actor_user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_username = Column(String(150), nullable=True)
    created_at = Column(UtcDateTime, nullable=False, default=utcnow, index=True)
    expires_at = Column(UtcDateTime, nullable=False, index=True)
    executed_at = Column(UtcDateTime, nullable=True)
    error_message = Column(Text, nullable=True)

    organization = relationship("Organization")
    screen = relationship("Screen")
    actor_user = relationship("User")


class DeviceChallenge(Base):
    """Ephemeral challenge nonce for Super Admin emergency on-site unlock."""
    __tablename__ = "device_challenges"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(String(100), nullable=False, index=True)
    nonce = Column(String(32), nullable=False, index=True)
    response_code = Column(String(64), nullable=False)
    expires_at = Column(UtcDateTime, nullable=False, index=True)
    used = Column(Boolean, nullable=False, default=False, server_default="false")
    created_at = Column(UtcDateTime, nullable=False, default=utcnow)


