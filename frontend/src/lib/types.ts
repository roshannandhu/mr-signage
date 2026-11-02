/**
 * `super_admin` is the platform operator, not a tenant role. It is the only role that may
 * publish or promote a player release, because a release installs across every tenant's
 * fleet. The team page never offers it — TenantRole is what that page may assign.
 */
// 'manager' is retired. It stays in Role because the server still returns it for accounts
// created before that, but it is gone from TenantRole -- which is what gets SENT, and what
// backend schemas.TenantRole rejects with a 422.
export type Role = 'super_admin' | 'manager' | 'owner' | 'editor' | 'viewer'
export type TenantRole = 'owner' | 'editor' | 'viewer'
/** Promotion ring for a player build. Only `released` reaches screens with no pin. */
export type RolloutState = 'draft' | 'canary' | 'released'
export type TransitionName = 'none' | 'fade' | 'slide_left' | 'slide_right' | 'slide_up' | 'slide_down' | 'zoom'

export interface User {
  id: number
  organization_id: number
  username: string
  role: Role
  is_active: boolean
  created_at: string
  full_name?: string | null
  email?: string | null
  organization_name?: string | null
  organization_status?: string | null
}

export interface ContentItem {
  id: number
  type: 'image' | 'video' | 'website'
  file_url: string
  thumbnail: string | null
  name: string
  tags: string | null
  uploaded_at: string
  file_size_bytes: number
  expires_at: string | null
  status: 'processing' | 'ready' | 'failed'
  failed_reason: string | null
  duration_ms?: number | null
  renditions?: MediaRendition[]

  // 1:1 Unified Ad & Client metadata
  client_id?: number | null
  client_name?: string | null
  client_email?: string | null
  client_phone?: string | null
  plan_id?: number | null
  plan_name?: string | null
  placement_id?: number | null
  placement_price_paise?: number | null
  // The booking's OWN length, not the gap to its effective end -- a location sold longer
  // than the campaign pushes that out, and the editor must not seed its box from it.
  placement_duration_days?: number | null
  placement_starts_at?: string | null
  placement_ends_at?: string | null
  placement_notes?: string | null
  screen_ids?: number[]
  // Per-location run lengths, keyed by screen id. Absent means every location runs the
  // booking's own window, which is the ordinary sale.
  screen_days?: Record<number, number>
  screen_names?: string[]
}

export interface MediaRendition {
  id: number
  resolution: string
  width: number
  height: number
  rotation: number
  duration_ms: number | null
  codec: string | null
  sha256: string | null
  file_size_bytes: number
  file_url: string
}

export interface EnrollmentToken {
  id: number
  token: string | null
  description: string | null
  created_at: string
  expires_at: string | null
  max_uses: number | null
  use_count: number
  is_active: boolean
}

export interface ItemSchedule {
  id?: number
  days_of_week: number[]
  start_time: string | null
  end_time: string | null
}

export interface PlaylistItem {
  id: number
  content_id: number
  duration: number
  /** null = follow the screen's own orientation. */
  rotation: number | null
  order: number
  start_at: string | null
  end_at: string | null
  transition: TransitionName | null
  transition_ms: number | null
  schedule: ItemSchedule | null
  content: ContentItem
}

export interface Playlist {
  id: number
  name: string
  default_transition: TransitionName
  default_transition_ms: number
  created_at: string
  updated_at: string
  items: PlaylistItem[]
}

