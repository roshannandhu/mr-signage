"""How many bytes a workspace occupies, and what is actually in the bucket.

Two separate questions that were both being answered badly.

**Per workspace.** Three places counted a tenant's storage and only one counted it right.
`routers/content.py` enforces the quota against Content AND MediaRendition rows, because a
transcode writes a full-size master and a smaller copy on top of the upload -- summing only
the source measured roughly a third of what a video really occupies. The admin console and
the tenant's own billing page each summed Content alone, so both showed about a third of the
truth: a tenant read "3 GB of 10 GB used" and was then refused an upload for being full.
That is the same disease as the screen limit before `effective_max_screens` -- the number
shown and the number enforced reading different columns -- so there is now one function and
all three call it.

**Across the platform.** Nothing reported what the bucket itself holds. A per-tenant quota is
a promise about what a workspace MAY use; the object store is what actually gets billed, and
the two drift apart through orphans, renditions of deleted content, and anything uploaded
before a prefix convention changed.
"""
import logging
import time

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models

logger = logging.getLogger(__name__)


# The settings an operator can fill in from the admin console, and the SystemSetting key each
# is stored under. Named for the environment variable they stand in for, so the console, the
# environment and `/api/health` all talk about the same four things.
STORAGE_SETTING_KEYS = {
    "AWS_ACCESS_KEY_ID": "storage.aws_access_key_id",
    "AWS_SECRET_ACCESS_KEY": "storage.aws_secret_access_key",
    "S3_ENDPOINT_URL": "storage.s3_endpoint_url",
    "S3_BUCKET_NAME": "storage.s3_bucket_name",
    "CLOUDFLARE_API_TOKEN": "storage.cloudflare_api_token",
    "CLOUDFLARE_ACCOUNT_ID": "storage.cloudflare_account_id",
}
SECRET_SETTINGS = {"AWS_SECRET_ACCESS_KEY", "CLOUDFLARE_API_TOKEN"}


def load_storage_overrides(db: Session) -> dict[str, str]:
    """Read the console's storage settings and make them live for this process.

    Called at start-up and again whenever they are saved, so the change takes effect without
    a restart -- and once more from the supervisor loop, so a second worker process picks up
    what the one handling the request wrote.
    """
    from ..media_urls import apply_storage_overrides

    try:
        rows = db.query(models.SystemSetting).filter(
            models.SystemSetting.key.in_(sorted(STORAGE_SETTING_KEYS.values()))
        ).all()
    except Exception:
        # A database that predates the settings table must not stop media serving.
        logger.exception("Could not read storage settings")
        return {}

    by_key = {row.key: row.value for row in rows}
    values = {
        name: by_key.get(setting_key, "")
        for name, setting_key in STORAGE_SETTING_KEYS.items()
    }
    apply_storage_overrides(values)
    return {name: value for name, value in values.items() if (value or "").strip()}


def save_storage_overrides(db: Session, values: dict[str, str | None]) -> dict[str, str]:
    """Write the console's storage settings, then make them live immediately.

    A blank or absent value clears that setting, which is how an operator goes back to
    whatever the environment says. Secrets are never read back out, so clearing is the only
    way to "see" that one is gone.
    """
    for name, setting_key in STORAGE_SETTING_KEYS.items():
        if name not in values:
            continue
        cleaned = (values.get(name) or "").strip()
        row = db.query(models.SystemSetting).filter(
            models.SystemSetting.key == setting_key
        ).first()
        if not cleaned:
            if row:
                db.delete(row)
            continue
        if row:
            row.value = cleaned
            row.updated_at = models.utcnow()
        else:
            db.add(models.SystemSetting(
                key=setting_key, value=cleaned,
                description="Object storage, set from the admin console",
            ))
    db.commit()
    return load_storage_overrides(db)


def organization_storage_used(db: Session, organization_id: int) -> int:
    """Bytes this workspace occupies: its content plus every rendition made from it.

    The one definition. Renditions count because they are real objects in the bucket that
    the workspace caused to exist, and because the quota has always been enforced against
    them -- a display that leaves them out is not a smaller truth, it is a different number
    from the one that refuses the next upload.
    """
    return storage_used_by_organization(db, [organization_id]).get(organization_id, 0)


def storage_used_by_organization(db: Session, organization_ids) -> dict[int, int]:
    """The same definition for many workspaces at once: two grouped queries, not two each."""
    ids = list(organization_ids)
    totals = {org_id: 0 for org_id in ids}
    if not ids:
        return totals

    content_rows = db.query(
        models.Content.organization_id, func.coalesce(func.sum(models.Content.file_size_bytes), 0)
    ).filter(models.Content.organization_id.in_(ids)).group_by(models.Content.organization_id).all()

    rendition_rows = db.query(
        models.Content.organization_id, func.coalesce(func.sum(models.MediaRendition.file_size_bytes), 0)
    ).join(
        models.Content, models.Content.id == models.MediaRendition.content_id
    ).filter(models.Content.organization_id.in_(ids)).group_by(models.Content.organization_id).all()

    for org_id, used in [*content_rows, *rendition_rows]:
        totals[org_id] += int(used or 0)
    return totals


