"""Purging one workspace must never touch another workspace's objects.

`delete_prefix` listed by a bare string prefix, so purging workspace 3 ("org-3") also matched
"org-30/..." and "org-31/...", and "tenants/Acme-3" matched "tenants/Acme-30/...". It now
matches whole folders. Runs against a fake bucket; pure, no database.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

# Imported first: backend.services pulls in the routers, which import each other in a
# circle unless backend.main is what starts the chain (see test_subscription_expiry).
import backend.main  # noqa: E402,F401

BUCKET = "olrac-purge-prefix-test"

_LEAKY_ENV = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "S3_ENDPOINT_URL",
    "S3_BUCKET_NAME",
)


@pytest.fixture
def bucket():
    moto = pytest.importorskip("moto", reason="moto is required for the object-storage checks")
    import boto3

    saved = {name: os.environ.get(name) for name in _LEAKY_ENV}
    os.environ.update(
        AWS_ACCESS_KEY_ID="testing",
        AWS_SECRET_ACCESS_KEY="testing",
        AWS_REGION="us-east-1",
        S3_ENDPOINT_URL="https://s3.us-east-1.amazonaws.com",
        S3_BUCKET_NAME=BUCKET,
    )
    try:
        with moto.mock_aws():
            client = boto3.client("s3", region_name="us-east-1")
            client.create_bucket(Bucket=BUCKET)
            for key in (
                "org-3/a.jpg",
                "org-3/sub/b.mp4",
                "org-30/c.jpg",
                "org-31/d.jpg",
                "tenants/Acme-3/e.jpg",
                "tenants/Acme-3/sub/f.jpg",
                "tenants/Acme-30/g.jpg",
                "tenants/Acme-3rd-Shop-7/h.jpg",
            ):
                client.put_object(Bucket=BUCKET, Key=key, Body=b"x")
            yield client
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _keys(client):
    pages = client.get_paginator("list_objects_v2").paginate(Bucket=BUCKET)
    return sorted(entry["Key"] for page in pages for entry in page.get("Contents", []))


def test_purging_org_3_leaves_org_30_and_31_alone(bucket):
    from backend.services.storage_service import delete_prefix

    result = delete_prefix("org-3")

    assert result == {"deleted": 2, "failed": 0, "error": None}
    remaining = _keys(bucket)
    assert "org-30/c.jpg" in remaining and "org-31/d.jpg" in remaining
    assert not [key for key in remaining if key.startswith("org-3/")]


def test_a_tenant_slug_prefix_matches_only_its_own_folder(bucket):
    from backend.services.storage_service import delete_prefix

    result = delete_prefix("tenants/Acme-3")

    assert result == {"deleted": 2, "failed": 0, "error": None}
    remaining = _keys(bucket)
    assert "tenants/Acme-30/g.jpg" in remaining
    assert "tenants/Acme-3rd-Shop-7/h.jpg" in remaining
    assert not [key for key in remaining if key.startswith("tenants/Acme-3/")]


def test_a_trailing_slash_is_accepted_and_changes_nothing(bucket):
    from backend.services.storage_service import delete_prefix

    assert delete_prefix("org-3/")["deleted"] == 2
    assert "org-30/c.jpg" in _keys(bucket)


@pytest.mark.parametrize("prefix", ["", "   ", "/", "//"])
def test_an_empty_prefix_is_still_refused(prefix):
    from backend.services.storage_service import delete_prefix

    with pytest.raises(ValueError):
        delete_prefix(prefix)
