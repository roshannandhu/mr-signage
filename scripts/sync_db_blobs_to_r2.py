"""Sync media files stored in the Postgres `media_blob` table to Cloudflare R2 object storage.

Usage:
    # Dry run (checks connection and lists files without writing):
    python scripts/sync_db_blobs_to_r2.py --dry-run

    # Live sync:
    python scripts/sync_db_blobs_to_r2.py

Credentials:
    Reads S3_ENDPOINT_URL, S3_BUCKET_NAME, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY,
    and AWS_REGION from backend/.env or the system environment.
"""
import argparse
import os
import pathlib
import sys
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError
from dotenv import load_dotenv
import sqlalchemy

# Load backend environment
root_dir = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root_dir))
env_path = root_dir / "backend" / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

from backend import database


def get_s3_client():
    endpoint_url = os.getenv("S3_ENDPOINT_URL")
    bucket_name = os.getenv("S3_BUCKET_NAME", "olrac")
    key_id = os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("AWS_SECRET_ACCESS_KEY")
    region = os.getenv("AWS_REGION", "auto")

    if not endpoint_url or not key_id or not secret_key or key_id.lower() == "mock":
        sys.exit(
            "[ERROR] Cloudflare R2 credentials are not configured in environment.\n"
            "Please set AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, and S3_ENDPOINT_URL."
        )

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=Config(signature_version="s3v4"),
    ), bucket_name


def main():
    parser = argparse.ArgumentParser(description="Sync media blobs from Postgres to Cloudflare R2")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and report without uploading")
    parser.add_argument("--force", action="store_true", help="Overwrite existing objects in R2")
    args = parser.parse_args()

    print("=== MR Signage Media Blob -> Cloudflare R2 Sync ===")
    if args.dry_run:
        print("[MODE] DRY-RUN (No files will be modified in R2)\n")

    # 1. Connect to Database
    print("[1/3] Reading media files from Supabase database...")
    db = database.SessionLocal()
    try:
        rows = db.execute(
            sqlalchemy.text(
                "SELECT key, content_type, data, size_bytes FROM media_blob ORDER BY key"
            )
        ).fetchall()
    except Exception as exc:
        sys.exit(f"[ERROR] Could not query media_blob table: {exc}")
    finally:
        db.close()

    total_files = len(rows)
    total_bytes = sum(len(r[2]) if r[2] else (r[3] or 0) for r in rows)
    print(f"      Found {total_files} media files ({total_bytes / (1024 * 1024):.2f} MB) in database.\n")

    if total_files == 0:
        print("No media files to sync. Done.")
        return

    # 2. Connect to R2
    print("[2/3] Connecting to Cloudflare R2...")
    client, bucket_name = get_s3_client()
    try:
        client.list_objects_v2(Bucket=bucket_name, MaxKeys=1)
        print(f"      Connected successfully to bucket '{bucket_name}'.\n")
    except ClientError as exc:
        sys.exit(f"[ERROR] Cannot reach bucket '{bucket_name}': {exc}")

    # 3. Process each file
    print("[3/3] Syncing files...")
    uploaded = 0
    skipped = 0
    failed = 0

    for idx, (key, content_type, data, size_bytes) in enumerate(rows, 1):
        payload = bytes(data) if data else b""
        content_type = content_type or "application/octet-stream"
        file_size = len(payload)

        # Check if already exists in bucket
        exists = False
        if not args.force:
            try:
                head = client.head_object(Bucket=bucket_name, Key=key)
                if head.get("ContentLength") == file_size:
                    exists = True
            except ClientError:
                exists = False

        if exists:
            skipped += 1
            print(f"[{idx}/{total_files}] [EXISTS] {key} ({file_size} bytes)")
            continue

        if args.dry_run:
            uploaded += 1
            print(f"[{idx}/{total_files}] [DRY-RUN WOULD UPLOAD] {key} ({file_size} bytes, {content_type})")
            continue

        try:
            client.put_object(
                Bucket=bucket_name,
                Key=key,
                Body=payload,
                ContentType=content_type,
            )
            uploaded += 1
            print(f"[{idx}/{total_files}] [UPLOADED] {key} ({file_size} bytes)")
        except Exception as exc:
            failed += 1
            print(f"[{idx}/{total_files}] [FAILED] {key}: {exc}")

    print("\n=== Sync Summary ===")
    print(f"Total files in DB: {total_files}")
    print(f"Uploaded:          {uploaded}")
    print(f"Already in R2:     {skipped}")
    print(f"Failed:            {failed}")
    print("====================")


if __name__ == "__main__":
    main()
