# MR Signage — Work Order for Antigravity (Gemini Pro)

**Repo:** `E:\IMP projects\MR SIGNAGE`
**Goal:** turn the working MVP into a commercial multi-tenant digital-signage SaaS
running 80 Android TVs now, 500+ later.

**Read sections 0–3 before writing any code.** Section 1 contains hard-won device
knowledge that is not discoverable from the codebase; ignoring it will cost you days.

---

## STATUS — updated 2026-08-07

**P0-P7 are code-complete and verified.** The backend, frontend, and Android TV application have reached the end of the planned roadmap. All functionality across the entire stack compiles, lints, builds, and passes integration and unit tests.

### Known Gaps
While the P0-P7 features are structurally complete, there are several outstanding issues that have been deferred:
- **Hardware Verification**: The player has never been tested on physical Realtek Android 14 budget panels for power-cut recovery, immersive mode hardiness, and watchdog `onCreate` latency.
- **APK Staged Rollout Targeting**: The backend assigns target version codes to screens, but complex deployment rings (e.g. "Canary channel", "Beta channel") are not fully supported natively without explicit manual screen management.
- **Campaign Backfill Edge Cases**: If a screen goes offline for weeks and returns, dumping a massive 30-day payload of `PlayEvent` logs could create heavy backend spikes or duplicate roll-up aggregations during race conditions.
- **Dashboard Load Handling**: At 500+ screens, the real-time websocket and polling architectures might face memory/CPU load on a single instance without horizontal scaling of the real-time servers.

---

## 0. Ground rules

1. **Do not redesign the stack.** FastAPI + SQLAlchemy + Alembic + Next.js 16 +
   Kotlin/Compose are fixed. Add Postgres, Redis, FFmpeg workers, WebSockets *around*
   what exists.
2. **Do not rebuild what already works.** Section 2 lists what is done and verified.
   Re-implementing it wastes budget and risks regressions.
3. **Never break offline playback or boot recovery.** These are requirements #1 and #2.
   Any player change must be re-verified against them on real hardware.
4. **Every phase ships with a runnable test.** Backend tests live in `tests/` and are
   collected as subprocesses by `tests/conftest.py` — read that file's docstring before
   adding a test, and do **not** add a `test_*` function to those script files.
5. **Migrations must preserve data.** Back up to `backups/` first, verify row counts after.
6. **No secrets in source.** Env only; `.env` is gitignored, `.env.example` documents shape.
7. Frontend must pass `npx tsc --noEmit`, `npm run lint`, `npm run build`.
   `frontend/AGENTS.md` warns this is Next.js 16 — read `node_modules/next/dist/docs/`
   before using an API you are unsure about. It differs from older Next.
8. Verify claims by running things. Do not report a phase complete on "it compiles".

---

## 1. Device knowledge — the most important section

The customer runs **budget Realtek "2K D5STV" Android 14 panels**. A separate, *already
working in production* watchdog app solved auto-launch on these. Its source is preserved
at `android-watchdog/` (originals, incl. signed APK + keystore, at
`E:\IMP PROJECT 2\ablesign launcher`). Read `android-watchdog/HOW_IT_WORKS.md`.

### The three techniques that actually work

| Problem | What fails | What works |
|---|---|---|
| App can't start itself at boot | `startActivity()` from a `BroadcastReceiver` or foreground service — Android 10+ blocks background activity starts, **silently** | `AlarmManager` + `PendingIntent.getActivity()`. The alarm is dispatched by the **system process**, which is exempt |
| Can't find the target app | `getPackageManager().getLaunchIntentForPackage(pkg)` returns **null** on Android 11+ (package visibility) | Explicit `ComponentName(pkg, pkg + ".MainActivity")` |
| Boot trigger never fires | `onServiceConnected()` — this OEM never calls it at boot | An **AccessibilityService**'s `onCreate()`, which Android always calls at boot |

### What survives a reboot on this OEM

| Setting | Survives? |
|---|---|
| `enabled_accessibility_services` | **YES** — stored in SettingsProvider DB. The only reliable persistence. |
| `accessibility_enabled` | **YES** |
| `deviceidle whitelist` (for the player) | YES |
| `appops SYSTEM_ALERT_WINDOW` | NO — OEM resets it |
| `pm disable-user` on the stock launcher | NO — OEM resets it |
| `device_config` | NO — OEM resets it |

**Consequence:** do not build kiosk mode on SYSTEM_ALERT_WINDOW or on disabling the
stock launcher. Those get wiped. Use HOME-category launcher + the accessibility trick.

Realistic boot timing on these panels: Android ready ~45–60 s, watchdog `onCreate`
~50–65 s, +12 s settle delay, **player visible ~65–80 s**. Do not "optimise" the 12 s
delay below ~8 s; the system is not settled and the launch is dropped.

