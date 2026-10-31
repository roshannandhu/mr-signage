"""Media storage: fetch and store, on local disk and on object storage.

The transcoder refused outright on any `s3://` source, so with R2 configured -- the
deployment the README documents -- every video upload ended `status="failed"` and no
rendition was ever produced. These checks pin the fetch/store pair that replaced the
refusal, on both backends, without needing ffmpeg or a database.

Run directly:  python tests/test_media_storage.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from backend import media_storage  # noqa: E402

BUCKET = "olrac-test-bucket"

# Process globals this file reassigns. Restored after every test.
_LEAKY_ENV = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "S3_ENDPOINT_URL",
    "S3_BUCKET_NAME",
)


@pytest.fixture(autouse=True)
def _restore_process_globals():
    """Undo every module-level and environment mutation this file makes.

    `local_mode` points `media_storage.UPLOAD_DIR` at a `TemporaryDirectory` that is
    deleted when its `with` block ends, and nothing put the original back. pytest imports
    this module and `test_media_worker.py` into the SAME process, and this file sorts
    first, so the worker there resolved every media path under a directory that no longer
    existed, failed with FileNotFoundError, and recorded the upload as `failed`. Both files
    passed alone; the suite was red. The `s3` fixture below leaked the same way, restoring
    only AWS_ACCESS_KEY_ID and leaving the bucket name behind.

    Restoring here rather than at each call site covers both, and any future test in this
    file that reaches for a global without thinking about the one after it.
    """
    saved_upload_dir = media_storage.UPLOAD_DIR
    saved_env = {name: os.environ.get(name) for name in _LEAKY_ENV}
    try:
        yield
    finally:
        media_storage.UPLOAD_DIR = saved_upload_dir
        for name, value in saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def local_mode(tmp):
    """Local disk: AWS_ACCESS_KEY_ID unset or the literal 'mock'."""
    os.environ["AWS_ACCESS_KEY_ID"] = "mock"
    media_storage.UPLOAD_DIR = tmp


def test_storage_key_for_understands_both_schemes():
    assert media_storage.storage_key_for("s3://7/ad.mp4") == "7/ad.mp4"
    assert media_storage.storage_key_for("/uploads/7/ad.mp4") == "7/ad.mp4"
    # A resolved absolute URL still yields the key; the row may hold either form.
    assert media_storage.storage_key_for("http://host:8000/uploads/7/ad.mp4") == "7/ad.mp4"


def test_storage_key_for_rejects_nonsense():
    with pytest.raises(ValueError):
        media_storage.storage_key_for("gopher://7/ad.mp4")


def test_is_remote():
    assert media_storage.is_remote("s3://7/ad.mp4")
    assert not media_storage.is_remote("/uploads/7/ad.mp4")


def test_local_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        local_mode(tmp)
        source = Path(tmp) / "7" / "ad.mp4"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"video-bytes")

        with tempfile.TemporaryDirectory() as work:
            fetched = media_storage.fetch_to("/uploads/7/ad.mp4", Path(work) / "ad.mp4")
            assert fetched.read_bytes() == b"video-bytes"
            # Copied, not moved: the original must survive a failed transcode.
            assert source.exists()

            rendition = Path(work) / "ad_720p.mp4"
            rendition.write_bytes(b"smaller")
            stored = media_storage.store(rendition, "7/ad_720p.mp4")

        assert stored == "/uploads/7/ad_720p.mp4", (
            "the stored location must match what a direct upload would have written, or "
            "resolve_media_url cannot turn it into something a TV can fetch"
        )
        assert (Path(tmp) / "7" / "ad_720p.mp4").read_bytes() == b"smaller"


def test_local_fetch_reports_a_missing_file():
    with tempfile.TemporaryDirectory() as tmp:
        local_mode(tmp)
        with tempfile.TemporaryDirectory() as work:
            with pytest.raises(FileNotFoundError):
                media_storage.fetch_to("/uploads/7/gone.mp4", Path(work) / "gone.mp4")


def test_local_delete():
    with tempfile.TemporaryDirectory() as tmp:
        local_mode(tmp)
        target = Path(tmp) / "7" / "shot.jpg"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"jpg")
        assert media_storage.delete("/uploads/7/shot.jpg") is True
        assert not target.exists()
        # Already gone is not an error; the caller is pruning best-effort.
        assert media_storage.delete("/uploads/7/shot.jpg") is False


@pytest.fixture
def s3():
    """A fake S3, with the environment the code reads to decide it is enabled."""
    moto = pytest.importorskip("moto", reason="moto is required for the object-storage checks")
    import boto3

    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_REGION"] = "us-east-1"
    # Named explicitly, because moto only intercepts an AWS-shaped endpoint. This used to
    # be "" to mean "no custom endpoint", which reached boto3 as an empty endpoint_url and
    # raised ValueError -- the failure was invisible because moto was not installed, so
    # every object-storage check in this file silently skipped instead of running.
    os.environ["S3_ENDPOINT_URL"] = "https://s3.us-east-1.amazonaws.com"
    # The bucket comes from get_s3_config() alone now. media_storage used to shadow it
    # with its own module global defaulting to "olrac-media" -- a second, different
    # answer to "which bucket?" that nothing read but everything could have.
    os.environ["S3_BUCKET_NAME"] = BUCKET

    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield boto3.client("s3", region_name="us-east-1")

    os.environ["AWS_ACCESS_KEY_ID"] = "mock"


def test_the_content_type_comes_from_the_file_name_not_the_uploader(s3, tmp_path):
    """A logo sent as text/html under a .png name was served from the bucket as HTML."""
    source = tmp_path / "logo.png"
    source.write_bytes(b"not really a png")

    media_storage.store(source, "org-1/logo.png", content_type="text/html")
    assert s3.head_object(Bucket=BUCKET, Key="org-1/logo.png")["ContentType"] == "image/png"

    media_storage.store(source, "org-1/ad.mp4", content_type="text/html")
    assert s3.head_object(Bucket=BUCKET, Key="org-1/ad.mp4")["ContentType"] == "video/mp4"

    media_storage.store(source, "org-1/logo.webp", content_type="text/html")
    assert s3.head_object(Bucket=BUCKET, Key="org-1/logo.webp")["ContentType"] == "image/webp"

    # No recognisable extension: an opaque download, never whatever the uploader claimed.
    media_storage.store(source, "org-1/blob", content_type="text/html")
    assert s3.head_object(Bucket=BUCKET, Key="org-1/blob")["ContentType"] == "application/octet-stream"


def test_content_type_for_covers_what_the_app_stores():
    assert media_storage.content_type_for("a/b.jpg") == "image/jpeg"
    assert media_storage.content_type_for("a/b.JPEG") == "image/jpeg"
    assert media_storage.content_type_for("a/b.png") == "image/png"
    assert media_storage.content_type_for("a/b.webp") == "image/webp"
    assert media_storage.content_type_for("a/b.mp4") == "video/mp4"
    assert media_storage.content_type_for("a/b.html") == "text/html"  # never stored, but honest
    assert media_storage.content_type_for("a/noextension") == "application/octet-stream"


def test_s3_round_trip(s3):
    # The case that used to raise NotImplementedError before a single byte was read.
    s3.put_object(Bucket=BUCKET, Key="7/ad.mp4", Body=b"video-bytes")

    with tempfile.TemporaryDirectory() as work:
        fetched = media_storage.fetch_to("s3://7/ad.mp4", Path(work) / "ad.mp4")
        assert fetched.read_bytes() == b"video-bytes", "ffmpeg needs a real local file"

        rendition = Path(work) / "ad_720p.mp4"
        rendition.write_bytes(b"smaller")
        stored = media_storage.store(rendition, "7/ad_720p.mp4", content_type="video/mp4")

    assert stored == "s3://7/ad_720p.mp4", (
        "a rendition must be stored back on the same backend as its original"
    )
    body = s3.get_object(Bucket=BUCKET, Key="7/ad_720p.mp4")
    assert body["Body"].read() == b"smaller"
    assert body["ContentType"] == "video/mp4"


def test_s3_delete(s3):
    s3.put_object(Bucket=BUCKET, Key="7/shot.jpg", Body=b"jpg")
    assert media_storage.delete("s3://7/shot.jpg") is True
    remaining = s3.list_objects_v2(Bucket=BUCKET).get("Contents", [])
    assert not any(o["Key"] == "7/shot.jpg" for o in remaining), (
        "screenshot pruning went through a local-only helper, so with R2 configured every "
        "pruned row left its object in the bucket forever"
    )


def test_s3_fetch_creates_missing_parent_directories(s3):
    s3.put_object(Bucket=BUCKET, Key="7/ad.mp4", Body=b"x")
    with tempfile.TemporaryDirectory() as work:
        nested = Path(work) / "does" / "not" / "exist" / "ad.mp4"
        assert media_storage.fetch_to("s3://7/ad.mp4", nested).exists()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


# --- read: what the server-side renderers use -----------------------------------------
#
# The PDF reports fetched their own public URL over HTTP for every image on the page. That
# needed the deployment to be reachable from inside its own container, cost a round trip
# per image, and once media URLs became a redirect it cost two -- all to reach bytes this
# process can read directly with the credentials it already wrote them with.


def test_read_local_file():
    with tempfile.TemporaryDirectory() as tmp:
        local_mode(tmp)
        source = Path(tmp) / "org-7" / "logo.png"
        source.parent.mkdir(parents=True)
        source.write_bytes(b"png-bytes")

        assert media_storage.read("/uploads/org-7/logo.png") == b"png-bytes"
        # The absolute form resolve_media_url hands out must work too -- that is what the
        # report actually receives.
        assert media_storage.read("https://api.example.com/uploads/org-7/logo.png") == b"png-bytes"
        assert media_storage.read("/uploads/org-7/missing.png") is None


def test_read_refuses_to_climb_out_of_the_uploads_root():
    with tempfile.TemporaryDirectory() as tmp:
        local_mode(tmp)
        secret = Path(tmp).parent / "outside.txt"
        secret.write_bytes(b"not yours")
        try:
            assert media_storage.read(f"/uploads/../{secret.name}") is None
        finally:
            secret.unlink()


def test_read_object_storage_by_key_and_by_public_url(s3):
    s3.put_object(Bucket=BUCKET, Key="org-7/creative.jpg", Body=b"jpeg-bytes")

    assert media_storage.read("s3://org-7/creative.jpg") == b"jpeg-bytes"
    # The public URL resolve_media_url builds is what reaches the report, so read has to
    # accept it without going near the network.
    from backend.media_urls import resolve_media_url

    assert media_storage.read(resolve_media_url("s3://org-7/creative.jpg")) == b"jpeg-bytes"
    assert media_storage.read("s3://org-7/gone.jpg") is None


def test_read_declines_anything_that_is_not_ours():
    """So _fetch_image can tell "not mine, try HTTP" from "mine, and missing"."""
    for foreign in ("https://cdn.example.com/logo.png", "https://example.com", "gopher://x", "", None):
        assert media_storage.read(foreign) is None


def test_the_report_reads_the_creative_without_any_http(s3, monkeypatch):
    """The whole point: a report renders its images with the network unavailable."""
    pytest.importorskip("reportlab", reason="the PDF reports need reportlab")
    from backend.media_urls import resolve_media_url
    from backend.reports import booking_report

    s3.put_object(Bucket=BUCKET, Key="org-7/creative.jpg", Body=b"jpeg-bytes")

    import urllib.request

    def explode(*_args, **_kwargs):
        raise AssertionError("the report went to the network for an image it already owns")

    monkeypatch.setattr(urllib.request, "urlopen", explode)

    assert booking_report._fetch_image(resolve_media_url("s3://org-7/creative.jpg")) == b"jpeg-bytes"
    # A genuinely external image is still allowed to try HTTP -- and still fails soft.
    assert booking_report._fetch_image("https://cdn.example.com/logo.png") is None