export interface Screen {
  id: number
  device_id: string | null
  pair_code: string | null
  name: string | null
  orientation: number
  status: 'online' | 'offline' | 'waiting_pairing'
  last_seen: string
  playlist_id: number | null
  group_id: number | null
  device_version: string | null
  app_version: string | null
  storage_used: string | null
  playback_state: 'playing' | 'idle' | 'error'
  current_item_id: number | null
  last_error: string | null
  last_error_at: string | null
  effective_playlist_id: number | null
  orientation_source?: 'auto' | 'manual'
  latest_screenshot?: string | null
  description?: string | null
  tags?: string | null
  location?: string | null
  latitude?: number | null
  longitude?: number | null
  place_id?: string | null
  fit_mode?: FitMode
  /** Unlocks the player's on-TV maintenance screen. Four digits, per screen. */
  maintenance_pin?: string | null
  /** Keeps this screen on its own PIN even when the workspace has set a shared one. */
  uses_own_pin?: boolean
  sync_playback?: boolean
  sync_role?: SyncRole
  leader_screen_id?: number | null
  operating_mode?: OperatingMode
  operating_hours?: Record<string, [string, string]> | null
  target_version_code: number | null
  update_status: string | null  // pending | downloading | installing | success | failed
  screen_width: number | null
  screen_height: number | null
  refresh_rate: number | null
  total_ram_mb: number | null
  available_ram_mb: number | null
  total_storage_mb: number | null
  free_storage_mb: number | null
  supported_video_codecs: string[] | null
  max_decode_width: number | null
  max_decode_height: number | null
  manufacturer: string | null
  model: string | null
  /**
   * Which identifier this panel could produce: 'serial' | 'android_id' | 'random'.
   * Decides whether it can be recognised again after a wipe. null on screens that
   * registered before it was recorded.
   */
  identity_source?: string | null
  /**
   * Whether this panel installs updates unattended. Android allows a silent install only
   * for the device owner; every other screen waits behind a confirmation dialog.
   * null = the player has not reported yet (an older build).
   */
  device_owner?: boolean | null
  /** What actually opens the maintenance door: this screen's own PIN, or the workspace
   *  PIN if one is set and this screen has not opted to keep its own. */
  effective_maintenance_pin?: string | null
  android_version: string | null
  sdk_int: number | null
  network_type: string | null
  timezone: string | null
}

export interface ScreenGroup {
  id: number
  name: string
  parent_id: number | null
  is_dynamic: boolean
  playlist_id: number | null
  created_at: string
  updated_at: string
  screen_count: number
}

export interface Plan {
  id: number
  name: string
  slug: string
  monthly_price_paise: number
  yearly_price_paise: number
  // One-time charge for `duration_days` of access — what the storefront sells on.
  price_paise: number
  duration_days: number
  // null = no screen limit; 0 = a package granting none. See Package above.
  max_screens: number | null
  max_clients: number
  // 0 = unlimited, same convention as max_clients.
  max_ad_slots: number
  max_storage_bytes: number
  feature_flags: Record<string, boolean>
}

export interface Subscription {
  status: string
  billing_period: 'monthly' | 'yearly'
  current_period_start: string | null
  current_period_end: string | null
  grace_period_end: string | null
  provider: string | null
  provider_subscription_id: string | null
}

export interface BillingSummary {
  plan: Plan
  subscription: Subscription
  screens_used: number
  max_screens: number | null
  ad_slots_used: number
  max_ad_slots: number | null
  clients_used: number
  max_clients: number | null
  storage_used_bytes: number
  storage_quota_bytes: number
  is_read_only: boolean
}

export interface CheckoutSession {
  provider: string
  provider_subscription_id: string
  checkout_url: string
}

export interface PurchaseResponse {
  // "internal" (free, activated now), "mock" (finish via mockConfirmOrder), or "razorpay"
  // (open Razorpay Checkout with key_id + order_id + amount_paise).
  provider: string
  order_id: string | null
  amount_paise: number
  key_id: string | null
  checkout_url: string | null
}

export interface CustomPlanRequestInput {
  max_screens: number
  max_clients: number
  max_ad_slots: number
  max_storage_bytes: number
  duration_days: number
  feature_flags: Record<string, boolean>
  notes?: string | null
  company_name?: string | null
  contact_name?: string | null
  contact_email?: string | null
  contact_phone?: string | null
}

/** A revision to a bespoke package. Every field optional -- send only what changed.
 *  Valid at any status, including 'paid': that is how a tenant already on a custom
 *  package gets moved, and it does not re-bill them. */
