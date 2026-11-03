"""Turning a stored media location into something a browser or a TV can fetch.

This lives in its own module because both the schemas and the routers need it, and the
schemas cannot import from a router without a cycle.

Locations are stored host-independently — "/uploads/<org>/<file>" for local storage, or
"s3://<key>" for object storage — and only become absolute when they are served. Baking an
origin in at upload time is what previously left every row pointing at a stale
http://localhost:8000 that no phone or TV could ever reach.
"""
import os
import pathlib
from functools import lru_cache
from urllib.parse import quote
from dotenv import load_dotenv

_env_path = pathlib.Path(__file__).parent / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path)


# Characters that must never reach a storage key. "/" would invent a nested folder and
# ".." a parent one; whitespace and control characters break the URLs built from the key.
_UNSAFE_PUNCTUATION = '/\\?%*:|"<>'
_UNSAFE_IN_KEY = str.maketrans(
    {character: "-" for character in _UNSAFE_PUNCTUATION}
    # Space and the control range: both are legal in an S3 key and both make the URLs
    # built from it awkward or invalid, so neither should survive into one.
    | {chr(code): "-" for code in range(33)}
)


import re


def sanitize_storage_name(name: str | None, default: str = "item") -> str:
    """Sanitize name for S3/R2 path component, preserving capitalization and replacing
    spaces/special punctuation with hyphens without consecutive hyphens.
    """
    if not name or not name.strip():
        return default
    cleaned = name.strip().replace("'", "").replace("’", "")
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or default


def tenant_storage_root(organization) -> str:
    """Root folder for a tenant: 'tenants/<Tenant-Name>-<org_id>'."""
    if not organization or getattr(organization, "id", None) is None:
        return "tenants/shared"
    org_id = organization.id
    raw_name = getattr(organization, "name", None) or f"org-{org_id}"
    cleaned_name = re.sub(r"('s|\s+)*workspace$", "", raw_name.strip(), flags=re.IGNORECASE).strip()
    slug = sanitize_storage_name(cleaned_name or raw_name, default=f"org-{org_id}")
    return f"tenants/{slug}-{org_id}"


def client_ads_prefix(organization, client_or_name) -> str:
    """Folder prefix for client ads: 'tenants/<Tenant-Name>-<org_id>/clients/<Client-Name>/ads'."""
    root = tenant_storage_root(organization)
    if not client_or_name:
        return f"{root}/general"
    client_name = getattr(client_or_name, "name", None) or str(client_or_name)
    slug = sanitize_storage_name(client_name, default="client")
    return f"{root}/clients/{slug}/ads"


def general_media_prefix(organization) -> str:
    """Folder prefix for general tenant media: 'tenants/<Tenant-Name>-<org_id>/general'."""
    return f"{tenant_storage_root(organization)}/general"


def branding_storage_prefix(organization) -> str:
    """Folder prefix for branding logos: 'tenants/<Tenant-Name>-<org_id>/branding'."""
    return f"{tenant_storage_root(organization)}/branding"


def mint_media_filename(original_name: str | None, ext: str, unique_stem: str) -> str:
    """Combine clean media name with short unique hash, e.g. 'Msolar_Ad-65e8b4f7.mp4'."""
    short_hash = unique_stem.split("-")[0] if "-" in unique_stem else unique_stem[:8]
    if original_name:
        clean_title = sanitize_storage_name(os.path.splitext(original_name)[0], default="media")
        return f"{clean_title}-{short_hash}{ext}"
    return f"{unique_stem}{ext}"


def storage_prefix(organization) -> str:
    """Safe, alphanumeric bucket key prefix for an organization."""
    if not organization or getattr(organization, "id", None) is None:
        return "shared"
    return f"org-{organization.id}"


# Location only. Credentials are deliberately absent: this file is public, and a key
# committed here is a key that has to be rotated. Missing credentials must surface as
# "object storage is not configured", never as a silent fall back to a baked-in one.
_S3_DEFAULTS = {
    "S3_ENDPOINT_URL": "https://3fe4487a2b8fd1e2e541bf0e0f4c7c42.r2.cloudflarestorage.com",
    "S3_BUCKET_NAME": "olrac",
    "AWS_REGION": "auto",
}


