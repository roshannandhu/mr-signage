# MR Signage — Build Goal & Agent Work Order

**Target:** a cloud digital-signage SaaS equivalent to AbleSign — businesses manage ad
playback on many Android TVs from one dashboard, with offline-resilient playback,
automatic boot recovery, smooth transitions, per-tenant isolation, and subscription billing.

**How to use this document (for the coding agent):**
Work top-down, one phase at a time. Do **not** start a phase until the previous phase's
acceptance tests pass. Every phase lists (a) the exact problem, (b) files to touch,
(c) the approach, (d) acceptance tests that must pass before moving on. Run the full
regression suite in §7 at the end of every phase. Do not mark a phase done on "it
compiles" — it is done when its acceptance tests pass.

---

## 1. Product requirements (the contract)

| # | Requirement | Phase |
|---|---|---|
| R1 | Player installs on any supported Android TV | P1 |
| R2 | Many TVs managed from one dashboard | ✅ done |
| R3 | Upload videos/images to cloud | ✅ done |
| R4 | Playlists with date/time scheduling | ✅ done |
| R5 | Assign content to individual TVs or groups | ✅ done |
| R6 | Content cached locally; **plays with no internet** | **P0 — broken** |
| R7 | **Auto-launch + resume after reboot / power cut** | **P0/P1 — broken** |
| R8 | Per-TV status: online / offline / **playing / error** | P4 |
| R9 | Remote player app update, no site visit | P7 |
| R10 | Separate companies: own accounts, TVs, storage, content | P5 |
| R11 | Subscription billing by TV count / storage / features | P6 |
| R12 | Smooth transitions, admin-controlled per item | P2 |
| R13 | Short-form ads (video < 90s) + photos, no black gaps | P2 |
| R14 | Auto content update when network returns | P3 |

---

## 1a. Verified status — audit of 2026-08-07

Every phase P0–P7 is implemented. Independently re-verified end to end on this date:

| Check | Result |
|---|---|
| `pytest tests -q` | **3 passed, 0 warnings** |
| `tests/validation.py` | passed |
| Cross-tenant probe (new, `test_tenant_isolation.py`) | **24 admin routes + 5 listings — no leak** |
| Quota enforcement (new, `test_quotas.py`) | screen limit 409, storage 413, raise-quota-unblocks |
| Live E2E over HTTP | register→pair→upload→playlist→assign→sync→download→heartbeat **PASSED** |
| Versioned sync (P3) | 200 → **204 Not Modified** → 200 after edit |
| Android unit tests | **16 tests, 0 failures**; APK newer than newest source |
| Frontend | `tsc` clean, ESLint clean, production build 11 routes |

Defects found and fixed during this audit:
1. **Test suite was not runnable as a suite.** All three backend scripts passed alone
   and failed together — `backend/database.py` builds the engine at import time, so
   the first module to import `backend` bound the engine and the other two queried a
   torn-down database. Fixed at the root in `tests/conftest.py`: each script is now
   collected as its own subprocess.
2. **`e2e_test.py` was stale** — asserted `200` where the API correctly returns
   `201 Created` (upload, create playlist, add item). It would have failed against a
   healthy server.
3. **Pydantic `class Config`** (6 uses) deprecated and removed in V3 → `ConfigDict`.
4. **`declarative_base` imported from the removed `sqlalchemy.ext.declarative`** path.

Known gap, not a defect: **no Gradle wrapper** in `android-tv/`, so the
`./gradlew test` command below only works from Android Studio's bundled Gradle.
Run `gradle wrapper` once from Android Studio's terminal to make CLI builds work.

---

## 2. Current state — audit (original, pre-implementation)

**Working and verified:** screen groups, playlist versioning with `204 Not Modified`
sync, per-item date/weekday/time-window scheduling, offline media caching in Room +
`filesDir`, role-gated API (`owner`/`editor`/`viewer`), presigned R2/S3 URLs,
admin dashboard (screens/content/playlists/team). Migration at `b71f3d902ac4`.

**Blocking defects found in the player — these break the core promise:**

