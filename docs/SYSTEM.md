# MR Signage — How the whole system works

A complete walkthrough of the product: what each kind of user can do, the packages that
gate it, how a television joins and stays in sync, and how the three parts talk to each
other.

Written from the code, not from memory. Where something is a known gap or a production
problem, it says so.

---

## 1. The three actors

The product is one platform with three separate audiences. Keeping them straight is the
key to reading everything else.

| Actor | Who they are | Where they work | What they own |
|---|---|---|---|
| **Platform operator** (`super_admin`) | You. MR Signage itself. | `/admin` — a dark control console | The platform: every workspace, the packages sold, the TV app releases |
| **Tenant** (`owner` / `editor` / `viewer`) | Your customer — a signage network operator | `/dashboard/*` | Their own screens, media, advertisers and bookings |
| **TV screen** (the Android player) | A television in a shop, mall or station | The Android TV app | Nothing — it is a client that plays what it is told |

There is a fourth party who never logs in: the **tenant's client** (the advertiser who
buys a slot). They receive PDFs — a playback report and an invoice — and nothing else.

### The chain of ownership

```
Platform operator
   └── sells a PACKAGE (Plan) to → Tenant  (caps screens / clients / storage / features)
          └── sells an ADVERTISING PLAN (TenantPlan) to → their Client
                 └── which runs as a BOOKING (AdPlacement) on → Screens
                        └── which plays as → PlaylistItems on the TV
```

**Two different things are both called "plan". This trips everyone up:**

- **`Plan`** — the *platform* package the **tenant buys from you** (Free / Starter /
  Business). Controls how big their workspace may get.
- **`TenantPlan`** — an *advertising* package the **tenant sells to their own advertisers**
  (e.g. "30 days, 5 locations, 2 slots, ₹30,000"). Nothing to do with their own limits.

---

## 2. Admin — the platform control plane

Route: `/admin`. Guarded by `role === 'super_admin'` resolved from the **server**
(`/api/auth/me`), never the browser's cached copy.

Visually it is deliberately **not** a tenant workspace: an always-dark cool-slate console
with violet accents that ignores the light/dark toggle entirely. That is enforced by the
`.admin-console` token scope in `globals.css`, applied on `app/admin/layout.tsx`.

### 2.1 What admin controls

| Area | Page | What it does |
|---|---|---|
| **Overview** | `/admin` | Platform-wide counts and health |
| **Approvals queue** | `/admin/approvals` | Let a newly signed-up workspace in, or reject it |
| **All tenants** | `/admin/tenants` | Every workspace, status, usage against limits |
| **One tenant** | `/admin/tenants/[id]` | Inspect their screens, media, team — and **enter their workspace** |
| **Packages** | `/admin/packages` | Create/price/retire the platform packages tenants buy |
| **App releases** | `/admin/releases` | Publish an APK version and roll it out to the fleet |
| **Fleet versions** | `/admin/fleet` | Every TV across every tenant, its app version and update state |
| **Zero-touch provisioning** | `/admin/provisioning` | Generate QR payloads for factory-fresh Android devices |
| **Demo video** | `/admin/demo-video` | The reel unapproved workspaces play on their TVs |

### 2.2 Tenant lifecycle — the admin's core job

An organisation is always in exactly one status:

```
pending_approval ──approve──► active ──suspend──► suspended
       │                         ▲                    │
       └──────reject────► rejected└──────reinstate────┘
```

- **`pending_approval`** — signed up, exists, but `get_tenant_scope` refuses **every**
  tenant route. They see only `/dashboard/pending`. Their TVs play the demo reel.
- **`active`** — full access, bounded by their package.
- **`suspended` / `rejected`** — blocked at the same gate. Suspending genuinely cuts
  access (it used to only change a label).

Endpoints: `POST /api/admin/tenants/{id}/approve | reject | suspend | reinstate`.

### 2.3 Setting limits and prices

- `PATCH /api/admin/tenants/{id}/quota` — override an individual workspace's caps
  (screens, ad slots, clients, storage) above or below their package.
- `POST|PATCH|DELETE /api/admin/plans` — the package catalogue.
- **Custom plan requests** — a tenant can ask for a bespoke package; admin prices it
  (`/custom-requests/{id}/price`) and the tenant then pays for it.

Limits resolve as **per-workspace override → package value → unlimited**, through the
`Organization.effective_max_*` properties. `0` means unlimited everywhere.

