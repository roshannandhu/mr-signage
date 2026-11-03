"""Run each backend test script in its own process.

`backend/database.py` builds the SQLAlchemy engine at import time from
DATABASE_URL. Each test script points that variable at its own temporary
database, but a single pytest process imports every module together, so the
first import wins and the rest end up querying a database that has already been
torn down — every script passes alone and the suite fails as a whole.

Collecting these files as subprocesses fixes it at the root: one process per
script, one engine per database, and no import-order coupling. It also keeps
each file directly runnable (`python tests/test_quotas.py`).
"""

import os
import pathlib
import socket
import subprocess
import sys

import pytest

# Point the in-process engine somewhere harmless BEFORE any test module is imported.
#
# backend/database.py calls load_dotenv() and builds its engine at import, so whichever
# module imports backend first decides the database for the whole pytest process -- and
# the modules in python_files are imported in alphabetical order, most of which never set
# DATABASE_URL. With a developer's backend/.env present, the first importer therefore bound
# the engine to the REAL database: verified, it resolved to the production Supabase host,
# and test_media_worker's uploads landed there. That is how its rendition count grew run
# over run (2, then 4) and failed only in a full-suite run, never alone.
#
# Set here rather than in each module because it has to happen before the first import, and
# because a rule that every new test file must remember is one that will eventually be
# forgotten -- exactly as it was. Scripts in ISOLATED_SCRIPTS run as subprocesses and set
# their own DATABASE_URL, which still wins.
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "pytest-in-process-secret")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "mock")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "mock")