- **D1 (critical, R6+R7).** `MainActivity.onCreate` gates the whole UI behind a live
  network call. `api.register(...)` runs, and only on success is `isPaired` set true.
  With no internet at boot the call throws, the coroutine dies, and the TV sits on
  **"Initializing…" forever — it never reaches `PlayerScreen`, so cached ads never
  play.** Paired state is never persisted. This is the single most important bug in
  the repo.
- **D2 (critical, R7).** `BootReceiver` calls `startActivity` from a `BroadcastReceiver`.
  Android 10+ blocks background activity starts, so **auto-launch after reboot silently
  fails on any modern TV.** It also only listens for `ACTION_BOOT_COMPLETED`, missing
  `LOCKED_BOOT_COMPLETED` and vendor quick-boot actions.
- **D3 (critical, R1).** The backend URL is hardcoded to `http://10.0.2.2:8000/` — the
  emulator loopback alias — in `MainActivity`, `SyncWorker`, and `HeartbeatWorker`.
  **The app cannot talk to a real server from a real TV.** Cleartext HTTP is also
  blocked by default on Android 9+.
- **D4 (R12/R13).** No transitions. `PlayerScreen` swaps items by index with a hard cut,
  and builds a **new `ExoPlayer` per item**, so every video starts with a black flash
  while it buffers.
- **D5 (R14).** `WorkManager` periodic minimum is 15 minutes, so new ads can take up to
  15 min to appear. There is no immediate re-sync when connectivity returns.
- **D6 (R8).** Heartbeat reports online + storage only — never *playing* or *error*.
- **D7 (R10).** No tenant model at all. Every user sees every screen, playlist, and file.
- **D8 (R11).** No subscription, plan, or quota concept.
- **D9.** No wake lock / `keepScreenOn`; the TV can blank mid-playlist.

**Verdict:** the management plane is in good shape; the **playback plane is not
production-viable**. Phases are ordered accordingly — reliability first, SaaS second.

---

## 3. Phase P0 — Offline-first playback (fix D1)

**Problem.** A TV with cached ads and no internet shows "Initializing…" instead of playing.

**Files:** `android-tv/.../MainActivity.kt`, new `data/DeviceState.kt`.

**Approach.**
1. Persist pairing state to `SharedPreferences` (`is_paired`, `screen_name`) the moment
   pairing succeeds. Treat "has local playlist rows" as an independent proof of pairing.
2. On launch, decide the screen from **local state only**:
   - `is_paired == true` → render `PlayerScreen` **immediately**, before any network call.
   - else → show pairing code UI and attempt registration.
3. Move `api.register(...)` off the critical path: wrap in try/catch, run it as a
   background refresh that can *upgrade* state but can never block playback.
4. If paired but the local playlist is empty, show a neutral branded idle screen
   (not an error) and retry sync in the background.

**Acceptance tests (all must pass):**
- **T0.1** Pair a device, let it cache media, force-stop, **enable airplane mode**, cold
  launch → ads begin playing within 5 seconds, no "Initializing…" state.
- **T0.2** Same as T0.1 but reboot the device instead of force-stop → ads resume.
- **T0.3** Unpaired device with no network → shows a clear "no connection" pairing
  message, and recovers to the pairing code automatically when network returns, with
  no restart.
- **T0.4** Unit test: `MainActivity` state resolver returns `Playing` when
  `is_paired=true` and the network call throws.

---

## 4. Phase P1 — Reliable auto-launch & kiosk survival (fix D2, D3, D9)

**Problem.** Auto-start after power cut does not work on Android 10+; the app cannot
reach a real server; the screen can blank.

**Files:** `AndroidManifest.xml`, `receivers/BootReceiver.kt`, new
`service/PlaybackService.kt`, new `network/ApiClient.kt`, `build.gradle.kts`,
new `res/xml/network_security_config.xml`.

**Approach.**
1. **Configurable server URL (D3).** One `ApiClient` object; base URL from
   `BuildConfig.API_BASE_URL` (Gradle field, overridable per build) **and** an on-device
   setup screen storing an override in `SharedPreferences`. Delete all three hardcoded
   `10.0.2.2` literals. Default release builds to HTTPS; permit cleartext only for an
   explicit dev flavor via `network_security_config.xml`.