# Listing a bucket is O(objects) and this is read by a dashboard, so the answer is held for
# a few minutes. Short enough that an upload shows up while someone is still looking at the
# page, long enough that a refresh loop cannot turn into thousands of LIST calls -- which R2
# and S3 both charge for.
_CACHE_SECONDS = 300
_cache: dict[str, object] = {}


def bucket_usage(force: bool = False) -> dict:
    """What the object store actually holds, in total and per workspace.

    Counted by walking the bucket rather than trusting the database, because the two are
    exactly what needs comparing: rows say what SHOULD be there, the bucket is what gets
    billed. Keys are minted as "org-<id>/..." by `media_urls.storage_prefix`, so the prefix
    is what attributes an object to a workspace; anything that does not match is reported as
    unattributed rather than quietly dropped, since orphans are the whole reason to look.

    Never raises. Storage being unreachable is a fact to display, not a reason for the admin
    console to fail to load.
    """
    cached = _cache.get("value")
    if not force and cached and time.time() - float(_cache.get("at", 0)) < _CACHE_SECONDS:
        return dict(cached, cached=True)  # type: ignore[arg-type]

    from ..media_urls import get_s3_config, is_s3_enabled, s3_client

    if not is_s3_enabled():
        return {
            "configured": False,
            "error": "Object storage has no credentials, so the bucket cannot be measured.",
            "total_bytes": 0,
            "object_count": 0,
            "by_prefix": {},
            "cached": False,
        }

    config = get_s3_config()
    by_prefix: dict[str, dict[str, int]] = {}
    total_bytes = 0
    object_count = 0

    try:
        paginator = s3_client().get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=config["bucket"]):
            for entry in page.get("Contents", []):
                size = int(entry.get("Size", 0))
                total_bytes += size
                object_count += 1
                key = entry.get("Key", "")
                if key.startswith("tenants/"):
                    parts = key.split("/")
                    prefix = f"{parts[0]}/{parts[1]}" if len(parts) > 1 else "tenants"
                else:
                    prefix = key.split("/", 1)[0] if "/" in key else "(no prefix)"
                bucket_for_prefix = by_prefix.setdefault(prefix, {"bytes": 0, "objects": 0})
                bucket_for_prefix["bytes"] += size
                bucket_for_prefix["objects"] += 1
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not measure the bucket: %s", exc)
        return {
            "configured": True,
            "error": f"The bucket could not be listed: {exc}",
            "total_bytes": 0,
            "object_count": 0,
            "by_prefix": {},
            "cached": False,
        }

    value = {
        "configured": True,
        "error": None,
        "bucket": config["bucket"],
        "total_bytes": total_bytes,
        "object_count": object_count,
        "by_prefix": by_prefix,
        "cached": False,
    }
    _cache["value"] = value
    _cache["at"] = time.time()
    return value


def forget_cached_usage() -> None:
    """Drop the measured-bucket cache.

    Anything that changes the bucket behind the dashboard's back has to call this, or the
    page keeps reporting the old totals for up to five minutes -- which, just after a purge,
    means showing the bytes of a workspace that no longer exists.
    """
    _cache.clear()