ISOLATED_SCRIPTS = {
    "test_feature_parity.py",
    "test_tenant_isolation.py",
    "test_quotas.py",
    "test_datetime_aware.py",
    "test_sqlite_utc.py",
    "test_proof_of_play.py",
    "test_play_log_attribution.py",
    "test_account_profile.py",
    "test_screen_approval.py",
    "test_platform_admin.py",
    "test_reinstall_reconnect.py",
    "test_role_separation.py",
    "test_signup_lifecycle.py",
    "test_analytics.py",
    "test_p6_websockets.py",
    # A socket with no credential is receive-only and sees only its own commands.
    "test_ws_unauthenticated.py",
    # Starting or cancelling an emergency broadcast reaches screens that had already settled.
    "test_emergency_sync.py",
    # -1 means unlimited on every plan limit, and no unlimited tenant is refused.
    "test_plan_limits.py",
    # Buying a plan: renewal keeps days, a switch starts today, payment never lifts a suspension.
    "test_plan_renewal_rules.py",
    # The media supervisor takes only stuck videos, and takes each one once.
    "test_media_supervisor_claim.py",
    "test_alert_resolution.py",
    # The first signup and first new package take the ids after the seeded rows.
    "test_sequence_migration.py",
    # /api/health stays up on a database with no media_blob table (Postgres aborts the transaction).
    "test_health_without_media_mirror.py",
    # An item's own transition override needs the transitions flag, same as the playlist default.
    "test_playlist_item_transition_gate.py",
    # New plan flags seed a fresh plan; they never backfill onto one that already exists.
    "test_billing_catalog_seed.py",
    # Every route Phase 3.1 sold behind a plan flag actually refuses a plan without it.
    "test_plan_feature_gates.py",
    # A purchase records one platform_payments row; admin Billing settings work.
    "test_admin_billing.py",
    # The /signup storefront needs no login and hides inactive/hidden plans.
    "test_public_plans.py",
    # A custom request's company/contact fields validate, round-trip, and stay optional.
    "test_custom_request_contact.py",
    # An emailed invoice signs off as the tenant's brand, not the raw organisation name.
    "test_invoice_brand_signoff.py",
    # Online/offline play counts reach campaign, media, booking-report and admin surfaces.
    "test_offline_play_reporting.py",
    # The tenant billing summary reports the limit actually enforced -- override, not plan.
    "test_billing_summary_limits.py",
    # Two distinct commands queue for an offline screen; each is held for its own hold time.
    "test_pending_commands_queue.py",
    # An opened alert emails owners/editors once, only on a plan with remote_control.
    "test_alert_email_notification.py",
    # The dashboard socket follows the acted-as workspace, not the operator's own org.
    "test_dashboard_ws_act_as.py",
    # The TV's sync response carries the tenant's own brand, never the raw org name.
    "test_sync_branding.py",
    # Workspace lock PIN resolution, per-screen opt-out, and the kiosk_lock feature gate.
    "test_kiosk_lock_pin.py",
    # A plan with kiosk_lock stays unlocked until the tenant enables it; audit log; scoped remote unlock.
    "test_kiosk_lock_enabled.py",
    # Every admin change writes one audit row; act-as writes are recorded, reads are not.
    "test_admin_audit_log.py",
    # The tenants list costs the same queries for 2 tenants as for 22, with quota-true counts.
    "test_admin_query_budget.py",
    # Self-demotion, confirmed promotion, release auth, plan FK, suspended socket, reel upload.
    "test_admin_guards.py",
    # "View workspace (read-only)" refuses every write: a static sweep of every write
    # route plus live checks across clients, playlists, branding and billing.
    "test_act_as_read_only.py",
    # Mission Control: numbers correct, cache works, query count flat regardless of tenant count.
    "test_admin_overview.py",
    # Every type matched, literal % escaped, short queries return nothing, non-admin refused.
    "test_admin_search.py",
    # The shared audit list behind Tenant 360 Activity and the Audit Log page: filters, paging.
    "test_admin_audit_list.py",
    # Cron heartbeat wrapper: job result/exception pass through, heartbeat written, Redis failure non-fatal.
    "test_cron_heartbeats.py",
    # Admin zero-touch QR binds to the CHOSEN tenant, not the operator's own org.
    "test_admin_provisioning_qr.py",
    # Admin payments list pagination (offset/limit + X-Total-Count) and kind/provider filters.
    "test_admin_payments_paging.py",
    "test_plan_impact.py",
    "test_admin_screen_commands.py",
    "test_phase_e_trust.py",
    # How many TVs hold no device secret: the number that says when legacy login can close.
    "test_legacy_auth_count.py",
    # Only a screen's own token acts as that screen; Google-flow tokens do not.
    "test_device_token_type.py",
    # A login token belongs to one user row, so a reused username cannot inherit it.
    "test_token_binding.py",
    # Screenshots must be images; email-status needs a sign-in and does not crash.
    "test_upload_hardening.py",
    # A device id alone must not take over a claimed screen via /register.
    "test_register_takeover.py",
    # Signing a TV in cannot delete or take over another workspace's screen; /pair is limited.
    "test_cross_tenant_binding.py",
    "test_media_selection.py",
    # These six were written, committed, and then never run: they were absent from this
    # set and from python_files in pytest.ini, so pytest collected neither. The suite
    # reported success while six files' worth of assertions sat untouched. The guard in
    # `pytest_collection_finish` below now makes that state impossible to reach again.
    "test_ad_placements.py",
    "test_booking_report.py",
    "test_tenant_plans.py",
    "test_ad_payments.py",
    "test_media_report.py",
    "test_reinstall_dedup.py",
    "test_storage_cleanup.py",
    "test_sync_invalidation.py",
    "test_release_rollout.py",
    "test_assign_shows_on_timeline.py",
    "test_replace_orphaned_targets.py",
    # A platform operator stepping into one workspace, and only a platform operator.
    "test_act_as_tenant.py",
    # A booking sold by per-location days is as long as its longest location.
    "test_booking_window_follows_days.py",
    # Storefront: pay-to-access, the clients cap, the feature gate and the custom flow.
    # Falls back to SQLite with no server, like the two below.
    "test_plan_purchase.py",
    # Google sign-in: authorisation, a case-insensitive lookup and a tenant guard.
    # Owns a database, so it belongs here rather than in pytest.ini.
    "test_google_signin.py",
    # Proves a websocket does not pin a pooled database connection.
    "test_ws_connection_pool.py",
    # Exercises the S3 branch that test_storage_cleanup disables.
    "test_r2_cleanup.py",
    # Rollups on a non-Postgres dialect. Owns a database, so it belongs here.
    "test_play_log_aggregation.py",
    "test_storage_budget.py",
    "test_bring_to_front.py",
    "test_ad_counting_detail.py",
    "test_counting_integrity.py",
    "test_all_five_areas_e2e.py",
    # Removing a TV: play history survives, the panel is signed out, quota is freed.
    "test_screen_removal.py",
    # The screen cap: package limits, admin overrides, and the /enroll bypass.
    "test_screen_quota.py",
    # One booking, different run lengths per location.
    "test_per_location_ad_window.py",
    # The dashboard and the player must resolve the same playlist for a screen.
    "test_playlist_resolution_parity.py",
    # The client-ad editor obeys the same booking rules as the placements routes.
    "test_client_ad_editor.py",
    # Re-pointing an advert at another client must not overwrite that client's contact details.
    "test_client_ad_contact_safety.py",
    # Live and paid bookings survive deletion; reviving a finished one respects the slot limit.
    "test_booking_deletion_guards.py",
    # A paid window that closes, and closes all the way to the television.
    "test_subscription_expiry.py",
    # The operator setting a paid window, and granting limits of their own.
    "test_admin_lifecycle_controls.py",
    # One definition of a workspace's storage, and bucket objects attributed to it.
    "test_storage_accounting.py",
    # One row per workspace however many key prefixes it has accumulated.
    "test_storage_attribution.py",
    # A delete reaches the bucket AND the Postgres mirror, and logs whatever it missed.
    "test_media_deletion.py",
    # A removed workspace survives 30 days, then nothing of it does.
    "test_tenant_purge.py",
    # A reinstalled panel comes back as itself; only removal wipes it.
    "test_screen_reconnect.py",
    # A factory-reset TV reconnects by model instead of duplicating.
    "test_factory_reset_reconnect.py",
    # Publishing a player build reaches screens whose content never changes.
    "test_ota_reaches_a_settled_fleet.py",
    # Every kiosked screen has a way in; only the operator sets the fallback PIN.
    "test_maintenance_pin.py",
    # A booked advert reaches the screen, inside a window that is open now.
    "test_assigned_ad_plays.py",
    # An operator can make one TV look for its update now.
    "test_screen_update_now.py",
    # A screen reports its own state for support; only the screen itself can.
    "test_device_diagnostics.py",
    # A screen's timeline follows its booking: add, remove, never twice, never past the plan.
    "test_screen_timeline_follows_booking.py",
    # Tenant purge: removing a workspace and hard-deleting 30 days later.
    "test_tenant_purge.py",
    # Every screen gets a dedicated playlist upon pairing/enrolling and safety provisioning.
    "test_screen_auto_playlist.py",
    # Custom bookings cap locations and enforce max screens.
    "test_custom_booking_screen_cap.py",
}