2. **Auto-launch (D2).** Belt-and-braces, because no single mechanism is reliable:
   - Add `<category android:name="android.intent.category.HOME"/>` +
     `DEFAULT` to `MainActivity` so the app can be set as the TV's **default launcher** —
     this is how production signage players survive reboot; document it as a setup step.
   - Keep `BOOT_COMPLETED`, add `LOCKED_BOOT_COMPLETED` and
     `android.intent.action.QUICKBOOT_POWERON`; mark the receiver `directBootAware`.
   - From the receiver start a **foreground service** (`PlaybackService`, allowed from
     boot) which owns sync scheduling and launches/keeps the activity alive, instead of
     calling `startActivity` directly from the receiver.
3. **Stay awake (D9).** `FLAG_KEEP_SCREEN_ON` on the player window, immersive/full-screen
   (hide status and nav bars), and a partial wake lock held by `PlaybackService`.
4. Handle `ACTION_MY_PACKAGE_REPLACED` so the player restarts itself after an app update.

**Acceptance tests:**
- **T1.1** Set as default launcher, hard power-cut the TV, restore power → app is
  foregrounded and playing within 60s of boot, no remote/user input.
- **T1.2** Same with app *not* set as launcher, via boot receiver + service path →
  app foregrounds (document any OEM where this fails; launcher mode is the supported path).
- **T1.3** Point a physical TV at a LAN/remote server through the configured URL —
  pairing, sync, and media download all succeed. No `10.0.2.2` string remains in the
  codebase (`grep -r "10.0.2.2" android-tv/src` returns nothing).
- **T1.4** Leave playing for 2 hours untouched → screen never blanks, playback never stops.
- **T1.5** Install an updated APK over the top → player relaunches on its own.

---

## 5. Phase P2 — Smooth transitions & gapless playback (fix D4; R12, R13)

**Problem.** Hard cuts and a black flash between items; admin cannot control transitions.

**Files:** backend `models.py`, `schemas.py`, `routers/playlists.py`, new Alembic
migration; frontend playlist builder; Android `PlayerScreen.kt`, `PlaylistItemEntity.kt`,
`network/ApiService.kt`.

**Approach.**
1. **Data model.** Add to `PlaylistItem`: `transition` (enum: `none`, `fade`,
   `slide_left`, `slide_right`, `slide_up`, `slide_down`, `zoom`) and
   `transition_ms` (int, default 600, clamp 100–3000). Default `fade`. Migration must
   backfill existing rows. Also add a playlist-level `default_transition` that items
   inherit unless overridden.
2. **Admin UI.** In the drag-and-drop builder, each item row gets a transition dropdown
   + duration slider, editable **per item, in sequence** (this is R12 — the operator sets
   how each ad hands off to the next). Add a "apply to all items" shortcut and a small
   live preview animation so the choice is visible without a TV.
3. **Player rendering.** Replace index-swap with Compose `AnimatedContent` /
   `Crossfade` driven by the item's transition spec.
4. **Gapless video (D4, R13).** Keep **two `ExoPlayer` instances** and alternate them:
   while item *N* plays on player A, item *N+1* is prepared on player B; the transition
   crossfades the two `PlayerView`s. Since all ads are < 90s, preparing one item ahead is
   cheap. Reuse players across items — never build a new one per item. Set
   `ExoPlayer.setVideoScalingMode` and pre-buffer so first frame is ready before the
   transition starts.
5. Images use the same two-surface crossfade via Coil, preloaded one ahead.

**Acceptance tests:**
- **T2.1** Playlist of 5 mixed image/video items loops for 10 minutes with **no black
  frame** between items (verify by screen recording, frame-inspect each boundary).
- **T2.2** Each transition type renders correctly and honours its configured duration
  (±100 ms).
- **T2.3** Changing an item's transition in admin reaches the TV on the next sync and
  takes effect without an app restart.
- **T2.4** A 90-second 1080p video plays start-to-finish with no stutter or dropped
  frames while the next item preloads.
- **T2.5** Android unit test for the transition-spec resolver (item override → playlist
  default → `fade` fallback).

---

## 6. Phase P3 — Fast, network-aware updates (fix D5; R14)

**Problem.** New ads can lag 15 minutes; nothing reacts to the network returning.

