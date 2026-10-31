"""confirm_destructive() lets local databases through and stops hosted ones without consent.

Pure: it never opens a connection or reads the environment.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from backend.script_guard import confirm_destructive  # noqa: E402

HOSTED = "postgresql://u:p@db.example.supabase.co:5432/postgres"


def _no_prompt(_message):
    raise AssertionError("a local database must never be prompted about")


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///./x.db",
        "sqlite:///:memory:",
        "postgresql://u:p@localhost:5432/db",
        "postgresql://u:p@127.0.0.1/db",
    ],
)
def test_local_databases_pass_without_a_prompt(url):
    confirm_destructive("wipe things", url, argv=[], ask=_no_prompt)


def test_hosted_database_without_the_flag_is_refused():
    with pytest.raises(SystemExit) as stop:
        confirm_destructive("wipe things", HOSTED, argv=[], ask=_no_prompt)
    assert "REFUSED" in str(stop.value) and "db.example.supabase.co" in str(stop.value)


def test_hosted_database_with_the_flag_needs_the_host_typed():
    with pytest.raises(SystemExit) as stop:
        confirm_destructive("wipe things", HOSTED, argv=["--yes"], ask=lambda _m: "something else")
    assert "did not match" in str(stop.value)


def test_hosted_database_with_flag_and_matching_host_proceeds():
    asked = []
    confirm_destructive(
        "wipe things", HOSTED, argv=["--yes"], ask=lambda m: asked.append(m) or "DB.Example.Supabase.co"
    )
    assert asked and "db.example.supabase.co" in asked[0]


def test_no_terminal_means_refusal_not_a_silent_yes():
    def closed(_message):
        raise EOFError

    with pytest.raises(SystemExit):
        confirm_destructive("wipe things", HOSTED, argv=["--yes"], ask=closed)


GUARDED_SCRIPTS = [
    "reset_fresh_start.py",
    "scripts/seed_demo_playlist_new_db.py",
    "scripts/reorganize_r2_storage.py",
    "tests/manual_pipeline_check.py",
    "scripts/test_live_bring_to_front.py",
    "scripts/test_live_hardware_dedup.py",
    "scripts/test_live_offline_counting_drain.py",
    "scripts/test_live_super_admin_flow.py",
    "scripts/test_google_approval_lifecycle.py",
]


@pytest.mark.parametrize("script", GUARDED_SCRIPTS)
def test_every_destructive_script_refuses_a_hosted_database(script):
    """Run each one pointed at a host that cannot resolve; it must stop before any work."""
    import os
    import subprocess

    repo = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "DATABASE_URL": "postgresql://u:p@db.example.invalid:5432/postgres",
        "SECRET_KEY": "guard-test",
        "AWS_ACCESS_KEY_ID": "mock",
        "AWS_SECRET_ACCESS_KEY": "mock",
        # A script under tests/ puts tests/, not the repo, first on the import path.
        "PYTHONPATH": str(repo),
    }
    result = subprocess.run(
        [sys.executable, script],
        cwd=repo, env=env, capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
    )
    assert result.returncode != 0, f"{script} ran against a hosted database"
    assert "refus" in (result.stdout + result.stderr).lower(), result.stderr[-400:]


def test_deleted_one_offs_stay_deleted():
    repo = Path(__file__).resolve().parents[1]
    for name in ("seed_new_supabase", "set_org_name", "migrate_org_approvals", "setup_tablet_live_test"):
        assert not (repo / "scripts" / f"{name}.py").exists()


def test_recent_or_unreadable_bucket_objects_are_never_judged_orphans():
    """An upload writes the object before its database row, so a fresh object is not an orphan."""
    from datetime import datetime, timedelta, timezone

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "scripts"))
    import backend.main  # noqa: F401  (import order; see test_subscription_expiry)
    import cleanup_r2_orphans as cleanup

    def stamp(age):
        return (datetime.now(timezone.utc) - age).isoformat().replace("+00:00", "Z")

    assert cleanup._too_new_to_judge({"last_modified": stamp(timedelta(minutes=5))})
    assert not cleanup._too_new_to_judge({"last_modified": stamp(timedelta(days=2))})
    assert cleanup._too_new_to_judge({"last_modified": "not a date"})
    assert cleanup._too_new_to_judge({})