### 2.4 Entering a tenant's workspace (impersonation)

Admin needs to *fix* things, not only look at them. **"Enter workspace"** on a tenant page
steps the operator into that tenant's own dashboard.

- The browser stores the acting workspace and sends **`X-Act-As-Org: <id>`** on every API
  call (attached once in `authFetch`, so every existing tenant endpoint inherits it).
- `TenantScope.acting_organization_id` then narrows `query()` to that workspace and makes
  creates land in it.
- **Honoured only for a `super_admin`.** A tenant sending the header is silently ignored.
- Every accepted use is **logged**. A violet bar sits above the dashboard naming the
  workspace, with one click out.
- An operator inside a workspace is **not** blocked by that tenant's pending/suspended or
  read-only billing state — that is usually exactly why they went in.

This reuses the real tenant endpoints rather than cloning them under `/admin`, so the two
can never drift apart.

> **Why this exists:** before it, a super admin opening the dashboard had the organisation
> filter *dropped entirely* — they saw every tenant's rows mixed together, and anything
> they created was filed under their own organisation.

### 2.5 App releases and fleet updates (OTA)

`AppRelease` rows are **platform-wide** — publishing one affects every tenant, which is
why only a super admin may create one.

| Field | Meaning |
|---|---|
| `version_code` / `version_name` | Android versioning; the highest `version_code` is "current" |
| `apk_url` | Where the TV downloads it |
| `sha256` | The TV **refuses to install** without a matching digest |
| `mandatory` | Whether the TV should apply it unprompted |
| `rollout_state` | `draft` → `canary` → `released` |

- Only `released` builds are offered fleet-wide. Promoting to `released` is refused if the
  row has no `sha256` — that would produce a fleet of failed installs.
- A screen or group can be **pinned** to a `target_version_code`.
- **Automatic rollback:** after `ROLLBACK_THRESHOLD = 3` failed install attempts the pin is
  dropped and the screen falls back to a known-good build.

⚠️ **Publishing a release is a manual 4-step chain and is easy to half-do.** Bumping
`versionCode` in `build.gradle.kts` releases *nothing*. See §5.4.

---

## 3. Tenant — the signage network operator

Route: `/dashboard/*`.

### 3.1 How a tenant gets in

**Signing up** — Google sign-in only for new workspaces. A verified Google address that
isn't known creates a **new organisation with `status="pending_approval"`** plus an owner
account. Both exist immediately and can do nothing until admin approves.

**Signing in** — either:
- `POST /api/auth/token` — username + password
- `POST /api/auth/google` — Google authorisation code

After sign-in, `destinationFor()` routes them: operator → `/admin`, pending workspace →
`/dashboard/pending`, everyone else → `/dashboard/screens`.

**Team roles inside a workspace** (`require_tenant_roles`):

| Role | Can do |
|---|---|
| `owner` | Everything, including billing, team and branding |
| `editor` | Screens, content, playlists, bookings — the day-to-day work |
| `viewer` | Read only |

A workspace whose subscription has lapsed becomes **read-only**: every write route returns
403 with "billing is restored" wording, while reads keep working.

### 3.2 What a tenant can do

| Page | Purpose |
|---|---|
| `/dashboard/screens` | The TVs: pair, name, locate, set hours, orientation, fit mode |
| `/dashboard/screens/[id]` | One screen — map, and its **playback timeline** |
| `/dashboard/groups` | Group screens (venues, chains); groups nest and can be dynamic |
| `/dashboard/content` | Media library — upload, review, retry failed processing |
| `/dashboard/content/[id]` | One advert: its bookings, payments, reports |
| `/dashboard/playlists/[id]` | The loop itself — order, durations, transitions, schedules |
| `/dashboard/clients` | Advertisers they sell to |
| `/dashboard/plans` | **Their own** advertising packages (TenantPlan) |
| `/dashboard/campaigns` | Analytics per campaign, with export |
| `/dashboard/invoices` | Money owed and received |
| `/dashboard/alerts` | Fleet problems |
| `/dashboard/emergency` | Override every screen with an emergency loop |
| `/dashboard/branding` | Their logo, brand name and colour (used on client PDFs) |
| `/dashboard/billing` | Their own package and payment |
| `/dashboard/team` | Users and roles |
| `/dashboard/provisioning` | Enrollment tokens for bulk TV setup |