def delete_prefix(prefix: str) -> dict:
    """Delete every object under `prefix`. Returns what went, and what would not.

    The database rows are the authoritative list of a workspace's objects and are deleted
    through `media_storage.delete` before this runs; this is the backstop for everything the
    rows never knew about -- thumbnails and screenshots carry no size column, renditions
    orphaned by a relocation have no row left, and a workspace renamed after uploading has
    objects filed under every slug it has ever had.

    Refuses an empty prefix rather than treating it as "match everything", because the one
    caller is a purge and the cost of that mistake is the entire bucket.
    """
    # .strip() first: a prefix of "   " is not empty, survives strip("/"), and would have
    # been passed to list_objects_v2 as a filter matching every object in the bucket.
    if not prefix or not prefix.strip().strip("/"):
        raise ValueError("delete_prefix needs a prefix; refusing to match the whole bucket")

    # A folder, never a string prefix. "org-3" also matches "org-30/..." and "org-31/...",
    # so purging workspace 3 deleted other workspaces' media; "tenants/Acme-3" likewise
    # matches "tenants/Acme-30/...". Every key the app writes is "<prefix>/<file>".
    prefix = prefix.strip().rstrip("/") + "/"

    from ..media_storage import _cf_credentials
    from ..media_urls import get_s3_config, is_s3_enabled, s3_client
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    creds = _cf_credentials()
    if creds:
        token, account_id, bucket = creds
        deleted = 0
        failed = 0
        cursor = None
        try:
            while True:
                list_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects?prefix={urllib.parse.quote(prefix)}&per_page=1000"
                if cursor:
                    list_url += f"&cursor={urllib.parse.quote(cursor)}"
                req = urllib.request.Request(list_url, headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                res = data.get("result", [])
                if not res:
                    break
                for item in res:
                    k = item.get("key")
                    if not k:
                        continue
                    del_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects/{urllib.parse.quote(k, safe='/')}"
                    del_req = urllib.request.Request(del_url, headers={"Authorization": f"Bearer {token}"}, method="DELETE")
                    try:
                        with urllib.request.urlopen(del_req, timeout=20) as d_resp:
                            if d_resp.status in (200, 204):
                                deleted += 1
                            else:
                                failed += 1
                    except urllib.error.HTTPError as he:
                        if he.code == 404:
                            deleted += 1
                        else:
                            failed += 1
                    except Exception:
                        failed += 1
                cursor = data.get("result_info", {}).get("cursor")
                if not cursor:
                    break
            return {"deleted": deleted, "failed": failed, "error": None}
        except Exception as exc:
            logger.warning("Cloudflare R2 direct sweep of prefix %s failed: %s", prefix, exc)

    if not is_s3_enabled():
        return {"deleted": 0, "failed": 0, "error": "Object storage has no credentials."}

    config = get_s3_config()
    client = s3_client()
    deleted = 0
    failed = 0

    try:
        paginator = client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=config["bucket"], Prefix=prefix):
            keys = [{"Key": entry["Key"]} for entry in page.get("Contents", [])]
            if not keys:
                continue
            # delete_objects takes 1000 at a time and a page is already capped there, so a
            # page maps to exactly one call.
            response = client.delete_objects(
                Bucket=config["bucket"], Delete={"Objects": keys, "Quiet": True}
            )
            errors = response.get("Errors") or []
            failed += len(errors)
            deleted += len(keys) - len(errors)
            for error in errors:
                logger.warning(
                    "Could not delete %s: %s", error.get("Key"), error.get("Message")
                )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Sweep of prefix %s failed: %s", prefix, exc)
        return {"deleted": deleted, "failed": failed, "error": str(exc)}

    return {"deleted": deleted, "failed": failed, "error": None}


def purge_tenant_r2_storage(org_id: int, known_prefixes: list[str]) -> dict:
    """Purge every single object belonging to this workspace from R2.

    Sweeps all known folder prefixes, followed by a backstop scan across the bucket to remove
    any legacy or orphaned objects associated with this org_id.
    """
    total_deleted = 0
    total_failed = 0
    errors: list[str] = []

    for prefix in known_prefixes:
        if not prefix or not prefix.strip():
            continue
        res = delete_prefix(prefix)
        total_deleted += res.get("deleted", 0)
        total_failed += res.get("failed", 0)
        if res.get("error"):
            errors.append(res["error"])

    # Backstop scan for any lingering keys containing the org ID pattern
    from ..media_storage import _cf_credentials
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    creds = _cf_credentials()
    if creds:
        token, account_id, bucket = creds
        cursor = None
        org_patterns = (f"-{org_id}/", f"/{org_id}/", f"org-{org_id}/")
        try:
            while True:
                url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects?per_page=1000"
                if cursor:
                    url += f"&cursor={urllib.parse.quote(cursor)}"
                req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                items = data.get("result", [])
                if not items:
                    break
                for it in items:
                    key = it.get("key", "")
                    if any(pat in key for pat in org_patterns):
                        del_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket}/objects/{urllib.parse.quote(key, safe='/')}"
                        del_req = urllib.request.Request(del_url, headers={"Authorization": f"Bearer {token}"}, method="DELETE")
                        try:
                            with urllib.request.urlopen(del_req, timeout=20) as d_resp:
                                if d_resp.status in (200, 204):
                                    total_deleted += 1
                                else:
                                    total_failed += 1
                        except urllib.error.HTTPError as he:
                            if he.code == 404:
                                total_deleted += 1
                            else:
                                total_failed += 1
                        except Exception:
                            total_failed += 1
                cursor = data.get("result_info", {}).get("cursor")
                if not cursor:
                    break
        except Exception as exc:
            logger.warning("Backstop R2 scan for org %s failed: %s", org_id, exc)

    return {
        "deleted": total_deleted,
        "failed": total_failed,
        "error": "; ".join(errors) if errors else None,
    }


def organization_id_from_prefix(prefix: str) -> int | None:
    """The workspace a bucket prefix belongs to, or None if it names no workspace.

    Supports:
    - New structure: 'tenants/<Tenant-Slug>-<org_id>'
    - Legacy structure: 'org-<id>'
    """
    if prefix.startswith("tenants/"):
        tenant_part = prefix.removeprefix("tenants/")
        if "-" in tenant_part:
            try:
                return int(tenant_part.rsplit("-", 1)[1])
            except ValueError:
                pass
        return None
    if prefix.startswith("org-"):
        try:
            return int(prefix.removeprefix("org-"))
        except ValueError:
            return None
    return None
