import os
import sys
import json
import urllib.request
import pathlib
import mimetypes
import re

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv("backend/.env")

from backend.database import SessionLocal, engine
from backend import models
from backend.media_urls import (
    tenant_storage_root,
    client_ads_prefix,
    general_media_prefix,
    branding_storage_prefix,
    mint_media_filename,
    sanitize_storage_name,
)
import sqlalchemy

ACCOUNT_ID = "3fe4487a2b8fd1e2e541bf0e0f4c7c42"
BUCKET = "olrac"

def get_token():
    p = pathlib.Path(os.environ["APPDATA"]) / "xdg.config" / ".wrangler" / "config" / "default.toml"
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            if "oauth_token" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None

def cf_api_get_object(token, key):
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/r2/buckets/{BUCKET}/objects/{key}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req) as resp:
        return resp.read()

def cf_api_put_object(token, key, data, content_type="application/octet-stream"):
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/r2/buckets/{BUCKET}/objects/{key}"
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
        method="PUT"
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def cf_api_delete_object(token, key):
    url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/r2/buckets/{BUCKET}/objects/{key}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"}, method="DELETE")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode())

def cf_api_list_objects(token):
    all_objects = []
    cursor = None
    while True:
        url = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/r2/buckets/{BUCKET}/objects?per_page=100"
        if cursor:
            url += f"&cursor={cursor}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
        items = data.get("result", [])
        all_objects.extend(items)
        info = data.get("result_info", {})
        if info.get("is_truncated") and info.get("cursor"):
            cursor = info["cursor"]
        else:
            break
    return {o["key"]: o for o in all_objects}

def save_to_media_blob(conn, key, data, ctype):
    conn.execute(
        sqlalchemy.text("""
            INSERT INTO media_blob (key, content_type, data, size_bytes, created_at)
            VALUES (:k, :ct, :d, :sz, NOW())
            ON CONFLICT (key) DO UPDATE SET content_type = :ct, data = :d, size_bytes = :sz
        """),
        {"k": key, "ct": ctype, "d": data, "sz": len(data)}
    )