### Already applied for you
`android-tv/.../boot/PlayerLauncher.kt` now implements the AlarmManager +
ComponentName technique, and both `BootReceiver` and `PlaybackService.launchPlayer()`
route through it. **This has not been compiled** — no JDK/Gradle was available in the
authoring environment. Your first task is to build it (§4, P0).

### Decision you must make
Either (a) fold the accessibility-service trick into the MR Signage player itself, or
(b) keep shipping the separate `com.ablesign.bootlauncher` watchdog alongside it and
just repoint its `ABLESIGN` constant to `com.olrac.signage`. **(b) is lower risk and is
already proven in the field** — spec §5 says "no separate watchdog app", but that rule
is about *recovery logic*, not about the boot shim. Recommend (b) for boot + in-app
`PlayerSupervisor` for runtime recovery. Confirm with the owner before choosing (a).

---

## 2. What already exists and is verified

Audited 2026-08-07: backend suite 3 passed / 0 warnings, live HTTP E2E passed,
Android 16 unit tests passed, frontend clean.

**Backend** — FastAPI, SQLAlchemy, Alembic (head `e6b8c0d3f5a2`), JWT + bcrypt.
Routers: `auth users screens groups content playlists billing`.
- Multi-tenancy with `Organization` + `organization_id` on every entity, enforced through
  a shared `TenantScope` / `require_tenant_roles` dependency. **Verified by
  `tests/test_tenant_isolation.py`: 24 admin routes probed cross-tenant, zero leaks.**
- Roles `owner` / `editor` / `viewer`. No platform super-admin yet.
- Plans + subscriptions + quota enforcement (409 screens, 413 storage), Razorpay webhook,
  mock provider. Verified by `tests/test_quotas.py`.
- Playlists with per-item date range, weekday mask, time window, and transitions
  (`none fade slide_* zoom` + duration ms).
- Flat `ScreenGroup` with group→playlist inheritance (`effective_playlist_id`).
- Versioned sync: `GET /api/screens/{device_id}/sync?since=` → **204 Not Modified** when
  unchanged. Returns `sync_interval_seconds` and `app_version`.
- Basic telemetry columns on `Screen`: `playback_state`, `current_item_id`, `last_error`,
  `last_error_at`, `app_version`, `storage_used`.
- R2/S3 via boto3 with presigned URLs; local `uploads/{org_id}/` fallback.

**Frontend** — Next.js 16, React 19, Tailwind 4, shadcn/ui, TanStack Query, Zustand,
dnd-kit, next-themes. Routes: overview, screens, content, playlists, playlist detail,
team, billing, login. White + navy theme via semantic tokens in `globals.css`
(`--rail --panel --brand --hairline`), light+dark, all pairs pass WCAG AA.
Route transitions via React `<ViewTransition>` (`experimental.viewTransition: true`).

**Android** — Kotlin/Compose, Media3, Coil, Room, WorkManager, Retrofit/OkHttp.
`ApiClient` (configurable base URL + dev/production flavors), `DeviceState`,
`ScheduleEvaluator` (handles overnight windows), `TransitionSpec`, `PlaybackService`
(foreground, 60 s poll), `ConnectivityWatcher`, `PlaylistSynchronizer`,
`SyncBackoffPolicy`, `HeartbeatReporter`, `PlaybackTelemetry`, `PlayerSupervisor` **does
not exist yet**.

---

## 3. Gap analysis — what the new goal needs

| # | Requirement | State | Phase |
|---|---|---|---|
| Postgres instead of SQLite | SQLite only | **P1** |
| Redis (cache, queues, presence, locks) | absent | **P1** |
| FFmpeg workers + multi-rendition transcode | absent | **P2** |
| Device capability profile + rendition selection | absent | **P2** |
| Auto device enrollment + device credentials | 6-digit pairing only | **P3** |
| Proof-of-play, offline-queued, deduplicated | absent | **P4** |
| Analytics + campaign pages + charts | absent | **P5** |
| Reports PDF/CSV/Excel | absent | **P5** |
| WebSockets + fallback polling | polling only | **P6** |
| `PlayerSupervisor` self-healing | absent | **P0** |
| Kiosk/immersive hardening | partial | **P0** |
| Atomic playlist switch + SHA-256 verify | swap exists, no checksum | **P0** |
| Storage manager (keep current/previous/upcoming) | naive cleanup | **P0** |
| Clock-drift offset | absent | P4 |
| Emergency broadcast override | absent | P6 |
| Nested/hierarchical groups | flat only | P6 |
| Screenshot verification | absent | P7 |
| Staged remote app updates | version reported only | P7 |
| Platform super-admin role | absent | P3 |

---

## 4. Phases

Do them in order. Each has a **Definition of done** that must actually pass.