### 3.3 The packages a tenant buys from you

Seeded in `backend/billing.py`:

| Package | Price | Screens | Clients | Storage | Features |
|---|---|---|---|---|---|
| **Free** | ₹0 | 5 | 3 | 10 GB | scheduling |
| **Starter** | ₹999/mo | 10 | 10 | 25 GB | scheduling, transitions |
| **Business** | ₹2,999/mo | 50 | 50 | 100 GB | scheduling, transitions, priority support, **emergency_alert** |

The storefront sells access as a **one-time charge for `duration_days`**, not a recurring
subscription — `price_paise` buys a fixed window. The older monthly/yearly columns remain
for the legacy subscription checkout.

**Buying:** `POST /api/billing/purchase` (or `/checkout` for the hosted flow). Razorpay is
the provider, with `POST /api/billing/mock/confirm` for testing and a webhook at
`/api/billing/webhooks/razorpay`. **Payment auto-activates the workspace.**

**Custom packages:** a tenant submits `POST /api/billing/custom-request`; admin prices it;
the tenant pays via `/custom-request/{id}/purchase`.

**Feature gating:** `require_feature("…")` reads the package's `feature_flags_json`. A
workspace with no package has no features. Today **only `emergency_alert` is actually
enforced** in code — the other flags are catalogue metadata.

**Quota enforcement** (all return 409, storage returns 413):

| Limit | Enforced at |
|---|---|
| Screens | every bind path — pair, TV sign-in, enroll |
| Clients | client creation |
| Ad slots | booking creation and the client-ad path |
| Storage | media upload and logo upload |
| Plan locations | booking targets — reported, not hard-refused (see below) |

> A TenantPlan's "5 locations" is what was *quoted*. Real sales add a sixth screen
> mid-campaign, so the booking is allowed but the overage is shown on the client's report
> rather than silently contradicting the plan card.

### 3.4 Selling advertising — the tenant's actual business

This is the heart of the product.

**Advertising packages (`TenantPlan`)** — name, `duration_days`, `max_locations`,
`ad_slots`, `price_paise`, support tier. Or sell **custom**: no package, a negotiated
price, and per-location run lengths.

**A booking (`AdPlacement`)** ties together: the creative, the advertiser, the price, the
window, and one or more **targets** (a screen or a group).

Key behaviours:

- **Per-location durations.** One client routinely buys different lengths in different
  places — 30 days in a mall, 10 in a shop, 50 at an airport. Modelled as per-target
  windows on **one** booking, so it stays one invoice, one report, one commercial record.
- **Placing an advert** writes a real `PlaylistItem` into the screen's loop. A screen with
  no playlist gets one; a screen inheriting a group loop gets its **own forked copy**
  seeded with what it was already playing, so the advert cannot leak to the rest of the
  group and the venue's own content is not lost.
- **Extensions** — sell more time; billed on top, and the run window moves.
- **Change plan** (`POST /placements/{id}/change-plan`) — **replaces** the plan and the
  price. Nothing is added, no date moves. Works custom→package, package→custom, and
  custom→custom to re-cut a figure. *(Extending is the separate action that adds.)*
- **Payments** are a **ledger**: each receipt is its own row, so deposits and balances both
  stand. A receipt can be corrected in place (`PATCH …/payments/{id}`) or deleted if the
  money never arrived. `is_paid` is always re-derived from the sum.
- **Repair** (`POST /placements/{id}/replace`) — puts a booking back on screens it has
  fallen off. See §6.2 for why that happens.

**What the advertiser receives:**

| Document | Contains |
|---|---|
| **Playback report** (`/report.pdf`) | Proof of delivery: campaign period, locations, total plays, **total airtime**, playback completion %, daily trend, a numbered location map, per-screen delivery, and a QR verification certificate. Carries **no price and no payment status.** |
| **Invoice** (`/invoice.pdf`) | The commercial document: what was sold, what it cost, what was paid |

Both are branded with the tenant's own logo and colour. They can be downloaded or emailed
(`POST /placements/{id}/email`).

### 3.5 Connecting a TV — full guidance

There are **four ways** to bind a television to a workspace. All of them end with the
screen holding a `device_id` and a `device_secret`.

