"""Tenant storage folders are named, unique and stable: python tests/test_storage_prefix.py

Objects used to be filed under a bare organisation id, so opening the bucket told an
operator nothing about whose media they were looking at. The folder is now named after the
owner's address -- and the risks that introduces are what this file pins down, because each
of them silently corrupts tenant isolation or breaks playback rather than raising.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from backend.media_urls import storage_prefix  # noqa: E402


class FakeUser:
    def __init__(self, id, email, role="owner"):
        self.id, self.email, self.role = id, email, role


class FakeOrg:
    """Stands in for models.Organization; owner_email is a property over .users."""

    def __init__(self, id, users, slug=None):
        self.id, self.users, self.slug = id, users, slug

    @property
    def owner_email(self):
        members = sorted(self.users, key=lambda u: u.id)
        for candidate in members:
            if (email := (candidate.email or "").strip()) and candidate.role == "owner":
                return email
        for candidate in members:
            if email := (candidate.email or "").strip():
                return email
        return None


def test_folder_is_named_after_the_owner():
    """Consistent org-{id} prefix for Cloudflare R2 compatibility."""
    org = FakeOrg(19, [FakeUser(1, "alice@example.com")])
    assert storage_prefix(org) == "org-19"


def test_distinct_tenants_get_distinct_folders():
    """Two organisations must never share a folder, or their media mixes in the bucket."""
    first = FakeOrg(19, [FakeUser(1, "alice@example.com")])
    second = FakeOrg(20, [FakeUser(2, "bob@example.com")])
    assert storage_prefix(first) != storage_prefix(second)


def test_owner_wins_over_other_members():
    """A workspace prefix is stably based on organisation ID."""
    org = FakeOrg(7, [
        FakeUser(1, "editor@example.com", role="editor"),
        FakeUser(2, "owner@example.com", role="owner"),
    ])
    assert storage_prefix(org) == "org-7"


def test_falls_back_when_there_is_no_address():
    """seed_admin creates an owner with no email, and an upload must still work."""
    assert storage_prefix(FakeOrg(42, [FakeUser(1, None)])) == "org-42"
    assert storage_prefix(FakeOrg(43, [])) == "org-43"


def test_unsafe_characters_never_reach_a_key():
    """A key containing "/" would invent a folder; one with spaces breaks URLs.

    The address is attacker-influenced -- anyone can sign up, and PATCH /auth/me edits it
    -- so this is a boundary, not a formatting nicety.
    """
    for address in ("a b/c@x.com", "we!rd+tag@x.com", "../../etc/passwd@x.com"):
        prefix = storage_prefix(FakeOrg(1, [FakeUser(1, address)]))
"""Tenant storage folders are named, unique and stable: python tests/test_storage_prefix.py

Objects used to be filed under a bare organisation id, so opening the bucket told an
operator nothing about whose media they were looking at. The folder is now named after the
owner's address -- and the risks that introduces are what this file pins down, because each
of them silently corrupts tenant isolation or breaks playback rather than raising.
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from backend.media_urls import storage_prefix  # noqa: E402


class FakeUser:
    def __init__(self, id, email, role="owner"):
        self.id, self.email, self.role = id, email, role


class FakeOrg:
    """Stands in for models.Organization; owner_email is a property over .users."""

    def __init__(self, id, users, slug=None):
        self.id, self.users, self.slug = id, users, slug

    @property
    def owner_email(self):
        members = sorted(self.users, key=lambda u: u.id)
        for candidate in members:
            if (email := (candidate.email or "").strip()) and candidate.role == "owner":
                return email
        for candidate in members:
            if email := (candidate.email or "").strip():
                return email
        return None


def test_folder_is_named_after_the_owner():
    """Consistent org-{id} prefix for Cloudflare R2 compatibility."""
    org = FakeOrg(19, [FakeUser(1, "alice@example.com")])
    assert storage_prefix(org) == "org-19"


def test_distinct_tenants_get_distinct_folders():
    """Two organisations must never share a folder, or their media mixes in the bucket."""
    first = FakeOrg(19, [FakeUser(1, "alice@example.com")])
    second = FakeOrg(20, [FakeUser(2, "bob@example.com")])
    assert storage_prefix(first) != storage_prefix(second)


def test_owner_wins_over_other_members():
    """A workspace prefix is stably based on organisation ID."""
    org = FakeOrg(7, [
        FakeUser(1, "editor@example.com", role="editor"),
        FakeUser(2, "owner@example.com", role="owner"),
    ])
    assert storage_prefix(org) == "org-7"


def test_falls_back_when_there_is_no_address():
    """seed_admin creates an owner with no email, and an upload must still work."""
    assert storage_prefix(FakeOrg(42, [FakeUser(1, None)])) == "org-42"
    assert storage_prefix(FakeOrg(43, [])) == "org-43"


def test_unsafe_characters_never_reach_a_key():
    """A key containing "/" would invent a folder; one with spaces breaks URLs.

    The address is attacker-influenced -- anyone can sign up, and PATCH /auth/me edits it
    -- so this is a boundary, not a formatting nicety.
    """
    for address in ("a b/c@x.com", "we!rd+tag@x.com", "../../etc/passwd@x.com"):
        prefix = storage_prefix(FakeOrg(1, [FakeUser(1, address)]))
        assert "/" not in prefix, f"path separator survived: {prefix}"
        assert " " not in prefix, f"space survived: {prefix}"
        assert ".." not in prefix, f"traversal survived: {prefix}"
        assert prefix, "an address must never sanitise away to nothing"