**Files:** `PlaybackService.kt`, `SyncWorker.kt`, new `network/ConnectivityWatcher.kt`,
backend `routers/screens.py`.

**Approach.**
1. Keep the 15-min `WorkManager` job as the **safety net** only.
2. In `PlaybackService`, run a foreground polling loop (default 60s, server-configurable
   via a `sync_interval_seconds` field on the sync response) hitting the existing
   `204 Not Modified` endpoint — that call is already cheap, which is exactly what the
   versioned-sync work was for.
3. Register a `ConnectivityManager.NetworkCallback`; on `onAvailable`, trigger an
   immediate sync — this is the "updates when it comes back online" requirement.
4. Download new media **in the background while the current playlist keeps playing**;
   swap to the new playlist only once every file is cached (atomic switch, never a
   half-downloaded playlist).
5. Exponential backoff on repeated failure so an offline TV doesn't hammer the radio.

**Acceptance tests:**
- **T3.1** Publish a new ad from admin → appears on the TV within 90 seconds.
- **T3.2** TV offline for 1 hour, then reconnect → syncs within 60s of reconnection with
  no manual action.
- **T3.3** During a large video download, the currently-cached playlist keeps playing
  uninterrupted; the new playlist activates only after the download completes.
- **T3.4** Kill the server for 30 min → player keeps playing cached content, retries with
  backoff, and recovers when the server returns.

---

## 7. Phase P4 — Real status telemetry (fix D6; R8)

**Approach.** Extend heartbeat with `playback_state` (`playing` / `idle` / `error`),
`current_item_id`, `last_error`, and `app_version`. Backend stores it on `Screen` and
derives `online` from `last_seen`. Dashboard screen cards show a live state chip
(playing / idle / error / offline) plus the currently-playing item name and a
"last error" tooltip. Add an alert list for any screen in `error` or offline > 30 min.

**Acceptance tests:**
- **T4.1** Playing TV shows `playing` + correct current item in the dashboard.
- **T4.2** Corrupt a cached file → TV reports `error` with a message, **skips that item
  and keeps playing the rest** (never a stuck black screen).
- **T4.3** Unplug a TV → dashboard flips it to offline within the heartbeat window.

---

## 8. Phase P5 — Multi-tenancy (fix D7; R10)

**This is the largest and riskiest phase. Do it only after P0–P4 are green.**

**Approach.**
1. Add `Organization` (id, name, slug, plan_id, storage_quota_bytes, created_at).
2. Add `organization_id` FK to `User`, `Screen`, `ScreenGroup`, `Content`, `Playlist`.
   Migration must create a default org and assign every existing row to it — **no data loss**.
3. Enforce scoping in a **single shared dependency**, not per-route: a
   `tenant_scoped_query` helper / SQLAlchemy filter that every admin route routes
   through. Adding the filter in one place is both the smaller diff and the only way to
   guarantee no route leaks another tenant's data.
4. Pairing binds a screen to the org of the user who enters the code.
5. Storage keys become `{org_id}/{uuid}` so tenants cannot collide or guess paths.
6. Roles stay per-org (`owner`/`editor`/`viewer`).

**Acceptance tests:**
- **T5.1** Two orgs, two users: user A can never list, fetch, mutate, or delete any of
  org B's screens/content/playlists — test **every** admin endpoint, expect 404/403.
- **T5.2** Direct-ID probing (`GET /api/playlists/{B's id}` as A) returns 404, not data.
- **T5.3** A TV paired to org A never receives org B content.
- **T5.4** Migration on a copy of the production DB preserves 100% of existing rows under
  a default org. Verify row counts before/after.
- **T5.5** Uploaded files land under the correct org prefix; presigned URLs cannot be
  used to reach another org's objects.

---

## 9. Phase P6 — Subscriptions & quotas (fix D8; R11)

**Approach.** `Plan` (name, monthly/yearly price, max_screens, max_storage_bytes,
feature flags) + `Subscription` (org, plan, status, period start/end, provider ids).
Enforce limits at the action that consumes them: pairing a screen beyond `max_screens`
is rejected with a clear upgrade message; upload beyond storage quota is rejected.
Dashboard billing page shows current plan, usage vs. limits, and an upgrade path.
Integrate one payment provider (Stripe or Razorpay — Razorpay if billing in INR) behind
a thin adapter so the provider can be swapped. Handle webhooks for
activation/renewal/failure, and define what happens on lapse (grace period → read-only
dashboard, **screens keep playing** — never black out a paying customer's storefront
over a billing hiccup).