# Pure-logic tests: no database, no import-time engine, safe to run in-process.
# Listed here only so the orphan guard knows they are accounted for; pytest.ini's
# python_files is what actually collects them.
PURE_MODULES = {
    "test_media_worker.py",
    "test_storage_override_shape.py",
    "test_rotation.py",
    "test_rollout_policy.py",
    "test_media_storage.py",
    "test_alerting.py",
    "test_maps_link.py",
    "test_rendition_defaults.py",
    # Pure logic over a fake Organization; no database, no engine at import.
    "test_storage_prefix.py",
    # Pure string rewriting over the TV hand-back link; reads no rows.
    "test_tv_deep_link.py",
    # Pricing a custom run in screen-days; arithmetic over a fake plan, no session.
    "test_plan_quote.py",
    # Airtime formatting on the client report. Committed without being registered in
    # either place, so it had never run -- the exact state this guard exists to catch.
    "test_report_airtime.py",
    # Screen-reading half of scripts/tv_remote_test.py: telling a focused button from
    # nothing focused, which is the difference between a working remote and the bug.
    "test_tv_remote_test_parsing.py",
    "test_no_embedded_credentials.py",
    # Serving media from a published bucket, with no credential anywhere.
    "test_public_media_bucket.py",
    # The pairing code must outlive the moment the player replaces it.
    "test_pairing_code_window.py",
    # Hosted databases are refused while tests run (backend/database.py).
    "test_database_guard.py",
    # Purge sweeps match whole folders, so workspace 3 cannot delete workspace 30.
    "test_purge_prefix.py",
    # Operator scripts that delete or overwrite data must stop at a hosted database.
    "test_script_guard.py",
    # Which X-Forwarded-For entry the rate limiter counts a request against.
    "test_limiter_key.py",
    # Names typed by a tenant survive being put into a PDF paragraph.
    "test_report_markup.py",
}


def _postgres_reachable() -> bool:
    """Whether a server is listening on the port the scripts create their databases on.

    Cached for the run: 16 scripts would otherwise each pay the connect timeout.
    """
    global _PG_REACHABLE
    if _PG_REACHABLE is None:
        probe = socket.socket()
        probe.settimeout(3)
        try:
            probe.connect(("127.0.0.1", 5432))
            _PG_REACHABLE = True
        except OSError:
            _PG_REACHABLE = False
        finally:
            probe.close()
    return _PG_REACHABLE