export interface CustomPlanRequestUpdate {
  max_screens?: number
  max_clients?: number
  max_ad_slots?: number
  max_storage_bytes?: number
  duration_days?: number
  feature_flags?: Record<string, boolean>
  price_paise?: number
  notes?: string | null
}

export interface CustomPlanRequestItem {
  id: number
  organization_id: number
  max_screens: number
  max_clients: number
  max_ad_slots: number
  max_storage_bytes: number
  duration_days: number
  feature_flags: Record<string, boolean>
  // 0 until the operator prices it; status carries whether that has happened.
  price_paise: number
  status: 'requested' | 'priced' | 'paid' | 'rejected'
  notes: string | null
  created_at: string
  organization_name?: string | null
  company_name?: string | null
  contact_name?: string | null
  contact_email?: string | null
  contact_phone?: string | null
}

export interface PlatformPayment {
  id: number
  organization_id: number
  organization_name: string | null
  kind: 'plan' | 'custom' | 'subscription'
  plan_id: number | null
  custom_request_id: number | null
  amount_paise: number
  currency: string
  provider: string
  provider_order_id: string | null
  provider_payment_id: string | null
  status: string
  paid_at: string
}

export interface AppRelease {
  id: number
  version_code: number
  version_name: string
  apk_url: string
  sha256: string | null
  mandatory: boolean
  rollout_state: RolloutState
  created_at: string
}

export interface EmergencyBroadcast {
  id: number
  target_type: 'all' | 'group' | 'screen'
  target_id: number | null
  playlist_id: number
  updated_at: string
}

export type CampaignExportFormat = 'csv' | 'excel' | 'pdf'

export interface Campaign {
  id: number
  name: string
}

export interface CampaignInfo extends Campaign {
  assigned_screens: number
  online: number
  offline: number
  currently_playing: number
}

export interface CampaignPeriodStats {
  total_plays: number
  success_percent: number
  online_plays: number
  offline_plays: number
}

export interface CampaignStats {
  today: CampaignPeriodStats
  yesterday: CampaignPeriodStats
  week: CampaignPeriodStats
  lifetime: CampaignPeriodStats
}

export interface CampaignPoint {
  date: string
  total_plays: number
  completed_plays: number
}

export interface Screenshot {
  id: number
  screen_id: number
  url: string
  created_at: string
}

/** How the player handles content whose aspect ratio differs from the panel. */
export type FitMode = 'contain' | 'cover'
/** Video-wall role: a follower takes its playback clock from its leader. */
export type SyncRole = 'leader' | 'follower'
export type OperatingMode = 'always' | 'hours' | 'never'

export const WEEKDAYS = [
  { key: 'mon', label: 'Monday' },
  { key: 'tue', label: 'Tuesday' },
  { key: 'wed', label: 'Wednesday' },
  { key: 'thu', label: 'Thursday' },
  { key: 'fri', label: 'Friday' },
  { key: 'sat', label: 'Saturday' },
  { key: 'sun', label: 'Sunday' },
] as const

export interface MediaPeriodStats {
  total_plays: number
  completed_plays: number
  error_plays: number
  success_percent: number
  online_plays: number
  offline_plays: number
}

export interface MediaScreenRow {
  screen_id: number
  screen_name: string
  group_name: string | null
  location: string | null
  latitude: number | null
  longitude: number | null
  total_plays: number
  completed_plays: number
  error_plays: number
  online_plays: number
  offline_plays: number
  last_played: string | null
}

export interface MediaLocationRow {
  /** The screen's real location, falling back to its group name when unset. */
  location: string
  screens: number
  total_plays: number
  completed_plays: number
}

/** Proof-of-play for a single advert, from the deduplicated hourly rollups. */
export interface MediaReport {
  content_id: number
  today: MediaPeriodStats
  week: MediaPeriodStats
  month: MediaPeriodStats
  lifetime: MediaPeriodStats
  per_screen: MediaScreenRow[]
  per_location: MediaLocationRow[]
  daily: { date: string; total_plays: number; completed_plays: number }[]
}