**Acceptance tests:**
- **T6.1** Pairing screen number `max_screens + 1` is refused with an upgrade prompt.
- **T6.2** Upload exceeding storage quota is refused; usage figures are accurate.
- **T6.3** Webhook-driven upgrade raises limits immediately.
- **T6.4** Simulated payment failure → grace period behaviour is exactly as specified,
  and **playback on already-paired screens continues**.
- **T6.5** No payment credentials in source; provider keys come from env only.

---

## 10. Phase P7 — Remote player updates (R9)

Version metadata and detection already exist. Complete the loop: host APKs, expose the
current version per org/channel, and let the player download and stage an update.

**Reality check, not an excuse:** true unattended install requires the app to be
**device owner** (provisioned via ADB/QR at setup, then `PackageInstaller` silent
install) or an OEM/MDM signage SDK. On a stock non-provisioned TV, Android will always
show a user prompt. Therefore: implement silent install for device-owner deployments,
and a clear on-screen prompt as the fallback. Document the provisioning procedure —
this is a deployment decision, not a coding shortcut.

**Acceptance tests:**
- **T7.1** Device-owner TV updates end-to-end with zero interaction and resumes playback.
- **T7.2** Non-provisioned TV shows the prompt and updates correctly on confirm.
- **T7.3** A failed/corrupt update rolls back and the old version keeps playing.
- **T7.4** Staged rollout: only screens on the targeted channel receive the update.

---

## 11. Regression suite — run after every phase

```bash
# Backend
backend\venv\Scripts\python.exe tests\validation.py
backend\venv\Scripts\python.exe -m pytest tests -q

# Frontend
cd frontend && npx tsc --noEmit && npm run lint && npm run build

# Android
cd android-tv && ./gradlew test && ./gradlew assembleDebug
```

Plus the manual TV checks: **reboot → plays**, **airplane mode → plays**,
**new ad → appears**, for every phase from P1 onward. Automated tests cannot prove R6/R7;
a physical (or emulated) reboot is the only real evidence.

---

## 12. Definition of done

The goal is met when, on a physical Android TV:

1. Power-cut the TV. On restore it boots straight into the player and resumes ads with
   no remote, no input, no network.
2. With the internet unplugged all day, cached ads keep looping correctly on schedule.
3. Reconnect, and new ads uploaded from the dashboard appear within ~a minute.
4. Transitions between every ad are smooth, with no black frames, exactly as configured
   per item in the admin page.
5. The dashboard shows each TV's true state: online/offline/playing/error + current item.
6. Two separate companies use the platform with zero visibility into each other's data.
7. Plan limits are enforced and a subscription can be paid for and upgraded.
8. A new player version can be pushed to all screens without visiting any site.

Until all eight hold on real hardware, the goal is not met — keep iterating.

---

## 13. Rules for the implementing agent

- **Fix the root cause, not the symptom.** Before editing a shared function, check every
  caller; one guard in the shared path beats a guard in each caller.
- **Never break offline playback.** It is the product's core promise. Any change to the
  player must be re-verified against T0.1 and T0.2.
- **Migrations must preserve data.** Back up the DB before each migration (see
  `backups/`), and verify row counts after.
- **No secrets in source.** Keys via env only; `.env` stays gitignored.
- **Don't add a dependency for what a few lines can do**, and reuse what's already
  installed (`@dnd-kit`, shadcn/ui, React Query, Media3, Room, WorkManager all present).
- **Every non-trivial change ships one runnable check** — the smallest test that fails if
  the logic breaks.
- Mark deliberate shortcuts with a `ponytail:` comment naming the ceiling and the
  upgrade path.

---

Reference: [AbleSign](https://www.ablesign.tv/) · [features](https://www.getapp.com/marketing-software/a/ablesign/) · [scheduling docs](https://www.ablesign.tv/knowledge-base/category/scheduling/)
