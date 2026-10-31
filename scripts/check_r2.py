"""Prove an R2 token works before a deploy depends on it.

    S3_ENDPOINT_URL=https://<account>.r2.cloudflarestorage.com \
    S3_BUCKET_NAME=olrac-media AWS_REGION=auto \
    AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... \
    python scripts/check_r2.py

Exercises the same four operations the app does -- put, presign, fetch, delete -- because
a token with the wrong permission scope passes a bare connection check and then fails on
the first upload, surfacing as a 500 with no explanation. Credentials are read from the
environment and never printed.
"""
import os
import sys
import urllib.request

import boto3

REQUIRED = ("S3_ENDPOINT_URL", "S3_BUCKET_NAME", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")
missing = [name for name in REQUIRED if not os.getenv(name)]
if missing:
    sys.exit(f"Not set: {', '.join(missing)}")

bucket = os.environ["S3_BUCKET_NAME"]
key = "mr-preflight-check.txt"
payload = b"mr r2 preflight"

client = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT_URL"],
    aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
    aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
    region_name=os.getenv("AWS_REGION", "auto"),
)

try:
    client.head_bucket(Bucket=bucket)
    print(f"[ok]   bucket '{bucket}' is reachable")
except Exception as exc:
    sys.exit(f"[FAIL] cannot reach bucket '{bucket}': {exc}\n"
             "       Check S3_BUCKET_NAME matches the bucket, and the token is scoped to it.")

try:
    client.put_object(Bucket=bucket, Key=key, Body=payload, ContentType="text/plain")
    print("[ok]   upload succeeded (token has write)")
except Exception as exc:
    sys.exit(f"[FAIL] upload refused: {exc}\n       Token needs Object Read & Write.")

# The exact call the app makes to hand a TV a downloadable link.
url = client.generate_presigned_url(
    "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=3600
)
try:
    with urllib.request.urlopen(url, timeout=20) as response:
        fetched = response.read()
    assert fetched == payload, "presigned URL served unexpected bytes"
    print("[ok]   presigned URL downloads (this is how a TV fetches media)")
except Exception as exc:
    sys.exit(f"[FAIL] presigned URL not fetchable: {exc}")

try:
    client.delete_object(Bucket=bucket, Key=key)
    print("[ok]   delete succeeded (retention jobs can reclaim space)")
except Exception as exc:
    sys.exit(f"[FAIL] delete refused: {exc}")

print("\nR2 is correctly configured. Use these same values in Render.")