export interface PlacementTarget {
  id: number
  screen_id: number | null
  group_id: number | null
  name: string
  location?: string | null
  kind: 'screen' | 'group'
  /** False if the playlist item was deleted by hand — sold, but no longer on air there. */
  is_placed: boolean
  /**
   * This location's own window, when it was sold one. Null means it follows the booking.
   *
   * The API has always returned these; nothing read them, so "Airport TV — 50 days" beside
   * a 30-day campaign was invisible everywhere except inside the edit modal.
   */
  starts_at: string | null
  ends_at: string | null
  days: number | null
}

/** What a tenant puts at the top of the report they hand their client. */
export interface Branding {
  brand_name: string | null
  brand_color: string | null
  /** Already resolved for display; the column holds the host-independent form. */
  logo_url: string | null
  /** What the report actually prints, once the workspace-name fallback is applied. */
  effective_name: string
}

/** The one PIN that can cover every screen in the workspace. Null means unset -- every
 *  screen stays on its own PIN, today's only behaviour. */
export interface LockPin {
  lock_pin: string | null
}

/** Whether the lock the plan sells is actually active. Off by default -- a plan gaining
 *  the feature never retroactively locks an already-paired fleet on its own. */
export interface LockEnabled {
  lock_enabled: boolean
}

/** One PIN attempt or remote unlock, for the admin audit log. */
export interface KioskUnlockEvent {
  id: number
  screen_id: number | null
  screen_name: string | null
  occurred_at: string
  method: 'pin' | 'remote'
  success: boolean
  actor_username: string | null
}

/** An advertiser this tenant sells to. Reusable across bookings, unlike a typed-in name. */
export interface Client {
  id: number
  name: string
  client_code: string
  email: string | null
  phone: string | null
  notes: string | null
  active_campaigns_count?: number
  total_spent_paise?: number
  created_at: string
}

/** A package the tenant sells on to its clients — not the plan MR Signage bills the tenant. */
export interface TenantPlan {
  id: number
  name: string
  description: string | null
  duration_days: number
  max_locations: number
  ad_slots: number
  price_paise: number
  support_tier: string
  is_active: boolean
  created_at: string
}

/** One paid extension of a booking's run. A booking may have several. */
export interface PlacementExtension {
  id: number
  extended_from: string
  extended_to: string
  additional_price_paise: number
  is_paid: boolean
  notes: string | null
  created_at: string
}

/** An advert sold to a client: who, how long, where, and for how much. */
export interface Placement {
  id: number
  content_id: number
  creative_name?: string | null
  creative_thumbnail_url?: string | null
  advertiser: string
  client: Client | null
  plan: TenantPlan | null
  extensions: PlacementExtension[]
  /** Where the run actually finishes once extensions count. `ends_at` stays as sold. */
  effective_ends_at: string | null
  days_remaining?: number | null
  /** The booking plus every extension sold against it. */
  total_price_paise: number | null
  price_paise: number
  /** Settled in full. The shadow of the figures below, never a separate opinion. */
  is_paid: boolean
  /** What the client has actually handed over. */
  amount_paid_paise: number
  /** What is still outstanding against `total_price_paise`, extensions included. */
  balance_due_paise: number
  /** The one word for the two figures above. */
  payment_status: PaymentStatus
  starts_at: string
  ends_at: string
  notes: string | null
  created_at: string
  targets: PlacementTarget[]
  /** Screens this booking actually reaches, groups expanded — the way the cap counts them. */
  screens_used: number
  /** The screen allowance in force — the package's, or this booking's own when it was sold
   *  off a package. 0 when nothing caps it. */
  plan_max_locations: number
  /** This booking's OWN cap as stored, for editing. Only in force when `plan` is null. */
  max_locations: number
  /** Paid for and not being used. Never blocks a save; it is money the client is owed. */
  screens_unused: number
  /** Every receipt taken against this booking, oldest first. Empty until one is recorded. */
  payments: Payment[]
}