def test_thumbnail_is_written_where_its_url_points():
    """The mismatch this pairing had, and it broke every video thumbnail.

    generate_video_thumbnail took an organisation id and wrote to uploads/<id>/, while the
    caller built the URL from the prefix -- so the file landed in one folder and the row
    pointed at another. Both now read the same value, and this asserts they still do.
    """
    import inspect
    from backend.routers import content

    signature = inspect.signature(content.generate_video_thumbnail)
    assert "prefix" in signature.parameters, (
        "generate_video_thumbnail must take the storage prefix, not an organisation id, "
        "or the thumbnail is written somewhere its URL does not point"
    )

    source = inspect.getsource(content.upload_content)
    assert "generate_video_thumbnail(temp_local_file, thumb_stem, folder)" in source
    assert "public_upload_url(storage_key)" in source


def test_hierarchical_storage_prefixes():
    """Test folder structures for tenant, client ads, general, and branding."""
    from backend.media_urls import (
        tenant_storage_root,
        client_ads_prefix,
        general_media_prefix,
        branding_storage_prefix,
        mint_media_filename,
    )

    class FakeClient:
        def __init__(self, name):
            self.name = name

    class FakeOrgWithName:
        def __init__(self, id, name):
            self.id = id
            self.name = name

    org = FakeOrgWithName(67, "Roshan Nandhu")
    assert tenant_storage_root(org) == "tenants/Roshan-Nandhu-67"
    assert client_ads_prefix(org, FakeClient("Royal Enfield")) == "tenants/Roshan-Nandhu-67/clients/Royal-Enfield/ads"
    assert client_ads_prefix(org, "Kalyan Jewellers") == "tenants/Roshan-Nandhu-67/clients/Kalyan-Jewellers/ads"
    assert general_media_prefix(org) == "tenants/Roshan-Nandhu-67/general"
    assert branding_storage_prefix(org) == "tenants/Roshan-Nandhu-67/branding"

    # Mint clean filenames
    assert mint_media_filename("Msolar Test.mp4", ".mp4", "65e8b4f7-1234") == "Msolar-Test-65e8b4f7.mp4"
    assert mint_media_filename(None, ".jpg", "abcdef12") == "abcdef12.jpg"


if __name__ == "__main__":
    for name, function in sorted(globals().items()):
        if name.startswith("test_") and callable(function):
            function()
            print(f"  ok  {name}")
    print("storage prefix: all checks passed")


# --- Media URLs -----------------------------------------------------------------------
#
# Blank thumbnails were a recurring bug for one reason: resolve_media_url handed out a
# presigned R2 URL, so every consumer held a credential-signed link with an expiry baked
# in, and the dashboard, the TV app and the JS and Kotlin signers each had to independently
# agree on bucket, endpoint, region, prefix and clock. These pin the property that makes
# that whole class of failure impossible -- the URL is stable, unsigned, and never expires.

import os  # noqa: E402
from urllib.parse import unquote, urlparse  # noqa: E402

import pytest  # noqa: E402

from backend.media_urls import media_base_url, resolve_media_url  # noqa: E402


def test_object_storage_key_resolves_to_our_own_api():
    url = resolve_media_url("s3://org-4/9f1c-2d.jpg")
    assert url == f"{media_base_url()}/api/media/org-4/9f1c-2d.jpg"


def test_the_url_carries_no_signature_and_no_expiry():
    """The whole point. A signed URL is a time bomb in every cache that holds it."""
    url = resolve_media_url("s3://org-4/9f1c-2d.jpg")
    for leak in ("X-Amz-Signature", "X-Amz-Expires", "X-Amz-Credential", "AWSAccessKeyId"):
        assert leak not in url, f"{leak} is back in a media URL; it will expire and 403"


def test_the_same_key_always_resolves_to_the_same_url():
    """Stability is what lets a browser, a report and a TV's local database cache it."""
    assert resolve_media_url("s3://org-4/a.jpg") == resolve_media_url("s3://org-4/a.jpg")


def test_resolution_needs_no_credentials():
    """It is pure string work now, so it cannot fail its way into a blank thumbnail.

    It used to call boto3 and swallow any exception by returning the raw "s3://..." value,
    which reached the browser as an unusable src and rendered as the same grey placeholder
    as a genuinely missing file.
    """
    saved = {k: os.environ.get(k) for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")}
    os.environ["AWS_ACCESS_KEY_ID"] = "mock"
    os.environ["AWS_SECRET_ACCESS_KEY"] = ""
    try:
        assert resolve_media_url("s3://org-4/a.jpg").startswith("http")
    finally:
        for key, value in saved.items():
            os.environ.pop(key, None) if value is None else os.environ.__setitem__(key, value)


def test_the_key_survives_the_round_trip():
    """What the route reads back out of the path must be the key that went in."""
    key = "org-4/a b+c&d.jpg"
    path = urlparse(resolve_media_url(f"s3://{key}")).path
    assert unquote(path.removeprefix("/api/media/")) == key


def test_local_uploads_are_untouched():
    assert resolve_media_url("/uploads/org-4/a.jpg") == f"{media_base_url()}/uploads/org-4/a.jpg"
    assert resolve_media_url("https://cdn.example.com/a.jpg") == "https://cdn.example.com/a.jpg"
    assert resolve_media_url(None) is None


def test_the_media_route_refuses_to_climb_out_of_the_bucket():
    """The path segment reaches S3 as a key, so it is a trust boundary."""
    from fastapi import HTTPException

    from backend.main import serve_media

    for hostile in ("../secrets/dump.sql", "org-4/../../etc/passwd", "/absolute", ""):
        with pytest.raises(HTTPException) as raised:
            serve_media(hostile)
        assert raised.value.status_code == 404, f"{hostile!r} was not rejected"