# Storage settings an operator typed into the admin console, which win over the environment.
#
# Kept as a plain dict that something else fills in, rather than read from the database here,
# because this module is imported by `schemas` and sits underneath everything -- giving it a
# session would put the whole ORM behind a function that resolves a URL.
#
# They win on purpose. A deployment whose AWS_ACCESS_KEY_ID is the literal string "mock" is
# indistinguishable from an unconfigured one, and that is not hypothetical: it is what this
# product was actually running with while an operator edited the variable on a second Render
# service and watched nothing change. Someone who fills the form in wants it to take effect.
_OVERRIDES: dict[str, str] = {}


def _credential_shaped(name: str, value: str) -> bool:
    """Whether a stored value could be a real access key or secret at all.

    Every S3-compatible store issues key ids as 16+ alphanumerics (R2's are 32 hex) and
    secrets as 20+ characters of base64. An email address or a login password is neither.
    """
    if name == "AWS_ACCESS_KEY_ID":
        return value.isalnum() and 16 <= len(value) <= 128
    if name == "AWS_SECRET_ACCESS_KEY":
        return len(value) >= 20 and all(ch.isalnum() or ch in "/+=" for ch in value)
    return True


def apply_storage_overrides(values: dict[str, str] | None) -> None:
    """Replace the settings the console has supplied. Blank entries are dropped.

    So is a credential that cannot be one. Production once held the admin's login email as
    the access key and a password-shaped string as the secret -- the shape a browser's
    password manager leaves when it autofills a sign-in pair into two unfamiliar fields. An
    override beats the environment, so that pair switched object storage ON with credentials
    no store would accept: every media URL was signed with them, R2 answered "Credential
    access key has length 15, should be 32", and every TV reported its adverts as
    undownloadable while the files sat intact in the database mirror. Dropping it here lets
    the environment and the mirror behind it take over again.
    """
    import logging

    _OVERRIDES.clear()
    for key, value in (values or {}).items():
        cleaned = (value or "").strip()
        if not cleaned:
            continue
        if not _credential_shaped(key, cleaned):
            logging.getLogger(__name__).warning(
                "Ignoring stored %s: it is not shaped like a storage credential "
                "(length %d). Re-enter it, or clear it to use the environment.",
                key, len(cleaned),
            )
            continue
        _OVERRIDES[key] = cleaned


def storage_override_names() -> list[str]:
    """Which settings are currently coming from the console rather than the environment."""
    return sorted(_OVERRIDES)


def _setting(name: str) -> str:
    """One environment value, with a variable that is present but BLANK treated as unset.

    Non-credential settings fall back to `_S3_DEFAULTS`. Credentials never do -- there is
    no default to fall back to, so an unset or placeholder key yields "" and object storage
    reports itself disabled rather than authenticating as somebody.
    """
    override = _OVERRIDES.get(name)
    if override:
        return override
    val = (os.getenv(name) or "").strip()
    if not val:
        return _S3_DEFAULTS.get(name, "")
    if val.lower() == "mock" and not os.getenv("PYTEST_CURRENT_TEST"):
        return _S3_DEFAULTS.get(name, "")
    return val


def public_media_base() -> str:
    """Origin that serves the bucket's objects without a signature, or "" if there is none.

    An R2 bucket can expose its objects read-only over an r2.dev subdomain or a custom
    domain. Where that is enabled, serving media needs no credential at all: the key is
    already the capability (it carries the upload's UUID), which is the same posture the
    signed URL had. This is a location, not a secret, so it belongs with the other defaults.

    Reads only. Uploading still needs an API token, so a deployment with this set but no
    credentials serves everything it already has and writes new uploads to ephemeral local
    disk -- which is why `/api/health` reports that state separately rather than as "fine".
    """
    return _setting("R2_PUBLIC_BASE_URL").rstrip("/")