/** One receipt against a booking. Their sum is `amount_paid_paise`, and `is_paid` its shadow. */
export interface Payment {
  id: number
  amount_paise: number
  method: PaymentMethod
  reference: string | null
  paid_at: string
  notes: string | null
  /** Who took the money, kept even after they leave. */
  recorded_by: string | null
  created_at: string
}

export type PaymentMethod = 'cash' | 'upi' | 'bank_transfer' | 'cheque' | 'card' | 'other'

export type PaymentStatus = 'unpaid' | 'part_paid' | 'paid'

/** One plan a booking could move to, and what moving would mean. */
export interface PlanOption {
  plan: TenantPlan
  is_current: boolean
  /** Whether it covers the screens the booking ALREADY runs on. */
  fits: boolean
  /** Exactly one option carries this: the cheapest that fits the screens in use. */
  recommended: boolean
}

export type AlertSeverity = 'critical' | 'warning'

export interface FleetAlert {
  id: number
  kind: string
  severity: AlertSeverity
  title: string
  detail: string | null
  screen_id: number | null
  content_id: number | null
  raised_at: string
  resolved_at: string | null
  acknowledged_at: string | null
}

export interface AlertSummary {
  total: number
  critical: number
  warning: number
  unacknowledged: number
}

// --- Platform administration ---------------------------------------------------------
// Mirrors the Pydantic models in backend/routers/admin.py. These are cross-tenant shapes
// only a super_admin can fetch, kept apart from the tenant types above for that reason.

export interface TenantSummary {
  id: number
  name: string
  slug: string
  status: string
  created_at: string
  owner_email?: string | null
  owner_name?: string | null
  plan_id?: number | null
  plan_name?: string | null
  screens_count: number
  online_screens_count: number
  // null = no limit configured; 0 = a package that grants none. Rendered differently, so
  // `max_screens || '∞'` is wrong here -- it would show a zero-screen package as unlimited.
  max_screens: number | null
  max_ad_slots: number | null
  // The raw override the quota dialog edits; 0 = "no override, follow the package".
  max_screens_override: number
  max_ad_slots_override: number
  ad_slots_used: number
  storage_used_bytes: number
  storage_quota_bytes: number
  rejection_reason?: string | null
  /** Set when an admin removed the workspace. Everything still exists until `purge_at`. */
  deleted_at?: string | null
  /** When the removal becomes permanent. Null unless `deleted_at` is set. */
  purge_at?: string | null
  // The paid window. `subscription_state` is what the product acts on -- 'active', 'grace'
  // or 'expired' -- while `subscription_status` is the stored column, which reads "active"
  // whatever the calendar says.
  subscription_state?: string | null
  subscription_status?: string | null
  billing_period?: string | null
  current_period_end?: string | null
  // null = unlimited, same as max_screens / max_ad_slots above.
  max_clients?: number | null
  clients_used?: number
  /** What this workspace's package grants it. The numbers never showed this. */
  feature_flags?: Record<string, boolean>
}

export interface TenantScreen {
  id: number
  name?: string | null
  status: string
  last_seen?: string | null
  location?: string | null
  model?: string | null
  app_version?: string | null
  playback_state: string
}

export interface TenantContent {
  id: number
  name?: string | null
  type?: string | null
  status: string
  file_size_bytes: number
  uploaded_at?: string | null
  thumbnail?: string | null
}

export interface TenantUser {
  id: number
  username: string
  email?: string | null
  full_name?: string | null
  role: string
  is_active: boolean
}

export interface ScreenUpdateResult {
  screen_id: number
  name: string | null
  app_version: string | null
  target_version_code: number | null
  offered_version_code: number | null
  offered_version_name: string | null
  already_current: boolean
  online: boolean
}

export interface FleetScreen {
  id: number
  name: string | null
  organization_id: number
  organization_name: string
  online: boolean
  app_version: string | null
  target_version_code: number | null
  update_status: string | null
  update_failure_count: number
  last_seen: string | null
}