### P0 — Player reliability (highest priority, do first)

The customer's #1 and #2 requirements. Nothing else matters if a TV shows black.

1. **Build the Android project.** There is no Gradle wrapper — run `gradle wrapper` from
   Android Studio's terminal so `./gradlew` works from CLI. Then compile
   `PlayerLauncher.kt` (added but never compiled) and fix any errors.
2. **`PlayerSupervisor`** inside the player process (no second app). Monitors ExoPlayer
   state, current media, playlist state, download state, storage, crashes, codec errors.
   Recovery ladder, in order: retry playback → skip damaged item → reload playlist →
   restart player activity → report error to server. Never leave a black screen.
   Wire `Player.Listener.onPlayerError` and a periodic liveness check (is position
   advancing?) — a frozen decoder does not always emit an error.
3. **Kiosk hardening.** Immersive sticky, hide status+nav bars, `FLAG_KEEP_SCREEN_ON`,
   disable screensaver, swallow the Home/Recents keys where the OEM allows. Do **not**
   rely on SYSTEM_ALERT_WINDOW or `pm disable-user` (§1: they reset on reboot).
4. **Atomic playlist switch with integrity.** Add `sha256` + `size_bytes` to every media
   row server-side. Player: download to `*.part` → verify SHA-256 → rename into place →
   only when *every* item verifies, switch version N→N+1 in one Room transaction.
   Any failure ⇒ keep playing the old version. Keep current + previous + a fallback playlist.
5. **Storage manager.** Keep current, previous, and upcoming-scheduled media; delete
   unreferenced and temp files; re-download corrupted. On low storage, clean safe files
   then report a warning. Must never delete the playlist currently on screen.

**Definition of done:** on a real Realtek Android 14 TV —
(a) pull the power, restore it, player is on screen and playing cached ads within ~90 s,
no input; (b) same with the router off all day; (c) a deliberately corrupted media file
is skipped and the rest keeps playing, with the error visible in the dashboard;
(d) interrupting a download mid-way leaves the old playlist playing untouched.
Plus `./gradlew test` green.

### P1 — Postgres + Redis foundation

1. Postgres via Docker Compose; keep SQLite working for local tests only.
   Migrate with Alembic — **verify row counts before/after**, back up first.
   Watch for SQLite-only assumptions (`BigInteger` autoincrement, naive datetimes,
   `func.coalesce` behaviour).
2. Redis for: screen online presence (TTL key per device), response caching,
   rate limiting, distributed locks, and the job queue for P2.
   Use one `redis.asyncio` pool created in the FastAPI lifespan.
3. Make presence authoritative from Redis, not from a `last_seen` scan — at 500 screens
   the current `GET /api/screens/` full-table status sweep will not hold up.

**Done:** compose stack up; suite green against Postgres; existing data migrated intact;
`/api/screens/` p95 under 200 ms with 500 seeded screens.

### P2 — Media pipeline (FFmpeg workers + renditions)

1. Worker process (RQ or Arq on Redis — **not** Celery, too heavy here) consuming an
   upload job queue.
2. On upload: probe with ffprobe, then generate **1080p / 720p / 540p / 360p H.264**
   + thumbnail + metadata. **Never crop or rotate** — portrait stays portrait. Carry
   `width`, `height`, `rotation`, `duration_ms`, `codec`, `sha256`, `size_bytes` per
   rendition. Store all renditions in R2 under `{org_id}/{media_id}/{rendition}.mp4`.
3. Upload returns immediately with status `processing`; dashboard shows progress; the
   playlist may reference the media but the player only receives *ready* renditions.
4. `MediaRendition` table; `Content` becomes the logical asset, renditions are the files.

**Done:** upload a 4K HEVC file and a portrait phone video; both produce correct H.264
renditions with orientation preserved; a 1 GB upload does not block the API; failed
transcode marks the asset `failed` with a readable reason and never reaches a TV.

### P3 — Device identity and enrollment

Replace per-TV 6-digit pairing for mass deployment. **Keep pairing as a fallback.**

1. `Enrollment token` per company/deployment (long-lived, revocable, QR/short-code).
   Installer signs in once *or* the APK is built with an enrollment token.
2. Device self-registers → server issues `device_id` + `device_secret` (store only a
   hash server-side) + `company_id` + `installation_id`.
3. Device auth is separate from user auth: device presents id+secret → short-lived
   device JWT (~1 h) with refresh. **Never** persist human credentials on the TV.
4. Device lifecycle: revoke, deactivate, move to group, rename, factory-reset identity.
5. Add platform super-admin role above company owner.

**Done:** 10 TVs enrolled with zero per-device manual entry; revoking a device causes its
next sync to 401 and stop receiving media; device token expiry refreshes without
interrupting playback; `tests/test_tenant_isolation.py` still passes.

