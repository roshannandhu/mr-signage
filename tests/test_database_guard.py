"""backend/database.py must refuse a hosted database whenever tests are running.

backend/.env points at the live database on a developer machine, and a test script that
forgets to set its own DATABASE_URL would otherwise bind to it and write there. Pure: nothing
here opens a connection.
"""
import os
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# conftest.py has already pointed DATABASE_URL at in-memory SQLite, so this import is safe.
from backend import database  # noqa: E402

HOSTED = "postgresql://u:p@db.example.supabase.co:5432/postgres"


def test_pytest_counts_as_a_test_run():
    assert database._running_under_tests()


@pytest.mark.parametrize(
    "url",
    [
        HOSTED,
        "postgresql://u:p@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres",
        "postgresql://u:p@10.0.0.5:5432/db",
    ],
)
def test_hosted_database_is_refused(url):
    with pytest.raises(RuntimeError, match="hosted database"):
        database._refuse_remote_database_in_tests(url)


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///:memory:",
        "sqlite:///./scratch.db",
        "postgresql://u:p@localhost:5432/db",
        "postgresql://u:p@127.0.0.1:5432/db",
        "postgresql://u:p@[::1]:5432/db",
    ],
)
def test_local_database_is_allowed(url):
    database._refuse_remote_database_in_tests(url)


def _run(args):
    # SECRET_KEY and both AWS keys are what importing the screens router needs; without
    # them it would fail on those rather than reaching the guard.
    env = {
        **os.environ,
        "DATABASE_URL": HOSTED,
        "SECRET_KEY": "guard-test",
        "AWS_ACCESS_KEY_ID": "mock",
        "AWS_SECRET_ACCESS_KEY": "mock",
    }
    return subprocess.run(
        [sys.executable, *args], cwd=REPO, env=env, capture_output=True, text=True, timeout=120
    )


def test_importing_backend_with_a_hosted_url_fails_before_an_engine_exists():
    result = _run(["-c", "import pytest, backend.database"])
    assert result.returncode != 0
    assert "Refusing to run tests against a hosted database" in result.stderr


def test_running_a_tests_script_directly_is_also_guarded():
    # test_tv_deep_link imports the screens router (so backend.database) and is run here as
    # a plain script, which is how a developer runs a single test file by hand.
    result = _run(["tests/test_tv_deep_link.py"])
    assert result.returncode != 0
    assert "Refusing to run tests against a hosted database" in result.stderr