**Method 1 — Pairing code** *(default; needs two people or two devices)*
1. Install and open the app on the TV.
2. The TV calls `POST /api/screens/register` and displays a **pairing code**.
3. In the dashboard: Screens → Pair, enter the code (`POST /api/screens/pair`).
4. The screen binds to that workspace and starts syncing.
- Codes are short-lived. Deleting a screen and letting it re-register produces a *fresh*
  code rather than resurrecting the old row.

**Method 2 — Sign in on the TV** *(one installer, no dashboard needed)*
- The installer enters their MR Signage username and password on the TV
  (`POST /api/screens/sign-in`). The device binds to that user's organisation.

**Method 3 — Google sign-in on the TV** *(device flow)*
- `POST /api/screens/google/start` returns a code/URL; the installer completes it on a
  phone; the TV polls `POST /api/screens/google/poll` until bound.

**Method 4 — Enrollment token** *(bulk / warehouse staging)*
1. Tenant creates a token in `/dashboard/provisioning`.
2. Each TV is flashed or configured with it and calls `POST /api/screens/enroll`.
3. Token rejections are deliberately a single generic error, so tokens cannot be probed.

**Zero-touch provisioning** (`/admin/provisioning` → `POST /api/provisioning/qr`) produces
a QR payload for a **factory-fresh** Android device, which installs the app as **device
owner** — the only way to get true kiosk lockdown.

**After binding — ongoing authentication**
- `POST /api/screens/auth` exchanges `device_id` + `device_secret` for a JWT; the app then
  sends `Authorization: Bearer …` on every call.
- `ALLOW_LEGACY_DEVICE_AUTH` (currently **on** in production) lets a screen call device
  endpoints with no credential at all. It exists for panels that predate secrets and
  should be turned off once no screen logs the legacy warning.
- A tenant can revoke a screen's credential: `DELETE /api/screens/{id}/device-secret`.

### 3.6 Controlling screens remotely

| Action | Effect |
|---|---|
| Assign a playlist | `POST /api/screens/{id}/assign/{playlist_id}` |
| Set operating hours | `always`, `never`, or per-weekday windows — the TV blanks outside them |
| Orientation / fit mode | Rotation and how media fills the panel |
| **Bring to front** | `POST /api/screens/{id}/bring-to-front` — forces the app back over whatever is covering it |
| **Request screenshot** | The TV uploads what is actually on screen |
| Emergency broadcast | Override `all`, one `group`, or one `screen` with a chosen playlist |
| Pin an app version | Hold a screen on a specific `target_version_code` |
| Delete | Archives the screen and signals the device to unpair |

---

## 4. The TV screen app (Android)

Package `com.olrac.signage`. Built for Android TV and tablets; `minSdk 26`.

### 4.1 What it does

- **Plays the loop** — dual-surface ExoPlayer with crossfade transitions, so one item is
  prepared while the other is on screen. Images and video, with per-item duration,
  rotation and fit mode.
- **Works offline.** Media is downloaded to local storage and played from disk; the Room
  database holds the playlist. A screen that loses the network keeps playing.
- **Counts every play** and stores it locally until it can be uploaded.
- **Survives reboots** — `BootReceiver` relaunches it; a foreground `PlaybackService` keeps
  it alive; `PlayerSupervisor` and a `WatchdogAccessibilityService` bring it back to the
  front if something covers it.
- **Kiosk lock** — when provisioned as **device owner**, lock-task pins the app so it
  cannot be exited. The lock **re-arms on resume**, so it cannot be dropped by accident.
- **Updates itself** over the air.
- **Keeps honest time** — `SignageClock` anchors to the server's `Date` header, so a TV
  with a wrong clock still schedules correctly.

### 4.2 Getting out of kiosk mode (maintenance)

Two ways, both ending at the PIN:

- **TV remote:** `Up, Up, Down, Down, OK` within 5 seconds.
- **Touch screen:** **seven quick taps in the top-left corner**.