### P4 — Proof of play (offline-first)

1. Room table on the TV: `event_id` (client-generated **UUID**), screen, media, playlist,
   campaign, `started_at`, `finished_at`, `duration_ms`, completion status, error.
2. Record every playback locally, online or not. Upload in batches; delete locally only
   after server ack. Server **deduplicates on `event_id`** (unique index) so retries
   never double-count.
3. Clock drift: sync a server-time offset on every heartbeat; stamp events with corrected
   time and also send raw device time. Cheap TVs have wrong clocks — this is required for
   billing-grade reports.

**Done:** disconnect a TV for 24 h with ads playing, reconnect — every play appears
exactly once; forcibly replay the same batch three times, counts do not change; events
recorded with a 3-hour-wrong TV clock land at the correct time.

### P5 — Analytics, dashboard, reports

1. Aggregate tables/materialised views for plays by day/hour/screen/campaign — do not
   compute reports by scanning raw events at 500 screens.
2. Campaign analytics page: currently playing count, assigned/online/offline, today /
   yesterday / week / lifetime, plays by day/hour/location/screen, success vs failure %.
3. Fleet dashboard per §17 and detailed screen page per §18 (all listed fields).
4. Reports for advertisement / campaign / screen / playlist / location / date range,
   exportable to **PDF, CSV, Excel**, presentable to an advertising client.

**Done:** a month-long campaign report generates in under 5 s over 100k events and the
totals reconcile exactly with raw event counts.

### P6 — Realtime and control

1. WebSocket per device (auth by device token) for push: playlist changed, emergency,
   command. **Polling stays as fallback** — if the socket is down the TV must still
   converge via the existing 60 s sync. Never make WS the only path.
2. Live dashboard: current ad, playlist position, remaining seconds per screen (§19).
3. Emergency broadcast: override all/location/group/individual; cancel restores the
   normal playlist. Must also work if the socket is down (picked up on next poll).
4. Hierarchical groups (region → city → shop) plus dynamic groups (all portrait,
   all 720p, all reception).

**Done:** playlist change reaches an online TV in under 5 s via WS; with WS blocked it
still arrives within one poll interval; emergency reaches 80 screens in under 10 s;
cancelling restores the previous playlist everywhere.

### P7 — Fleet operations

1. Screenshot verification: dashboard requests a frame; the player captures **its own
   rendered surface** (never the camera), uploads to R2, stored with screen/timestamp/
   media/playlist. Note: `MediaCodec`-backed video surfaces are not captured by naive
   `View.draw()` — use `PixelCopy` or an ExoPlayer frame callback.
2. Staged app rollout: 5 test TVs → 20 → all. Show current/latest version, update
   required/successful/failed per screen. Roll back automatically on repeated failure.
   Silent install requires **device-owner** provisioning; otherwise Android shows a
   prompt. Document the provisioning procedure; do not pretend it is avoidable.

**Done:** a bad build pushed to the 5-TV canary does not reach the rest of the fleet, and
the canary TVs recover to the previous version and keep playing.

---

## 5. Infrastructure

Target 80 screens now, 500+ later, cheap. Docker Compose: Cloudflare → reverse proxy →
FastAPI containers → Postgres → Redis → workers → R2. **No Kubernetes.** Add CI running
the full suite in §6 on every push.

Cost note: R2 has no egress fees, which is why media must be downloaded **directly from
R2 via signed URL**, never proxied through FastAPI (spec §10). The API only issues
permissions, metadata, playlists, commands and signed URLs.

---

## 6. Regression suite — run after every phase

```bash
backend\venv\Scripts\python.exe -m pytest tests -q
backend\venv\Scripts\python.exe tests\validation.py
cd frontend && npx tsc --noEmit && npm run lint && npm run build
cd android-tv && gradlew test
```

Plus, from P0 onward, on real hardware every time: **power-cut → plays**,
**no internet → plays**, **new ad → appears**. Automated tests cannot prove those.

---

## 7. Definition of done for the whole programme

On real Realtek Android 14 TVs:

1. Power cut → boots straight into full-screen ads, no input, no internet needed.
2. Internet down for a day → cached ads keep playing on the correct schedule.
3. Reconnect → new ads appear within about a minute, automatically.
4. A 4K HEVC upload plays correctly on a 1 GB-RAM 1080p panel, orientation intact.
5. A corrupted or unsupported file never blacks out a screen.
6. Two companies cannot see each other's anything.
7. Every play is counted exactly once, including plays that happened offline.
8. A client-ready PDF report proves where, when, and how many times an ad played.
9. A new player version reaches all TVs without a site visit.
10. One person can understand and control 500 screens from one dashboard.

Until all ten hold on hardware, the programme is not done.