def get_s3_config() -> dict[str, str]:
    """Where object storage is and how to authenticate to it.

    The single source of truth: every boto3 client in this codebase is built from this and
    nothing else. A second answer to "which bucket?" living elsewhere is what produced a
    run of signature 403s.
    """
    bucket = _setting("S3_BUCKET_NAME") or "olrac"
    if bucket == "olrac-media":
        bucket = "olrac"
    return {
        "endpoint_url": _setting("S3_ENDPOINT_URL"),
        "aws_access_key_id": _setting("AWS_ACCESS_KEY_ID"),
        "aws_secret_access_key": _setting("AWS_SECRET_ACCESS_KEY"),
        "bucket": bucket,
        "region_name": _setting("AWS_REGION") or "auto",
    }


def is_s3_enabled() -> bool:
    cfg = get_s3_config()
    if os.getenv("PYTEST_CURRENT_TEST") and cfg["aws_access_key_id"] == "mock":
        return False
    return bool(cfg["aws_access_key_id"] and cfg["aws_access_key_id"].lower() != "mock")


@lru_cache(maxsize=4)
def _s3_client_for(endpoint_url: str, key_id: str, secret: str, region: str):
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name=region,
    )


def s3_client():
    """A boto3 client built from `get_s3_config` and nothing else.

    Cached, because /api/media signs a URL for every image in every grid and constructing a
    client re-parses botocore's service model each time -- but keyed on the configuration
    rather than cached outright, so changing an environment variable actually takes effect
    instead of being silently served by a client built from the old one.
    """
    cfg = get_s3_config()
    return _s3_client_for(
        cfg["endpoint_url"], cfg["aws_access_key_id"], cfg["aws_secret_access_key"], cfg["region_name"]
    )


@lru_cache(maxsize=1)
def _detect_lan_host() -> str:
    """Best-effort LAN address of this machine, so devices on the network can reach it.

    Cached: this runs for every asset and every rendition in a response, and a socket call
    per URL would turn one list request into hundreds of them.
    """
    import socket

    try:
        # No packet is actually sent; this only asks the OS which interface would be used.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            return probe.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def media_base_url() -> str:
    """Origin that players and browsers should fetch media from."""
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    if render_url:
        return render_url
    configured = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured and not ("localhost" in configured or "127.0.0.1" in configured):
        return configured
    public_host = os.getenv("PUBLIC_HOST", "").strip()
    if public_host and not ("127.0.0.1" in public_host or "localhost" in public_host):
        return f"http://{public_host}:{os.getenv('PORT', '8010')}"
    return "https://mr-signage.onrender.com"


def resolve_media_url(value: str | None) -> str | None:
    """Absolute, fetchable URL for a stored media location.

    An object-storage key resolves to this API's own `/api/media/<key>`, which redirects to
    a freshly signed URL each time it is followed. It used to resolve to a presigned R2 URL
    minted right here, and that is what made blank thumbnails a recurring bug rather than a
    one-off: every consumer ended up holding a credential-signed URL with an expiry baked
    into it, so the dashboard, the TV app and the JS and Kotlin signers each had to agree
    on bucket, endpoint, region, key prefix and clock. Any drift between them, any URL
    cached past its expiry, and any object that had since moved all came back as the same
    opaque 403 and rendered as the same grey placeholder.

    What this returns is pure string work -- no credentials, no clock, no network, nothing
    that can throw -- and it stays valid for as long as the object exists, so it is safe to
    cache in a browser, persist in a TV's local database or paste into a report.
    """
    if not value:
        return value
    if value.startswith("/uploads/"):
        return f"{media_base_url()}{value}"
    if not value.startswith("s3://"):
        return value
    key = quote(value.removeprefix("s3://").lstrip("/"), safe="/")
    return f"{media_base_url()}/api/media/{key}"


def delete_stored_file(stored_url: str | None, upload_dir: str) -> bool:
    """Remove the local file a stored location points at. Returns True if it went.

    Shared by content deletion and the orphan sweep so the
    path-escape guard exists once. A stored value is attacker-influenced in principle, so
    the resolved path must still sit inside the uploads root — otherwise a crafted
    "/uploads/../../etc/passwd" would delete outside it.
    """
    if not stored_url or "/uploads/" not in stored_url:
        return False
    relative_path = stored_url.split("/uploads/", 1)[1]
    root = pathlib.Path(upload_dir).resolve()
    try:
        target = pathlib.Path(upload_dir, relative_path).resolve()
    except (OSError, ValueError):
        return False
    if not target.is_relative_to(root) or not target.exists():
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False