_PG_REACHABLE = None

# Scripts that build their own throwaway Postgres database and cannot run without a
# server. Distinguished from the rest so that a machine with no database reports "these
# were skipped, and why" rather than sixteen identical connection tracebacks that bury
# whatever else went wrong in the run.
NEEDS_POSTGRES = ISOLATED_SCRIPTS - {
    "test_sqlite_utc.py",
    "test_release_rollout.py",
    "test_plan_purchase.py",
    # Falls back to SQLite when no server is listening; nothing it checks is
    # dialect-specific, so it still runs on a machine with no database.
    "test_google_signin.py",
    # Skips itself when Redis is absent; falls back to SQLite otherwise.
    "test_ws_connection_pool.py",
    # Playlist resolution is plain Python over the ORM, so SQLite exercises it fully.
    "test_playlist_resolution_parity.py",
    # Both create their own SQLite file and check plain Python over the ORM. Listed here or
    # a machine with no Postgres would skip them and report a green run that proved nothing.
    "test_subscription_expiry.py",
    "test_admin_lifecycle_controls.py",
    "test_storage_accounting.py",
    "test_screen_reconnect.py",
    "test_factory_reset_reconnect.py",
    "test_ota_reaches_a_settled_fleet.py",
    "test_maintenance_pin.py",
    "test_assigned_ad_plays.py",
    "test_screen_update_now.py",
    "test_device_diagnostics.py",
    "test_screen_timeline_follows_booking.py",
    # All three build their own SQLite file and check plain Python over the ORM. Listed
    # here or a machine with no Postgres would skip them and report a green run that
    # proved nothing.
    "test_storage_attribution.py",
    "test_media_deletion.py",
    "test_tenant_purge.py",
    # Each of these builds its own SQLite file. Without a server they were skipped and reported
    # green while proving nothing -- test_bring_to_front, test_ad_counting_detail and
    # test_play_log_aggregation cover offline counting and remote commands.
    "test_bring_to_front.py",
    "test_ad_counting_detail.py",
    "test_play_log_aggregation.py",
    "test_device_token_type.py",
    "test_register_takeover.py",
    "test_cross_tenant_binding.py",
    "test_client_ad_contact_safety.py",
    "test_booking_deletion_guards.py",
    "test_ws_unauthenticated.py",
    "test_legacy_auth_count.py",
    "test_token_binding.py",
    "test_upload_hardening.py",
    "test_emergency_sync.py",
    "test_plan_limits.py",
    "test_plan_renewal_rules.py",
    "test_media_supervisor_claim.py",
    "test_alert_resolution.py",
    "test_playlist_item_transition_gate.py",
    "test_billing_catalog_seed.py",
    "test_plan_feature_gates.py",
    "test_admin_billing.py",
    "test_public_plans.py",
    "test_custom_request_contact.py",
    "test_invoice_brand_signoff.py",
    "test_offline_play_reporting.py",
    "test_billing_summary_limits.py",
    "test_pending_commands_queue.py",
    "test_alert_email_notification.py",
    "test_dashboard_ws_act_as.py",
    "test_sync_branding.py",
    "test_kiosk_lock_pin.py",
    "test_kiosk_lock_enabled.py",
    "test_admin_audit_log.py",
    "test_admin_query_budget.py",
    "test_admin_guards.py",
    # "View workspace (read-only)" refuses every write: a static sweep of every write
    # route plus live checks across clients, playlists, branding and billing.
    "test_act_as_read_only.py",
    # Mission Control: numbers correct, cache works, query count flat regardless of tenant count.
    "test_admin_overview.py",
    # Every type matched, literal % escaped, short queries return nothing, non-admin refused.
    "test_admin_search.py",
    # The shared audit list behind Tenant 360 Activity and the Audit Log page: filters, paging.
    "test_admin_audit_list.py",
    # Cron heartbeat wrapper: job result/exception pass through, heartbeat written, Redis failure non-fatal.
    "test_cron_heartbeats.py",
    # Admin zero-touch QR binds to the CHOSEN tenant, not the operator's own org.
    "test_admin_provisioning_qr.py",
    # Admin payments list pagination (offset/limit + X-Total-Count) and kind/provider filters.
    "test_admin_payments_paging.py",
    "test_plan_impact.py",
}


