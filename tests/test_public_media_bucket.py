"""A published bucket serves media with no credentials at all.

The deployment lost its storage credentials, so `/api/media/<key>` answered 503 and every
thumbnail, report image and TV advert went blank. Publishing the bucket read-only fixes the
serving half without a credential existing anywhere -- but only if the route checks for it
BEFORE it checks for a key, which is the thing worth pinning down.

Pure logic: no database, no network, no boto3 client is ever constructed.
"""
import os
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

os.environ.setdefault("SECRET_KEY", "pytest-public-bucket")
# setdefault, not assignment: pytest's conftest has already pointed this process at an in-memory
# database, and overwriting it here at import time changed the engine other in-process modules
# (test_media_worker) were collected against.
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest  # noqa: E402

from backend import media_urls  # noqa: E402


def _blank_credentials() -> None:
    # Blanked AFTER the import, not before: media_urls loads backend/.env at import time, so a
    # developer machine with real credentials in that file would otherwise put them straight
    # back and this suite would assert nothing. `_setting` treats blank as unset.
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        os.environ[name] = ""


@pytest.fixture(autouse=True)
def _contained_environment():
    """Blank the credentials for one test and put the environment back afterwards.

    This used to blank them (and set R2_PUBLIC_BASE_URL) at import time and never restore them.
    Every other in-process module is collected before any test runs, so they all inherited the
    blanks -- which is why this file could not be listed in pytest.ini without breaking
    test_media_worker.
    """
    saved = dict(os.environ)
    _blank_credentials()
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def _serve(key: str, range_header: str | None = None):
    """Call the route function directly, without a database or an app fixture."""
    from backend.main import serve_media

    class _Request:
        headers = {"range": range_header} if range_header else {}

    return serve_media(key, _Request())


def test_public_base_is_a_location_not_a_secret():
    """Blank unless configured, and trailing slashes never produce a doubled one."""
    os.environ.pop("R2_PUBLIC_BASE_URL", None)
    assert media_urls.public_media_base() == ""

    os.environ["R2_PUBLIC_BASE_URL"] = "https://pub-abc123.r2.dev/"
    assert media_urls.public_media_base() == "https://pub-abc123.r2.dev"


def test_serves_without_credentials():
    os.environ["R2_PUBLIC_BASE_URL"] = "https://pub-abc123.r2.dev"
    assert not media_urls.is_s3_enabled(), "precondition: no credentials are configured"

    response = _serve("org-67/9685365d-0d94-4dee-ae61-8f7deefbf3a3.png")

    assert response.status_code == 307
    assert response.headers["location"] == (
        "https://pub-abc123.r2.dev/org-67/9685365d-0d94-4dee-ae61-8f7deefbf3a3.png"
    )
    # A signature-free URL does not expire, so it is safe for a shared cache to keep --
    # which is the difference that keeps an R2 egress bill flat under a fleet of TVs.
    assert "public" in response.headers["cache-control"]


def test_without_a_public_base_it_still_refuses_rather_than_guessing():
    """No credentials and no published bucket must stay a loud 503, not a broken redirect."""
    from fastapi import HTTPException

    os.environ.pop("R2_PUBLIC_BASE_URL", None)
    try:
        _serve("org-67/whatever.png")
    except HTTPException as exc:
        assert exc.status_code == 503
    else:
        raise AssertionError("expected 503 when neither credentials nor a public base exist")


def test_key_escape_is_still_blocked_on_the_public_path():
    """The traversal guard must run before the redirect, or it stops applying at all."""
    from fastapi import HTTPException

    os.environ["R2_PUBLIC_BASE_URL"] = "https://pub-abc123.r2.dev"
    for bad in ("", "/etc/passwd", "org-1/../org-2/private.png"):
        try:
            _serve(bad)
        except HTTPException as exc:
            assert exc.status_code == 404
        else:
            raise AssertionError(f"expected 404 for {bad!r}")


def test_range_parsing():
    """Android's player sends these; an off-by-one here is a video that will not seek."""
    from backend.main import _parse_range

    assert _parse_range("bytes=0-99", 1000) == (0, 99)
    assert _parse_range("bytes=500-", 1000) == (500, 999)
    assert _parse_range("bytes=-200", 1000) == (800, 999)
    # Past the end, malformed, absent, and multi-range all fall back to a whole-file reply.
    for bad in ("bytes=2000-3000", "bytes=abc-def", "junk", None, "bytes=0-9,20-29"):
        assert _parse_range(bad, 1000) is None, bad


def test_database_mirror_is_absent_without_the_table():
    """No mirror table must read as "not mirrored", never as a 500 on a media request."""
    from backend.main import _serve_media_from_database

    assert _serve_media_from_database("org-1/nothing.png", None) is None


if __name__ == "__main__":
    _blank_credentials()
    test_range_parsing()
    test_database_mirror_is_absent_without_the_table()
    test_public_base_is_a_location_not_a_secret()
    test_serves_without_credentials()
    test_without_a_public_base_it_still_refuses_rather_than_guessing()
    test_key_escape_is_still_blocked_on_the_public_path()
    print("OK - published bucket serves media with no credentials")
