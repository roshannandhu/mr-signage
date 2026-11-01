# MR Signage — Admin Control Center: Work Order for Antigravity

**Repo:** `E:\IMP projects\OLRAC SIGNAGE`
**Branch:** `phase-2-fixes`
**Goal:** finish the Super Admin console redesign ("Control Center") that Claude Sonnet 5
started this session. Phase A and the first slice of Phase B are done, committed, and
verified. Everything else below is **unbuilt** — this file is the complete spec for it.

**Start a brand-new chat with Antigravity and give it this file first, before anything
else.** It has zero memory of this session. Tell it: "Read ADMIN_CONSOLE_GOAL.md in this
repo fully before writing any code. It is the complete spec — do not skip sections."

---

## 0. Ground rules (same as the rest of this repo — see `ANTIGRAVITY_GOAL.md` section 0)

1. **Do not redesign the stack.** FastAPI + SQLAlchemy + Alembic + Next.js 16 + React
   Query + Tailwind v4 + Base UI (`@base-ui/react`, shadcn-style wrappers in
   `frontend/src/components/ui/`) are fixed.
2. **Do not rebuild what already works.** Section 2 below lists what Phase A built —
   reuse it, don't duplicate it.
3. **Reuse before writing.** This codebase has a strong "ponytail" ethos: check for an
   existing helper/util/pattern before adding new code. Section 3 lists the load-bearing
   patterns Phase A established that every later phase should reuse.
4. **Every backend change ships with a test**, registered in `tests/conftest.py`'s
   `ISOLATED_SCRIPTS` set (and in the `NEEDS_POSTGRES` subtraction set if it's fine on
   SQLite — most admin tests are). Read `tests/conftest.py`'s docstrings first. Pattern:
   a standalone script with `run()` + `check()` + a final `print`, not pytest functions —
   copy `tests/test_admin_audit_log.py` as the template.
5. **Mutation-test every new test**: `git stash push -q -- <files>`, re-run the test and
   confirm it now **fails for the right reason**, `git stash pop -q`. Several tests this
   session caught real bugs doing this (see section 2).
6. **Migrations are additive only** — new nullable columns or new tables, each with a
   working `downgrade()`. No drops, no renames. Follow the exact style of
   `backend/alembic/versions/c3e8a1f4d7b9_admin_audit_events.py` (just-shipped, good
   example: explanatory docstring, `sa.Column(..., index=True)` inline rather than
   separate `create_index` calls where possible, chains off the real current head).