Either reveals the PIN prompt. A **correct** PIN (the screen's `maintenance_pin`) is the
only thing that drops the lock — the gesture alone never does. From there the operator can
change the server URL, unlink the device, or set the launcher role.

> The PIN is withheld from a screen that authenticated with no credential, because device
> ids are guessable.

### 4.3 Sync — how the TV stays current

`GET /api/screens/{device_id}/sync?since=<marker>`

- Default cadence **60s** (configurable, clamped 15s–3600s). The server returns the
  interval in the body and an `X-Sync-Interval-Seconds` header.
- **`204 No Content`** when nothing changed — the cheap common case. The marker is the
  latest of `screen.assignment_updated_at`, `playlist.updated_at`, `group.updated_at`.
- On failure the app backs off (5s → 300s) instead of hammering.
- An **empty local playlist always requests a full snapshot**, so a repaired database
  cannot get stuck empty behind a matching marker.

**What the response carries:** the playlist with resolved media URLs, fit mode, rotation,
operating mode/hours, status, the maintenance PIN, the target app version, and any
**pending command** (`bring_to_front`, `deregister`, `reset`).

**Which playlist a screen gets** (`resolve_screen_playlist`):
```
1. Active emergency broadcast   (screen → group → all)
2. The screen's own playlist
3. The nearest ancestor group's playlist
4. The first dynamic group whose criteria it matches
```

**Items are filtered before sending:** anything whose paid window has **ended** is dropped
entirely, so the TV stops caching media for dead campaigns. Items that have not *started*
are deliberately still sent — the screen must have tomorrow's advert cached today.

### 4.4 Counting plays (proof of play)

1. Every play writes a local event: which media, start, finish, whether it completed, and
   why it ended.
2. `ProofOfPlayWorker` uploads batches to `POST /api/screens/play-logs/batch`.
3. The server inserts with **`ON CONFLICT DO NOTHING` on `event_id`** — so a retried or
   replayed batch is **deduplicated**, never double-counted.
4. Device timestamps are preserved alongside clock-corrected ones, so a TV with skewed time
   still reports truthfully.
5. `aggregate_play_logs` (every 15 min) rolls raw events into `PlayLogHourlyRollup` — this
   is what reports and analytics read. Raw logs are pruned; rollups are kept far longer.

A batch rejected with a permanent 4xx is **narrowed** (batch size halved) rather than
discarded, so one bad row cannot destroy a whole batch of good events.

### 4.5 Updating itself

1. Sync tells the TV the target `version_code` and the APK URL + `sha256`.
2. `UpdateGate` **refuses** to install without a URL and a digest, and verifies the digest
   after download.
3. Device-owner screens can install silently; others prompt.
4. Success or failure is reported back. Three consecutive failures → automatic rollback.

---

## 5. How the three parts connect

### 5.1 The full signal path

```
TENANT DASHBOARD                 BACKEND (FastAPI)                    TV SCREEN
      │                                 │                                 │
      │ create booking ────────────────►│                                 │
      │                                 │ writes PlaylistItem             │
      │                                 │ bumps playlist.updated_at       │
      │                                 │                                 │
      │                                 │◄──── GET /sync?since=marker ────│ every 60s
      │                                 │ 200 + playlist (or 204)         │
      │                                 │─────────────────────────────────►│
      │                                 │                                 │ downloads media
      │                                 │                                 │ plays loop
      │                                 │◄──── POST /play-logs/batch ─────│ counts
      │                                 │ dedup on event_id               │
      │                                 │ rollup every 15 min             │
      │ report / invoice PDF ◄──────────│                                 │
      │                                 │◄──── POST /heartbeat ───────────│ online status
      │ alerts (websocket) ◄────────────│                                 │
```

### 5.2 Why a booking reaches the television

The critical link is **`playlist.updated_at`**. `sync_tv` answers `204` until that marker
moves. Every path that changes what a screen should play — placing an advert, editing a
window, selling an extension — calls `bump_playlist`. A change that forgets to bump reaches
the dashboard and **never reaches the TV**.

### 5.3 Realtime push

Two websockets (`/api/ws/dashboard/ws`, `/api/ws/{device_id}/ws`) backed by Redis pub/sub
carry alerts and commands without waiting for the next poll. They are an **accelerator, not
a dependency** — everything still works on the polling path if Redis is down.

### 5.4 Publishing a new TV app version — the full chain

**All four steps are required. Doing only step 1 ships nothing.**

1. Bump `versionCode` / `versionName` in `android-tv/app/build.gradle.kts` and commit.
2. Build the APK: `./gradlew :app:assembleProductionRelease`.
3. Create a GitHub Release and upload the APK.
4. **Super-admin → Releases**: create the `AppRelease` row with the asset URL, its
   `sha256`, and `rollout_state=released`. *Only this step moves the fleet.*

⚠️ Release builds are signed with the **per-machine debug keystore** unless a
`keystore.properties` is present. An APK built on a different machine is rejected by
Android as a signature mismatch, so **build on the machine that built the previous
release**, or set up a real release key.

### 5.5 Background jobs

| Job | Cadence | Purpose |
|---|---|---|
| `reconcile_alerts` | every minute | Raise/resolve fleet alerts |
| `recover_stuck_processing` | every 5 min | Finish media whose worker died |
| `aggregate_play_logs` | every 15 min | Raw plays → hourly rollups |
| `prune_play_logs` | hourly | Drop raw events past retention |
| `prune_screenshots` | daily 03:30 | Storage hygiene |
| `prune_finished_bookings` | daily 03:45 | Tidy completed campaigns |
| `prune_play_log_rollups` | daily 04:00 | Long-horizon retention |

These run on **arq over Redis**. With Redis unreachable, they all stop.

### 5.6 Alerts

Raised and resolved automatically: `screen_offline` (after 1 min), `playback_error`,
`screen_idle`, `low_storage` (under 500 MB), `update_failed`, `content_failed`,
`campaign_ending` (within 7 days), `plan_underused`. Screens that are *scheduled off* do
not raise offline alerts.

---

## 6. Things to know before changing anything

### 6.1 Tenant isolation is enforced in exactly one place

`TenantScope.query()` is the funnel. It applies the organisation filter **and** excludes
soft-deleted rows. Forty-odd call sites rely on it; a query that bypasses it bypasses both
rules. Cross-tenant reads deliberately return **404, not 403** — a 403 confirms the row
exists.

### 6.2 A booking can silently fall off its screens

`AdPlacementTarget.playlist_item_id` is `ON DELETE SET NULL` (so an operator deleting an
item by hand leaves the booking as a record of what was sold), but `PlaylistItem` cascades
from **both** its playlist and its content. So deleting a playlist or a creative nulls the
link on every booking that used it — the booking survives reading **"Running"** and
**"Paid"** while playing on nothing.

The dashboard shows this as **"Not playing — Put back on screens"**, and
`POST /placements/{id}/replace` repairs it from the original assignment date.

### 6.3 Portability

The test suite and local development run on **SQLite**; production is **Postgres**.
Postgres-only SQL (`date_trunc`, `GREATEST`) must not be used on any path the tests
exercise — it has broken reports before. Bucket in Python instead.

### 6.4 Architecture is enforced by CI

`.importlinter` holds the backend layering (`routers → services → repositories → models`)
and `frontend/.dependency-cruiser.cjs` holds the frontend layering
(`app → components → lib`). Both run in GitHub Actions on every push. Three known upward
imports are pinned as documented debt; any **new** one fails the build.

---

## 7. Current production status

Accurate as of the last check — these are environment problems, not code defects.

| Item | State |
|---|---|
| **Object storage (R2)** | ❌ **Not configured.** `/api/media/*` returns 503, so **no TV can download any media and nothing plays.** Needs `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` in Render. |
| **Redis** | ❌ Unreachable. Uploads, realtime push and **all scheduled jobs** are stopped. |
| **Database** | ✅ Postgres (Supabase, ap-northeast-2) |
| **Legacy device auth** | ⚠️ Still enabled |

🔴 **Security:** R2 credentials are hardcoded in `frontend/src/lib/r2-signer.ts` and ship to
every browser, and an earlier pair was committed to a public repo. **Rotate the keys**,
move them to Render environment variables, and remove the client-side signer.

> Verified on real hardware: with a correctly configured backend, the same tablet and the
> same app played adverts within seconds and counted every play. The player and the API are
> sound; the outage is entirely configuration.

---

## 8. Where things live

| Area | Path |
|---|---|
| API routers | `backend/routers/` |
| Business logic | `backend/services/` |
| Domain model | `backend/models.py` |
| Tenant isolation | `backend/tenancy.py` |
| Client PDFs | `backend/reports/` |
| Background jobs | `backend/worker.py` |
| Packages catalogue | `backend/billing.py` |
| Admin console | `frontend/src/app/admin/` |
| Tenant dashboard | `frontend/src/app/dashboard/` |
| API client | `frontend/src/lib/api.ts` |
| TV player | `android-tv/app/src/main/java/com/olrac/signage/` |
| Tests | `tests/` — run each file directly; grouped pytest runs flake |