def execute_migration():
    token = get_token()
    if not token:
        print("ERROR: Could not get Cloudflare Wrangler token!")
        return False

    db = SessionLocal()
    r2_objects = cf_api_list_objects(token)
    print(f"Initial Cloudflare R2 objects: {len(r2_objects)}")

    conn = engine.connect()

    migrated_keys = []
    old_keys_to_delete = set()

    try:
        # 1. Migrate Org 68 Logo
        org68 = db.query(models.Organization).filter(models.Organization.id == 68).first()
        if org68 and org68.logo_url:
            old_key = org68.logo_url.removeprefix("s3://").lstrip("/")
            folder = branding_storage_prefix(org68)
            ext = os.path.splitext(old_key)[1] or ".png"
            stem = os.path.splitext(os.path.basename(old_key))[0]
            new_filename = mint_media_filename("logo", ext, stem)
            new_key = f"{folder}/{new_filename}"

            print(f"\n[Migrating Org 68 Logo] {old_key} -> {new_key}")
            if old_key in r2_objects:
                data = cf_api_get_object(token, old_key)
                cf_api_put_object(token, new_key, data, "image/png")
                old_keys_to_delete.add(old_key)
                org68.logo_url = f"s3://{new_key}"
                save_to_media_blob(conn, new_key, data, "image/png")
                migrated_keys.append((old_key, new_key))
                print(f"  SUCCESS: Org 68 Logo migrated ({len(data)} bytes)")

        # 2. Org 67 Logo cleanup
        org67 = db.query(models.Organization).filter(models.Organization.id == 67).first()
        if org67 and org67.logo_url and not os.path.exists(org67.logo_url.removeprefix("/uploads/")):
            print(f"\n[Org 67 Logo] Cleaning stale nonexistent logo: {org67.logo_url}")
            org67.logo_url = None

        # 3. Content rows
        contents = db.query(models.Content).all()
        for cnt in contents:
            org = db.query(models.Organization).filter(models.Organization.id == cnt.organization_id).first()
            placement = db.query(models.AdPlacement).filter(models.AdPlacement.content_id == cnt.id).first()
            client = db.query(models.Client).filter(models.Client.id == placement.client_id).first() if placement and placement.client_id else None

            folder = client_ads_prefix(org, client) if client else general_media_prefix(org)

            raw_key = cnt.file_url.removeprefix("s3://").lstrip("/") if cnt.file_url else ""
            ext = os.path.splitext(raw_key)[1] or (".mp4" if cnt.type == "video" else ".jpg")
            stem = os.path.splitext(os.path.basename(raw_key))[0]
            base_filename = mint_media_filename(cnt.name, ext, stem)
            base_stem = os.path.splitext(base_filename)[0]

            new_master_key = f"{folder}/{base_filename}"
            print(f"\n[Migrating Content {cnt.id} ({cnt.type})] {raw_key} -> {new_master_key}")

            # Master data
            master_data = None
            if raw_key in r2_objects:
                master_data = cf_api_get_object(token, raw_key)
                old_keys_to_delete.add(raw_key)
            else:
                blob_row = conn.execute(sqlalchemy.text("SELECT data FROM media_blob WHERE key = :k"), {"k": raw_key}).fetchone()
                if blob_row:
                    master_data = bytes(blob_row[0])
                    print(f"  (Loaded {len(master_data)} bytes from media_blob)")

            if master_data:
                ctype = mimetypes.guess_type(new_master_key)[0] or ("video/mp4" if cnt.type == "video" else "image/png")
                cf_api_put_object(token, new_master_key, master_data, ctype)
                cnt.file_url = f"s3://{new_master_key}"
                save_to_media_blob(conn, new_master_key, master_data, ctype)
                migrated_keys.append((raw_key, new_master_key))
                print(f"  SUCCESS: Content {cnt.id} master uploaded ({len(master_data)} bytes)")
            else:
                print(f"  WARNING: Could not find data for {raw_key}")

            # Thumbnail
            if cnt.thumbnail:
                old_thumb_key = cnt.thumbnail.removeprefix("s3://").lstrip("/")
                if cnt.type == "video":
                    thumb_ext = os.path.splitext(old_thumb_key)[1] or ".jpg"
                    new_thumb_key = f"{folder}/{base_stem}-thumbnail{thumb_ext}"
                    thumb_data = None
                    if old_thumb_key in r2_objects:
                        thumb_data = cf_api_get_object(token, old_thumb_key)
                        old_keys_to_delete.add(old_thumb_key)
                    else:
                        blob_row = conn.execute(sqlalchemy.text("SELECT data FROM media_blob WHERE key = :k"), {"k": old_thumb_key}).fetchone()
                        if blob_row:
                            thumb_data = bytes(blob_row[0])
                    if thumb_data:
                        cf_api_put_object(token, new_thumb_key, thumb_data, "image/jpeg")
                        cnt.thumbnail = f"s3://{new_thumb_key}"
                        save_to_media_blob(conn, new_thumb_key, thumb_data, "image/jpeg")
                        migrated_keys.append((old_thumb_key, new_thumb_key))
                        print(f"  SUCCESS: Content {cnt.id} thumbnail uploaded ({len(thumb_data)} bytes)")
                else:
                    cnt.thumbnail = f"s3://{new_master_key}"

            # Renditions
            rends = db.query(models.MediaRendition).filter(models.MediaRendition.content_id == cnt.id).all()
            for r in rends:
                if r.file_url:
                    old_r_key = r.file_url.removeprefix("s3://").lstrip("/")
                    new_r_key = f"{folder}/{base_stem}_{r.resolution}.mp4"
                    r_data = None
                    if old_r_key in r2_objects:
                        r_data = cf_api_get_object(token, old_r_key)
                        old_keys_to_delete.add(old_r_key)
                    else:
                        blob_row = conn.execute(sqlalchemy.text("SELECT data FROM media_blob WHERE key = :k"), {"k": old_r_key}).fetchone()
                        if blob_row:
                            r_data = bytes(blob_row[0])
                    if r_data:
                        cf_api_put_object(token, new_r_key, r_data, "video/mp4")
                        r.file_url = f"s3://{new_r_key}"
                        save_to_media_blob(conn, new_r_key, r_data, "video/mp4")
                        migrated_keys.append((old_r_key, new_r_key))
                        print(f"  SUCCESS: Rendition {r.resolution} uploaded ({len(r_data)} bytes)")

        # Commit DB changes
        db.commit()
        conn.commit()
        print("\nSUCCESS: All Supabase DB rows committed!")

        # 4. Verification of newly uploaded objects
        new_r2_objects = cf_api_list_objects(token)
        print(f"\nVerifying in Cloudflare R2: now {len(new_r2_objects)} objects present")
        all_ok = True
        for old_k, new_k in migrated_keys:
            if new_k not in new_r2_objects:
                print(f"  ERROR: {new_k} is MISSING from R2!")
                all_ok = False
            else:
                print(f"  VERIFIED: {new_k} exists ({new_r2_objects[new_k].get('size')} bytes)")

        # 5. Delete old keys from R2 and media_blob
        if all_ok:
            print(f"\nCleaning up {len(old_keys_to_delete)} old objects from Cloudflare R2 and media_blob...")
            for old_k in sorted(old_keys_to_delete):
                cf_api_delete_object(token, old_k)
                conn.execute(sqlalchemy.text("DELETE FROM media_blob WHERE key = :k"), {"k": old_k})
                print(f"  DELETED old key: {old_k}")
            conn.commit()

            final_r2 = cf_api_list_objects(token)
            print(f"\nFinal Cloudflare R2 Objects Count: {len(final_r2)}")
            print("Current R2 Objects:")
            for k in sorted(final_r2):
                print(f"  - {k} ({final_r2[k].get('size')} bytes)")
        else:
            print("\nWARNING: Skipping deletion of old keys because verification found missing objects!")

    finally:
        conn.close()
        db.close()

if __name__ == "__main__":
    from backend.database import SQLALCHEMY_DATABASE_URL
    from backend.script_guard import confirm_destructive

    confirm_destructive("MOVE objects in the storage bucket and rewrite file URLs in the database", SQLALCHEMY_DATABASE_URL)
    execute_migration()