7. **Check the real current Alembic head before writing a migration**:
   `python -m alembic -c backend/alembic.ini heads` (don't trust this doc's number by the
   time you read it — Phase A's head was `c3e8a1f4d7b9`, but check).
8. **No secrets in source.** Env only.
9. **Frontend must pass** `npx tsc --noEmit`, `npx eslint src`, `npx depcruise src`,
   `npm run build` — all from `frontend/`. Read `frontend/AGENTS.md`: this is Next.js 16,
   training data is stale for it, check `node_modules/next/dist/docs/` before using an
   API you're unsure about.
10. **No production deploy, ever, without the user's separate explicit approval.** Local
    verification only (local Postgres on `localhost:5432`, Redis on `localhost:6379`,
    backend preview on `:8010`, frontend preview on `:3000`). Never touch the production
    database or push to a remote that auto-deploys.
11. **Verify by running things**, not by "it compiles." Run the actual test, hit the
    actual endpoint, look at the actual rendered page. This session's commit message
    (`git log -1`) documents exactly what was verified for Phase A — match that bar.
12. **`-1` = unlimited**, consistently, on the wire and in the UI, for every limit (TVs,
    ad slots, clients, storage). Existing helpers: `_screen_limit`, `_zero_is_unlimited`,
    `_store_limits` in `backend/routers/admin.py`.
13. **"Hide, don't lock"**: a tenant whose plan lacks a feature sees no UI for it at all,
    both frontend and backend (`require_feature` / `ensure_feature` in `backend/tenancy.py`).

---

## 1. Context: why this exists

The user asked for a from-scratch redesign of the Super Admin console into something on
the level of AWS Console / Stripe / Cloudflare / Vercel / Linear, built specifically for
MR Signage (not copied). Three parallel code surveys (backend routes, frontend pages,
data model + security + tests) found the console was a first-version tool: no audit
trail, an O(tenants) query loop on the main list, no pagination anywhere, every
destructive action used `window.confirm`/`prompt`, and six real security/safety bugs.

A phased plan was written, reviewed, and **approved by the user**. The full original
plan text (every page's "what exists / what to change / why / how / reuse / risk", plus
every phase's contents) is preserved at:
`C:\Users\Roshan Raj\.claude\plans\hashed-hugging-yao.md`

**Read that file too — it is the authoritative page-by-page spec.** This document gives
you the current status and the engineering patterns to reuse; the plan file gives you the
product spec for each page. Where they conflict (e.g. if real code has diverged from a
plan assumption), trust the code and use judgement, the way Phase A did in several places.

**User's explicit design decision** (overrides anything the plan doc's prose suggests
otherwise): admin console backgrounds are **pure black `#000` in dark mode and pure white
`#fff` in light mode**, clean and minimal. Small status colours (green/amber/red/blue for
online/warning/critical/info) are fine — the rule is about the page background and chrome,
not a ban on all colour. This is already implemented in `frontend/src/app/globals.css`'s
`.admin-console` / `.dark .admin-console` blocks — match that palette exactly for any new
admin UI, don't invent new colours.

---

## 2. What Phase A (+ Phase B slice 1) already built — DO NOT REBUILD

Committed as `a5353fd` on `phase-2-fixes`. Read `git show a5353fd --stat` and the full
commit message (`git log -1`) for the complete, precise list. Summary:

### Design system (reuse for every new page)
- `.admin-console` / `.dark .admin-console` tokens in `frontend/src/app/globals.css` —
  pure black/white, status colour only.
- Console shell: `frontend/src/app/admin/layout.tsx` — collapsible left rail (persisted
  to localStorage), breadcrumbs (derived from the nav list, don't hand-write them on new
  pages — add new pages to `navSections` and breadcrumbs follow automatically),
  environment badge (`EnvironmentBadge` component, calls `adminApi.platformHealth()`),
  theme toggle, account menu (`DropdownMenu` from `components/ui/dropdown-menu`).
- `frontend/src/components/admin/kit/confirm-dialog.tsx` — `ConfirmProvider` +
  `useConfirm()`. **Use this for every destructive/impactful action**, not
  `window.confirm`. Three levels: 1 = plain confirm, 2 = confirm + a reason (stored in
  the audit log via the `X-Admin-Reason` header, see below), 3 = confirm + type the
  resource's name back. `ConfirmProvider` is already mounted in `admin/layout.tsx`;
  just call `useConfirm()` in any admin page/component.

### Backend foundations (reuse these, don't re-invent)
- **`backend/services/admin_metrics.py`**: `tenant_usage(db, org_ids)` returns bulk
  per-tenant counts (screens, online screens, ad slots used, clients, storage) in a
  **fixed small number of queries** regardless of tenant count, via `GROUP BY`
  aggregates. `screen_is_online(last_seen, now=None)` and `online_cutoff()` are **the**
  definition of "online" platform-wide — use them, don't write a new threshold.
  `fleet_screen_filter()` returns the standard "real fleet screen" filter tuple
  (not archived, has an org, not `waiting_pairing`).
- **`backend/services/audit.py`**: `audit.record(db, actor=, action=, request=,
  target_org_id=, target_type=, target_id=, before=, after=, reason=,
  acting_org_id=, batch_id=)` writes one `AdminAuditEvent` row (model in
  `backend/models.py`). **Call this from every new admin mutation route.** It redacts
  known secret keys automatically (see `_SECRET_KEYS` in that file — add to it if a new
  route introduces a new secret-shaped field). `reason_from(request)` reads and decodes
  the `X-Admin-Reason` header the frontend's `ConfirmDialog` sends.
  `backend/routers/admin.py` has a local `_audit(db, scope, request, action, *, org=,
  org_id=, target_type=, target_id=, before=, after=)` wrapper that's slightly more
  convenient inside that file — use it there; use `audit.record` directly elsewhere
  (see how `backend/routers/releases.py` and `backend/routers/billing.py` do it).
- **Act-as write auditing is automatic**: `backend/main.py`'s `ActAsAuditMiddleware`
  plus `backend/tenancy.py`'s `note_act_as_write()` record an `act_as.write` row for any
  successful (status < 400) non-GET request made with `X-Act-As-Org` set, **except**
  requests to `/api/admin/*` (those write their own, richer rows via the two functions
  above — don't double-audit).
- **Read-only workspace view**: `X-Act-As-Mode: read` header →
  `TenantScope.acting_read_only` → `TenantScope.require_write_access()` raises 403. Wired
  into `require_tenant_roles(..., writable=True)` automatically. If you add a new
  tenant-facing write route that does **not** go through `require_tenant_roles` (i.e. you
  call `get_tenant_scope`/`get_billing_scope` directly, the way 5 routes in `billing.py`
  do), you **must** call `scope.require_write_access()` yourself as the first line, or
  `tests/test_act_as_read_only.py`'s static sweep will fail the build. Read that test
  file — it's a genuinely good spec of "what every new write route must do."
- **Pagination contract**: `GET /api/admin/tenants` takes optional `q`, `plan_id`, `sort`,
  `limit`, `offset`; returns the same plain list as before (backward compatible) plus
  `X-Total-Count` in the response header when `limit` is passed. CORS
  `expose_headers=["X-Total-Count"]` already set in `backend/main.py`. Copy this exact
  pattern for any other list endpoint you paginate (fleet, payments, audit log, etc.).
- **`GET /api/admin/platform-health`**: full health report (DB host, storage env,
  legacy-auth count, migration error, etc.), admin-only. The public `GET /api/health` is
  now a trimmed 4-field subset. If Phase E's Platform Health page needs more detail
  (job heartbeats, etc.), **extend** `_health_details()` in `backend/main.py` rather than
  adding a parallel endpoint.
- `AdminAuditEvent` model (`backend/models.py`): no foreign keys (append-only, must
  outlive the row it describes), `actor_username` snapshotted, `before_json`/`after_json`
  text columns (JSON, redacted). Query it directly for the Phase E Audit Log page.

### Tests (all registered in `tests/conftest.py`, all passing — 291/291 last full run)
- `tests/test_admin_audit_log.py` — the audit log template to copy.
- `tests/test_admin_query_budget.py` — query-counting pattern (`event.listen(engine,
  "before_cursor_execute", ...)`) — copy this for any new bulk endpoint (Mission
  Control's overview query especially — it must stay O(1), not O(tenants)).
- `tests/test_admin_guards.py` — security-fix regression tests.
- `tests/test_act_as_read_only.py` — the static-sweep + live-check pattern for "every
  write route must respect read-only act-as." **Re-run this after adding ANY new write
  route anywhere in `backend/routers/` — not just admin routes.** It will tell you
  immediately if you forgot the gate.
- `frontend/e2e/admin-smoke.spec.ts` (Playwright) — 4 tests: every admin page loads
  clean, black/white theme in both modes, Level-3 confirm guard works, mobile nav drawer
  works. **Add a test here for every new admin page you build.**
  `frontend/e2e/seed.ts` + `scripts/e2e_seed.py` seed/clean throwaway accounts — reuse
  this, don't write new seed scripts. `scripts/e2e_seed.py` is gated by
  `backend/script_guard.py`'s `confirm_destructive` — it will **refuse** to run against
  anything but a local database, by design. Don't bypass that.
  Run locally with: `cd frontend && npx playwright test` (after `npx playwright install
  chromium` — if that hangs on Windows/this machine specifically, see the note at the
  bottom of `frontend/playwright.config.ts` about `PW_LOCAL_CHROME_PATH`; this is a
  machine-local workaround, never commit a real path for it).

### Six real bugs fixed this session (context only — already shipped, nothing to redo)
Self-demotion from super_admin, confirmed-promotion-to-super_admin, `releases.py`'s wrong
auth dependency (was applying the *operator's own* workspace billing state to a
platform-wide action), plan-delete 500 on a lingering `Subscription` FK, a
suspended/rejected/removed tenant's dashboard websocket staying open, the demo-video
upload going to ephemeral disk with no size cap, and the public `/api/health` leaking the
DB host/env var names.

---

## 3. What's left — full remaining scope, phase by phase

Everything below is **unbuilt**. For the full product spec of each page (exact columns,
copy, tabs), read the corresponding section of
`C:\Users\Roshan Raj\.claude\plans\hashed-hugging-yao.md` — section numbers below match
that file's section numbers. This document adds the engineering notes the plan doc
doesn't have (which files, which patterns to reuse, what to watch for).

### Phase B remainder (plan doc §4.1, §4.2, §4.3, §4.4, §4.12, §4.13 — B6)

**B6. `GET /api/admin/overview?range=7d|30d|90d`** (backend, `backend/routers/admin.py`
or split into a new `backend/routers/admin_overview.py` if `admin.py` is getting
unwieldy — it's already 2000+ lines). Must be O(1)-ish: a fixed small number of
aggregate queries regardless of data size, cached in Redis for 30s (see how
`backend/services/storage_service.py`'s `_CACHE_SECONDS` pattern caches the bucket
listing — copy that shape). Returns: tenants by status + signups over time, screens
online/offline/offline>24h (use `admin_metrics.screen_is_online`), % on latest released
version (reuse the `/admin/fleet` logic in `admin.py` — don't duplicate, consider
extracting a shared helper), open alerts by severity (`models.Alert`), plays over time
online/offline (from `models.PlayLogHourlyRollup` — already has an `offline_plays`
column), money collected over time (`models.PlatformPayment`, now indexed on `paid_at`),
renewals due in 14 days (`models.Subscription.current_period_end`), storage total
(reuse `admin_metrics`/`storage_service`), pending approvals + custom requests count,
last 10 `AdminAuditEvent` rows. **Write `test_admin_overview.py` with the query-budget
pattern from `test_admin_query_budget.py` before you consider this done** — this is the
single highest-risk-of-regression endpoint in the whole plan if done naively.

**`GET /api/admin/inbox-counts`** — small, same file. Powers the notifications bell
(§4.13). Pending approvals + new custom requests + critical alerts + failed payments in
7 days + attention-queue items (attention queue itself is Phase F — until it exists,
just the first four).

**Frontend Mission Control** (§4.1): replace `frontend/src/app/admin/page.tsx` (currently
107 lines, builds everything from the slow tenant list). Use `recharts` (already a
dependency, see `frontend/src/app/dashboard/content/[id]/page.tsx` for an existing
`AreaChart` usage to copy the pattern from) — grey ramp only (`--chart-1..5` tokens,
already redefined for `.admin-console` in `globals.css`), colour only where a series
genuinely is a status (offline, failed).

**Tenant Directory redesign** (§4.2): `frontend/src/app/admin/tenants/page.tsx` (currently
~500 lines, already has `ConfirmDialog` wired in from Phase A for its row actions — don't
redo that part). What's still missing: a real `AdminDataTable` component (doesn't exist
yet — build it in `frontend/src/components/admin/kit/`, on top of `components/ui/table`
+ the existing `frontend/src/components/dashboard/list-toolbar.tsx`'s `ListToolbar` +
`sortItems`/`commonSorts` + `frontend/src/hooks/use-bulk-selection.ts`'s
`useBulkSelection` + `BulkActionBar`/`SelectAllCheckbox` + `frontend/src/lib/bulk.ts`'s
`runBulk`/`bulkSummary` — **all of these already exist and are used elsewhere in the
dashboard, this is a reuse job, not a from-scratch build**), server-side pagination
wired to the `limit`/`offset`/`sort`/`q` params Phase A already added to the backend, a
health-score column (new, simple weighted formula — spec in plan doc §4.2), a row
"⋯" action menu (use `components/ui/dropdown-menu`) instead of the current button wall.

**Tenant 360** (§4.3): `frontend/src/app/admin/tenants/[id]/page.tsx` needs the
Billing/Plan&Limits/Branding/Security/Activity/Notes tabs added (currently has
Screens/Content/Team only, plus the Phase-A-added read-only "View" entry point — keep
that). Activity tab = a filtered view of `AdminAuditEvent` for that org — build the
backend list route first (`GET /api/admin/tenants/{id}/activity` or fold into a general
`GET /api/admin/audit?org_id=`, see Phase E's Audit Log page — **build the audit list
endpoint once, shared by both**).

**Review Center** (§4.4): `frontend/src/app/admin/approvals/page.tsx` (currently 148
lines, reject flow already uses `ConfirmDialog` from Phase A). Needs: custom requests
folded in (move `frontend/src/components/admin/custom-requests-panel.tsx` here from
Billing), a details drawer, duplicate-tenant hints, a History tab (reuse the audit list
endpoint above), bulk approve.

**Global search / command palette** (§4.12): new `GET /api/admin/search?q=` — **one**
`UNION ALL` query (tenants/users/screens/payments/releases, top 5 each). Frontend: Base
UI's `Dialog` + `Autocomplete` (both already installed via `@base-ui/react` — do **not**
add `cmdk`, the plan doc explicitly rules it out since Base UI already covers it).

### Phase C (plan doc §4.5, §4.6) — Financial Command Center, Feature Control Studio
- `backend/routers/admin.py` already has `/payments` (unpaginated — paginate it the same
  way as `/tenants`), `/plans` CRUD (Phase A fixed the delete-FK bug), `/custom-requests`
  CRUD. New: a revenue-by-day/week aggregate endpoint (reuse the `PlatformPayment.paid_at`
  index Phase A added), a `POST /api/admin/plans/{id}/impact` endpoint (counts tenants
  that would exceed a proposed new limit or lose a feature — pure read, no writes), a
  `GET /api/admin/features` endpoint (derive from `backend.billing.plan_features`'s known
  keys — replaces 3 hardcoded copies in the frontend: `FEATURES` in
  `components/admin/admin-ui.tsx`, and two more the original survey found — grep for
  `FEATURES` across `frontend/src/app` to find them all before changing anything).
- Frontend: Billing page tiles/charts, Packages page becomes cards + an editor drawer
  (currently a spreadsheet-of-inputs table — full rewrite per plan doc §4.5), a
  "preview as tenant" component shared with `/signup` and the tenant billing page (pull
  the existing plan-card markup out of one of those into a shared component rather than
  writing a new one).

### Phase D (plan doc §4.7, §4.8, §4.10) — TV Control Center, Deployment Studio, Setup Studio
- Fleet page (`frontend/src/app/admin/fleet/page.tsx`) needs pagination + filters + a
  screen drawer + a "lock strength" indicator (device-owner vs standard-install — see
  this project's own memory file on the Android Home-button hardware limitation if
  Antigravity has access to it, otherwise: a non-device-owner TV cannot reliably block
  Home, only Back, confirmed on real hardware earlier in this project).
- New `POST /api/admin/screens/{id}/commands` — allowlist of
  `{check_update, bring_to_front, request_screenshot, remote_unlock}`, reuse
  `backend.services.queue_device_command` (already exists, used by the kiosk-lock
  feature), audit every call, cap bulk sends at 100 screens with a Level-2 confirm.
- Deployment Studio: `frontend/src/app/admin/releases/page.tsx` (Phase A already fixed
  its auth bug and gave it the black/white theme) needs the rollout-timeline UI, a
  publish wizard that defaults to canary, and "Pause rollout" replacing the misleading
  "demote" language (Android never downgrades — a fix ships as a higher version code,
  say so in the UI).
- Setup Studio: `frontend/src/app/admin/provisioning/page.tsx` — the QR generator
  currently binds to **the operator's own org** (bug, not yet fixed) instead of a chosen
  tenant. Fix: a new admin route that creates the enrollment token for a specified org id
  (reuse `backend/routers/enrollment_tokens.py`'s logic, called with an explicit org
  rather than the caller's own).

### Phase E (plan doc §4.14, §4.15, §4.16, §4.18 — B5, B8)
- **B5, sign-in history + sessions**: new `auth_events` table (migration — additive,
  columns: user_id nullable, attempted_username, success, method
  password/google, ip, user_agent, occurred_at indexed), `users.last_login_at`,
  `users.token_version` (default 0, server_default "0"). Hook into
  `backend/routers/auth.py`'s `login_for_access_token` and `sign_in_with_google`. Add a
  `tv` (token-version) claim to minted tokens; `get_current_user`/`get_current_user_ws`
  compare it, treating a missing claim as version 0 (so existing live tokens aren't
  invalidated on deploy — this matters, don't skip it). "Sign out everywhere" = bump
  `token_version`. Optional `SystemSetting` "super admins must use Google" — check it in
  `login_for_access_token` before accepting a password for a `super_admin` user.
- **B8, job heartbeats**: each arq cron in `backend/worker.py` writes
  `cron_last_run:<name>` (timestamp + duration) to Redis on completion — a few lines per
  job, not a redesign.
- Audit Log page: list + filter UI over `AdminAuditEvent`, reusing the list endpoint
  built for Tenant 360's Activity tab (see Phase B above — build it once).
- Security Center: admin list + last login (from B5), sign-in activity, act-as activity
  (from `AdminAuditEvent` where `action = 'act_as.write'`), kiosk unlock activity
  (`models.KioskUnlockEvent`, already exists from the earlier kiosk-lock feature), the
  maintenance PIN card (move from Fleet page, mask by default, audit every reveal).
- Platform Health page: extend `_health_details()` (section 2 above) with the B8 job
  heartbeats and expose it on a dedicated page — the data's mostly already there from
  Phase A, this is mainly a frontend page plus the B8 heartbeat data.
- System Settings: move contact-email + SMTP test (already built, currently on the
  Billing page) here, add audit-log retention and attention-queue-threshold settings
  (`SystemSetting` key/value pattern, already used elsewhere — copy it).

### Phase F (plan doc §4.9, §4.11, §4.17 — B9)
- Support Center: attention queue (rules engine, each rule a pure function over existing
  data — TVs offline >24h, plan expiring, failed updates, storage >90%, repeated wrong
  PINs via `KioskUnlockEvent`, stale custom requests/signups) + a new `tenant_notes` table
  (migration) + a new `attention_dismissals` table (migration). Never tenant-visible.
- Media Intelligence: Storage page gets sorting, a top-consumers chart, the
  `media_blob` database-mirror size (`pg_total_relation_size('media_blob')` — raw SQL,
  table has no ORM model, see how `backend/main.py` already queries it defensively with
  a try/except for "table might not exist").
- Media Studio: Demo Video page polish (progress bar, metadata, version history).
- B9, live admin channel: a `platform:admin` Redis pub/sub channel, published on
  alert/signup/payment/custom-request events, super-admin-only at socket connect time.
  Reuse the broadcast helper and backoff logic already in
  `frontend/src/lib/use-fleet-alerts.ts` rather than writing new reconnect logic.

---

## 4. How to verify as you go (match Phase A's bar, don't skip steps to save time)

1. `DATABASE_URL="postgresql://olrac:olrac_password@localhost:5432/olrac_signage" python
   -m alembic -c backend/alembic.ini upgrade head` after any new migration.
2. New/changed backend test: run it standalone, then mutation-test it (stash the
   production code, confirm the test fails, unstash).
3. Before calling a backend sub-feature done: `DATABASE_URL="postgresql://olrac:
   olrac_password@localhost:5432/olrac_signage" python -m pytest tests/ -q
   --ignore=tests/run_isolated.py --deselect
   tests/test_p6_websockets.py::test_p6_websockets` (takes ~10 min — this file's own
   session saw it pass clean at 291/291 right before committing Phase A; one
   pre-existing flaky test, `test_p6_websockets.py::test_p6_websockets`, is excluded by
   convention — it only fails in the full-suite run, never standalone, per this
   project's own memory of that flake).
4. Frontend: `npx tsc --noEmit && npx eslint src && npx depcruise src && npm run build`
   from `frontend/`, then `npx playwright test` (see the Chromium-install note in
   section 2 if the install itself hangs — that's a known Windows-local quirk, not a
   code problem).
5. Before calling ANY phase done: a live browser check with a throwaway super-admin
   account (create it directly in the local Postgres DB via the `backend.routers.auth`
   password-hashing helpers — see how this session's own throwaway-account scripts did
   it, e.g. the pattern in `scripts/e2e_seed.py`), screenshot it in both light and dark
   mode, then **delete the throwaway account**.
6. Commit each sub-phase as its own commit (don't batch everything into one giant commit
   at the end — if something breaks, a smaller commit is easier to bisect). Never push.
   Never deploy. Never touch the production database.

---

## 5. Known unknowns / judgement calls for Antigravity

- The plan doc's §4.5–§4.11 descriptions are product specs, not file-level instructions —
  expect to make small file-organization calls (e.g. whether `admin.py` needs splitting
  up once Mission Control's endpoint is added — it's already large).
- `frontend/src/app/admin/releases/page.tsx` and `provisioning/page.tsx` still have some
  rough edges Phase A didn't fully polish (release calls live on `api` instead of
  `adminApi` — a cleanup noted in the original 3-survey findings but not yet done; low
  risk, low priority, fix opportunistically).
- If something in this doc turns out to be stale by the time you read it (a file moved,
  a line number shifted), **trust the actual code over this document** — grep for the
  symbol names mentioned here rather than trusting exact line numbers.
