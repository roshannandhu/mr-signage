"""Audit and remove unwanted / orphaned objects from Cloudflare R2 bucket.

Usage:
    # Dry run (default: inspect and display without deleting):
    python scripts/cleanup_r2_orphans.py

    # Apply cleanup (deletes only orphaned and unwanted objects):
    python scripts/cleanup_r2_orphans.py --apply
"""
import argparse
import datetime
import json
import os
import pathlib
import sys
import urllib.request
import urllib.error

root_dir = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root_dir))

from backend import database, models
from _referenced_files import extra_stored_urls

# Anything newer than this is skipped: an upload writes the object first and the database
# row a moment later, so a fresh object with no row yet is not an orphan.
MIN_AGE = datetime.timedelta(hours=1)


def get_cloudflare_oauth_token() -> str:
    """Read active Wrangler OAuth token."""
    appdata = os.environ.get("APPDATA")
    candidates = []
    if appdata:
        candidates.append(pathlib.Path(appdata) / "xdg.config" / ".wrangler" / "config" / "default.toml")
    candidates.append(pathlib.Path.home() / ".config" / ".wrangler" / "config" / "default.toml")

    for path in candidates:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("oauth_token"):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit("[ERROR] Could not find Wrangler OAuth credentials. Run 'npx wrangler login' first.")


def get_active_db_keys() -> set[str]:
    """Extract every legitimate file key currently referenced in Supabase PostgreSQL."""
    db = database.SessionLocal()
    keys = set()

    def add(val: str | None):
        if not val:
            return
        cleaned = val.strip()
        if cleaned.startswith("s3://"):
            keys.add(cleaned.removeprefix("s3://").lstrip("/"))
        elif "/uploads/" in cleaned:
            keys.add(cleaned.split("/uploads/", 1)[1].lstrip("/"))

    try:
        for c in db.query(models.Content).all():
            add(c.file_url)
            add(c.thumbnail)
        for r in db.query(models.MediaRendition).all():
            add(r.file_url)
        # Workspace logos, the demo video and every release APK, which the loops above miss.
        for stored_url in extra_stored_urls(db):
            add(stored_url)
    finally:
        db.close()

    return keys


def _too_new_to_judge(obj: dict) -> bool:
    """True for an object modified within MIN_AGE, or whose age cannot be read."""
    try:
        modified = datetime.datetime.fromisoformat(str(obj.get("last_modified")).replace("Z", "+00:00"))
    except ValueError:
        return True
    if modified.tzinfo is None:
        modified = modified.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc) - modified < MIN_AGE


def list_r2_objects(account_id: str, bucket_name: str, token: str) -> list[dict]:
    """Fetch all objects currently in Cloudflare R2 bucket with pagination."""
    objects = []
    cursor = None
    while True:
        url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket_name}/objects?per_page=100"
        if cursor:
            url += f"&cursor={cursor}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("result", [])
            objects.extend(items)
            info = data.get("result_info", {})
            if info.get("is_truncated") and info.get("cursor"):
                cursor = info["cursor"]
            else:
                break
    return objects


def delete_r2_object(account_id: str, bucket_name: str, key: str, token: str) -> bool:
    """Delete a single object from R2 via Cloudflare REST API."""
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/r2/buckets/{bucket_name}/objects/{key}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return bool(data.get("success"))
    except urllib.error.HTTPError as exc:
        print(f"      HTTP {exc.code} while deleting '{key}': {exc.reason}", flush=True)
        return False
    except Exception as exc:
        print(f"      Error deleting '{key}': {exc}", flush=True)
        return False


def main():
    parser = argparse.ArgumentParser(description="Audit and remove unwanted/orphaned files in Cloudflare R2")
    parser.add_argument("--apply", action="store_true", help="Perform actual deletion of unwanted files")
    parser.add_argument("--account-id", default="3fe4487a2b8fd1e2e541bf0e0f4c7c42", help="Cloudflare Account ID")
    parser.add_argument("--bucket", default="olrac", help="R2 Bucket Name")
    args = parser.parse_args()

    print("=== Cloudflare R2 Orphan & Unwanted File Cleanup ===")
    token = get_cloudflare_oauth_token()

    # 1. List R2 objects FIRST. The database is read after this, so a file uploaded while
    #    the script runs is either absent from the listing or already referenced -- never
    #    listed as an orphan merely because its row did not exist yet when the database was read.
    print(f"[1/3] Listing objects in Cloudflare R2 bucket '{args.bucket}'...")
    all_objects = list_r2_objects(args.account_id, args.bucket, token)
    print(f"      Found {len(all_objects)} total objects in bucket '{args.bucket}'.\n")

    # 2. Fetch DB keys
    print("[2/3] Querying active database references from Supabase...")
    active_keys = get_active_db_keys()
    print(f"      Found {len(active_keys)} legitimate active media keys in database.\n")

    # 3. Categorize
    protected = []
    unwanted = []

    for obj in all_objects:
        key = obj["key"]
        normalized = key.lstrip("/")
        if key in active_keys or normalized in active_keys or _too_new_to_judge(obj):
            protected.append(obj)
        else:
            unwanted.append(obj)

    print("[3/3] Audit Results:")
    print(f"      [PROTECTED] Active files in use: {len(protected)}")
    print(f"      [UNWANTED]  Orphaned / Test files: {len(unwanted)}")

    unwanted_bytes = sum(u.get("size", 0) for u in unwanted)
    print(f"      Total space to reclaim: {unwanted_bytes / (1024 * 1024):.2f} MB\n")

    if not unwanted:
        print("No unwanted or orphaned files found in R2. Bucket is completely clean!")
        return

    print("--- Unwanted Files Identified for Removal ---")
    for idx, u in enumerate(unwanted, 1):
        print(f"  {idx:2d}. {u['key']:<60} ({u.get('size', 0):>8} bytes, {u.get('last_modified', '')})")
    print("---------------------------------------------")

    if not args.apply:
        print("\n[INFO] Dry-run completed. No files were deleted.")
        print("To permanently delete these unwanted files, re-run with: python scripts/cleanup_r2_orphans.py --apply")
        return

    from backend.database import SQLALCHEMY_DATABASE_URL
    from backend.script_guard import confirm_destructive

    confirm_destructive("DELETE bucket objects that no database row references", SQLALCHEMY_DATABASE_URL)

    print("\n[ACTION] Proceeding with deletion of unwanted files...")
    deleted_count = 0
    failed_count = 0

    for idx, u in enumerate(unwanted, 1):
        key = u["key"]
        print(f"  Deleting [{idx}/{len(unwanted)}] {key} ...", end=" ", flush=True)
        if delete_r2_object(args.account_id, args.bucket, key, token):
            deleted_count += 1
            print("[DELETED]", flush=True)
        else:
            failed_count += 1
            print("[FAILED]", flush=True)

    print("\n=== Cleanup Summary ===")
    print(f"Total processed: {len(unwanted)}")
    print(f"Deleted:         {deleted_count}")
    print(f"Failed:          {failed_count}")
    print(f"Reclaimed:       {unwanted_bytes / (1024 * 1024):.2f} MB")
    print(f"Protected retained in R2: {len(protected)}")
    print("========================")


if __name__ == "__main__":
    main()