def pytest_collect_file(parent, file_path):
    # TESTS_SKIP_ISOLATED=1 leaves the isolated scripts to tests/run_isolated.py, which runs
    # each in its own process. CI runs the pure modules through pytest and the scripts through
    # that runner, so neither is run twice.
    if file_path.name in ISOLATED_SCRIPTS and not os.environ.get("TESTS_SKIP_ISOLATED"):
        return ScriptFile.from_parent(parent, path=file_path)
    return None


def pytest_collection_modifyitems(config, items):
    """Fail loudly if a script also got imported as a module.

    pytest.ini restricts `python_files` so these scripts are never imported, but that is
    easy to undo by accident. If both collectors ever pick up the same file the suite
    silently loses its database isolation, so surface it instead.
    """
    seen = {}
    for item in items:
        name = getattr(item.path, "name", None)
        if name in ISOLATED_SCRIPTS:
            seen.setdefault(name, []).append(type(item).__name__)
    doubled = {n: k for n, k in seen.items() if len(k) > 1}
    if doubled:
        raise pytest.UsageError(
            "These files were collected twice (as a script AND as a module), which breaks "
            f"database isolation: {doubled}. Check python_files in pytest.ini."
        )


class ScriptFile(pytest.File):
    def collect(self):
        yield ScriptItem.from_parent(self, name=self.path.stem)


class ScriptItem(pytest.Item):
    def runtest(self):
        if self.path.name in NEEDS_POSTGRES and not _postgres_reachable():
            if os.environ.get("CI"):
                # A skip exits 0, so a CI job that lost its database would go green having run
                # none of these. There the absence is the bug.
                pytest.fail(
                    f"{self.path.name} needs PostgreSQL on localhost:5432 and CI provides one; "
                    "the service container did not come up",
                    pytrace=False,
                )
            pytest.skip(
                "PostgreSQL is not reachable on localhost:5432; this script creates its "
                "own throwaway database and cannot run without a server"
            )
        result = subprocess.run(
            [sys.executable, str(self.path)],
            capture_output=True,
            text=True,
            cwd=str(self.path.parents[1]),
        )
        if result.returncode != 0:
            raise AssertionError(
                f"{self.path.name} exited {result.returncode}\n"
                f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
            )

    def repr_failure(self, excinfo):
        return str(excinfo.value)

    def reportinfo(self):
        return self.path, 0, f"script: {self.path.name}"


def pytest_collection_finish(session):
    """Fail if any tests/test_*.py is collected by neither mechanism.

    A file that is in neither ISOLATED_SCRIPTS nor python_files is simply not run, and
    nothing says so -- the suite still exits 0. Six files sat in that state. Checking the
    directory against both registries turns a silent omission into a failed run.
    """
    directory = pathlib.Path(__file__).parent
    on_disk = {p.name for p in directory.glob("test_*.py")}
    registered = ISOLATED_SCRIPTS | PURE_MODULES
    orphans = sorted(on_disk - registered)
    if orphans:
        raise pytest.UsageError(
            "These test files are not collected by anything and never ran: "
            f"{orphans}. Add each to ISOLATED_SCRIPTS in tests/conftest.py (if it owns "
            "a database) or to python_files in pytest.ini (if it is pure logic)."
        )
    missing = sorted(registered - on_disk)
    if missing:
        raise pytest.UsageError(
            f"These files are registered but do not exist: {missing}."
        )

    # Every isolated script must CHOOSE its database before it imports backend.
    #
    # The assignment at the top of this file only protects the pytest path. These scripts are
    # meant to be run directly (`python tests/test_quotas.py`), and a script that names no
    # database lets backend/database.py fall back to load_dotenv() -- which on a developer
    # machine binds the engine to the PRODUCTION Supabase database. Two of them did, and were
    # found to have created organisations, screens and play logs in production, dated to test
    # runs. Nothing said so; the tests passed either way, which is why it went unnoticed.
    #
    # Checked by reading the source rather than by running it, because by the time the module
    # is imported the damage is already done.
    stray = []
    for script in sorted(ISOLATED_SCRIPTS):
        source = (directory / script).read_text(encoding="utf-8", errors="ignore")
        before_backend = source.split("import backend", 1)[0].split("from backend", 1)[0]
        if "DATABASE_URL" not in before_backend:
            stray.append(script)
    if stray:
        raise pytest.UsageError(
            "These scripts do not set DATABASE_URL before importing backend, so run on "
            f"their own they write to whatever backend/.env points at: {stray}. Set "
            'os.environ["DATABASE_URL"] to a throwaway database at the top of each.'
        )

