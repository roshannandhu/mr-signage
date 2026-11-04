"""Reading and writing media wherever it happens to live.

The transcoder needs a real file on disk -- ffmpeg cannot read `s3://` -- and then needs
to put four renditions and a thumbnail back where the original came from. Only that
fetch/store pair differs between local disk and R2/S3, so it is isolated here and the
worker stays a single code path.

Before this existed the worker simply refused: any `s3://` source raised
NotImplementedError, so with cloud storage configured -- the deployment the README
documents -- every video upload ended in `status="failed"` and no rendition was ever
produced. Capability-based rendition selection then had nothing to select from and every
panel, however cheap, was handed the original 4K file.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import pathlib
import shutil
import urllib.error
import urllib.request
from typing import Optional
from urllib.parse import quote, unquote

from .media_urls import is_s3_enabled, get_s3_config

logger = logging.getLogger(__name__)

UPLOAD_DIR = os.path.join(
    pathlib.Path(__file__).parent.parent.absolute(), "uploads"
)


def _cf_credentials() -> tuple[str, str, str] | None:
    """Returns (token, account_id, bucket) if Cloudflare Direct API is available, else None."""
    cfg = get_s3_config()
    token = cfg.get("cloudflare_api_token") or os.getenv("CLOUDFLARE_API_TOKEN")
    account_id = cfg.get("cloudflare_account_id") or os.getenv("CLOUDFLARE_ACCOUNT_ID") or "e92ddadc9098dbcf44aadc5653e7a0d3"
    bucket = cfg.get("bucket") or "mr-signage-media"
    if token and account_id:
        return token.strip(), account_id.strip(), bucket.strip()
    return None


def _cf_url(account_id: str, bucket: str, key: str) -> str:
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects/{quote(key, safe='/')}"


def _cf_store_file(local_path: pathlib.Path, key: str, content_type: str) -> bool:
    creds = _cf_credentials()
    if not creds:
        return False
    token, account_id, bucket = creds
    url = _cf_url(account_id, bucket, key)
    try:
        with open(local_path, "rb") as f:
            data = f.read()
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": content_type,
            },
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status == 200
    except Exception as exc:
        logger.warning("Cloudflare R2 direct upload failed for %s: %s", key, exc)
        return False


def _cf_delete_object(key: str) -> bool:
    creds = _cf_credentials()
    if not creds:
        return False
    token, account_id, bucket = creds
    url = _cf_url(account_id, bucket, key)
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}"},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status in (200, 204)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True
        logger.warning("Cloudflare R2 delete failed for %s: %s", key, e)
        return False
    except Exception as exc:
        logger.warning("Cloudflare R2 delete failed for %s: %s", key, exc)
        return False


def _cf_read_object(key: str) -> Optional[bytes]:
    creds = _cf_credentials()
    if not creds:
        return None
    token, account_id, bucket = creds
    url = _cf_url(account_id, bucket, key)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read() or None
    except Exception as exc:
        logger.debug("Cloudflare R2 read failed for %s: %s", key, exc)
        return None


def _cf_download_file(key: str, destination: pathlib.Path) -> bool:
    creds = _cf_credentials()
    if not creds:
        return False
    token, account_id, bucket = creds
    url = _cf_url(account_id, bucket, key)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(destination, "wb") as f_out:
            shutil.copyfileobj(resp, f_out)
        return True
    except Exception as exc:
        logger.warning("Cloudflare R2 download failed for %s: %s", key, exc)
        return False


def _client():
    import boto3

    cfg = get_s3_config()
    return boto3.client(
        "s3",
        endpoint_url=cfg["endpoint_url"],
        aws_access_key_id=cfg["aws_access_key_id"],
        aws_secret_access_key=cfg["aws_secret_access_key"],
        region_name=cfg["region_name"],
    )


def storage_key_for(stored_url: str) -> str:
    """The backend-relative key inside a stored location.

    Both schemes carry the same "<org_id>/<filename>" key; they differ only in prefix.
    """
    if stored_url.startswith("s3://"):
        return stored_url[len("s3://"):]
    if "/uploads/" in stored_url:
        return stored_url.split("/uploads/", 1)[1]
    raise ValueError(f"Unrecognised storage location: {stored_url}")


def is_remote(stored_url: str) -> bool:
    return stored_url.startswith("s3://")


def fetch_to(stored_url: str, destination: pathlib.Path) -> pathlib.Path:
    """Put the bytes of `stored_url` at `destination` and return it.

    A local file is copied rather than used in place: the worker writes its renditions
    beside the file it is given, and a scratch directory keeps half-finished output out of
    the uploads tree if the transcode dies midway.
    """
    key = storage_key_for(stored_url)
    destination.parent.mkdir(parents=True, exist_ok=True)

    if is_remote(stored_url):
        if _cf_download_file(key, destination):
            return destination
        cfg = get_s3_config()
        _client().download_file(cfg["bucket"], key, str(destination))
        return destination

    source = pathlib.Path(UPLOAD_DIR) / key
    if not source.exists():
        raise FileNotFoundError(f"File not found: {source}")
    shutil.copy2(source, destination)
    return destination


def read(location: str | None) -> Optional[bytes]:
    """Bytes of a stored object, read straight from wherever it lives. Never raises.

    Accepts a stored location ("s3://<key>", "/uploads/<key>") or any of the public URLs
    `resolve_media_url` builds from one, and returns None for anything else -- a genuinely
    external URL, or an object that is not there.

    This exists for the server-side renderers. The PDF reports used to fetch their own
    public URL over HTTP for every image on a page they were already rendering: the API
    called itself back through the load balancer, which needs the deployment to be publicly
    reachable from inside its own container, costs a round trip per image under a 10s
    timeout, and now would mean following a redirect just to reach bytes this process can
    already read. A report is generated with the same credentials that wrote the object.
    """
    if not location:
        return None

    path = location.split("?", 1)[0]
    if "://" in path and not path.startswith("s3://"):
        # Drop scheme and host, keeping the path. A URL with no path is not one of ours.
        rest = path.split("://", 1)[1]
        if "/" not in rest:
            return None
        path = rest[rest.index("/"):]

    if "/uploads/" in path:
        local = pathlib.Path(UPLOAD_DIR) / unquote(path.split("/uploads/", 1)[1])
        root = pathlib.Path(UPLOAD_DIR).resolve()
        try:
            if local.resolve().is_relative_to(root) and local.is_file():
                return local.read_bytes() or None
        except (OSError, ValueError):
            pass
        return None

    if path.startswith("s3://"):
        key = path[len("s3://"):]
    elif "/api/media/" in path:
        key = path.split("/api/media/", 1)[1]
    else:
        return None

    if not key or not is_s3_enabled():
        return None
    cf_data = _cf_read_object(unquote(key))
    if cf_data is not None:
        return cf_data
    try:
        cfg = get_s3_config()
        return _client().get_object(Bucket=cfg["bucket"], Key=unquote(key))["Body"].read() or None
    except Exception:
        return None


# Not in every platform's mime table (Windows reads its registry), and logos may be WebP.
mimetypes.add_type("image/webp", ".webp")


def content_type_for(key: str) -> str:
    """The Content-Type to store an object under, decided by what WE name the file.

    Uploads used to be stored under whatever type the client claimed. A logo sent as
    `text/html` under a .png name was then served as HTML from the bucket's own origin, which
    is script running next to everything else on that origin. The extension is ours (every
    upload route allow-lists it), so the type follows from it; anything unrecognised is served
    as an opaque download rather than rendered.
    """
    return mimetypes.guess_type(key)[0] or "application/octet-stream"


def store(local_path: pathlib.Path, key: str, content_type: Optional[str] = None) -> str:
    """Persist `local_path` under `key` and return the location to save on the row.

    The return value matches whatever `routers/content.py` would have written for a direct
    upload -- `s3://<key>` or `/uploads/<key>` -- so a rendition is indistinguishable from
    an original as far as `resolve_media_url` is concerned.
    """
    if is_s3_enabled():
        extra_type = content_type_for(key)
        # Try Cloudflare Direct REST API first if configured
        if _cf_store_file(local_path, key, extra_type):
            return f"s3://{key}"
        cfg = get_s3_config()
        # The extension decides. `content_type` is accepted for existing callers but never
        # trusted: some pass the uploader's own claim, and a claimed `text/html` on a .png key
        # was served as HTML. An unrecognised extension is stored as an opaque download.
        extra = {"ContentType": extra_type}
        _client().upload_file(str(local_path), cfg["bucket"], key, ExtraArgs=extra)
        return f"s3://{key}"

    target = pathlib.Path(UPLOAD_DIR) / key
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.resolve() != local_path.resolve():
        shutil.copy2(local_path, target)
    return f"/uploads/{key}"


def _forget_database_mirror(key: str) -> None:
    """Drop the `media_blob` row for `key`, if that mirror is present.

    Production served media straight out of Postgres for as long as R2 was unconfigured,
    and nothing has ever deleted a row from that table -- so every asset removed in that
    period kept its bytes in the database permanently, where no bucket sweep can see them.

    The table has no model and no migration; it was created out of band and may simply not
    exist. Every failure here is therefore survivable and must not stop the real delete.
    """
    if not key:
        return
    try:
        from sqlalchemy import text

        from . import database

        with database.engine.begin() as connection:
            connection.execute(
                text("DELETE FROM media_blob WHERE key = :key"), {"key": key}
            )
    except Exception as exc:
        logger.debug("No media_blob row removed for %s: %s", key, exc)


def delete(stored_url: str) -> bool:
    """Remove a stored object. Best effort -- a missing object is not an error.

    Local deletion goes through `media_urls.delete_stored_file`, which carries the
    path-escape guard; this adds the object-storage half so a deleted asset does not leave
    its bytes (and the storage quota they consume) behind in the bucket forever.

    Best effort is not the same as silent. This used to catch every exception and return a
    bare False that `delete_content` discarded without looking, so with credentials absent
    -- exactly the state a deployment is in while it still serves from the database mirror
    -- every delete was a no-op and the rows vanished anyway. Whatever goes wrong now says
    so in the log, and says which of the two it was: storage that is not configured at all,
    or a bucket that refused.
    """
    if not stored_url:
        return False

    try:
        key = storage_key_for(stored_url)
    except ValueError:
        logger.warning("Not a storage location, nothing to delete: %s", stored_url)
        return False

    # Cleared whichever backend holds the object: the mirror shadows the same key.
    _forget_database_mirror(key)

    if not is_remote(stored_url):
        from .media_urls import delete_stored_file

        return delete_stored_file(stored_url, UPLOAD_DIR)

    if not is_s3_enabled():
        # boto3 would still build a client and fail at signing time, which reads exactly
        # like the bucket refusing. It is a different problem and needs a different fix.
        logger.warning("Object storage is not configured; %s left in the bucket", key)
        return False

    if _cf_delete_object(key):
        return True

    cfg = get_s3_config()
    try:
        _client().delete_object(Bucket=cfg["bucket"], Key=key)
        return True
    except Exception as exc:
        logger.warning("Bucket %s refused to delete %s: %s", cfg["bucket"], key, exc)
        return False