export interface FleetOverview {
  total: number
  online: number
  latest_version_name: string | null
  latest_version_code: number | null
  on_latest: number
  updating: number
  failed: number
  versions: Record<string, number>
  screens: FleetScreen[]
  /** Whether TVs may still call the API with no device credential. */
  legacy_device_auth?: boolean
  /** TVs holding no device secret: the ones switching legacy auth off would strand. */
  screens_without_secret?: number
}

export interface Package {
  id: number
  name: string
  slug: string
  monthly_price_paise: number
  yearly_price_paise: number
  price_paise: number
  duration_days: number
  // null = no screen limit. 0 stays a real limit of zero, unlike the three below where 0
  // IS the unlimited marker — see UNLIMITED in the admin router.
  max_screens: number | null
  max_clients: number
  max_storage_bytes: number
  max_ad_slots: number
  feature_flags: Record<string, boolean>
  is_active: boolean
}

// slug is write-once: /api/billing/checkout resolves the payment provider's plan id from
// an env var named after it, so renaming one silently breaks checkout for that package.
export interface PackageWrite {
  name: string
  slug: string
  monthly_price_paise: number
  yearly_price_paise: number
  price_paise: number
  duration_days: number
  max_screens: number
  max_clients: number
  max_storage_bytes: number
  max_ad_slots: number
  feature_flags: Record<string, boolean>
  is_active: boolean
}

export interface StorageTenant {
  organization_id: number | null
  name: string
  prefix: string
  bucket_bytes: number
  bucket_objects: number
  /** What the database says. Differs from bucket_bytes when objects were orphaned. */
  database_bytes: number
  quota_bytes: number
}

/** GET /api/admin/platform-health -- the full report; the public /api/health is a subset. */
export interface PlatformHealth {
  status: 'ok' | 'degraded'
  database: string
  backend: string
  host: string | null
  redis: 'connected' | 'unreachable'
  object_storage: string
  email: string
  storage_env: { set: string[]; missing: string[]; unexpected: string[]; placeholder: string[] }
  schema_migration_error: string | null
  legacy_screens_without_secret: number | null
  warning: string | null
}

export interface PlatformStorage {
  configured: boolean
  error?: string | null
  bucket?: string | null
  endpoint?: string | null
  total_bytes: number
  object_count: number
  /** Object storage has no fixed size, so "left" only means something against this. */
  budget_bytes?: number | null
  committed_quota_bytes: number
  cached: boolean
  tenants: StorageTenant[]
}

export interface StorageSettings {
  /** Masked — only the last characters, enough to tell two keys apart. */
  access_key_id?: string | null
  /** The secret is never returned. This says only whether one is held. */
  secret_is_set: boolean
  endpoint_url?: string | null
  bucket?: string | null
  /** Which values come from the console rather than the server environment. */
  from_console: string[]
  /** Whether the resulting configuration actually works. The only real question. */
  storage_enabled: boolean
}


export interface DayPoint {
  date: string
  value: number
}

export interface RecentAuditEvent {
  id: number
  occurred_at: string | null
  actor_username: string | null
  action: string
  target_org_id: number | null
  target_type: string | null
  target_id: string | null
}

export interface AdminOverview {
  range: '7d' | '30d' | '90d'
  generated_at: string
  cached: boolean

  tenants_active: number
  tenants_pending: number
  tenants_suspended: number
  signups_by_day: DayPoint[]

  screens_total: number
  screens_online: number
  screens_offline_24h: number
  on_latest_version_pct: number | null

  alerts_critical_open: number
  alerts_warning_open: number

  plays_online_by_day: DayPoint[]
  plays_offline_by_day: DayPoint[]

  revenue_paise_by_day: DayPoint[]
  revenue_paise_total: number

  renewals_due_14d_count: number
  renewals_due_14d_value_paise: number

  storage_used_bytes: number

  pending_approvals: number
  pending_custom_requests: number

  recent_activity: RecentAuditEvent[]
}

export interface InboxCounts {
  pending_approvals: number
  new_custom_requests: number
  critical_alerts: number
  failed_payments_7d: number
}
