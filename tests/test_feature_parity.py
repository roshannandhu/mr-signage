"""Runnable backend parity check: python tests/test_feature_parity.py"""

import os
import sys
import tempfile
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# ignore_cleanup_errors: Windows holds the SQLite file open past interpreter
# exit, which otherwise dumps a PermissionError traceback over the test output.
TEMP_DIR = tempfile.TemporaryDirectory(prefix="olrac-feature-test-", ignore_cleanup_errors=True)
DB_PATH = Path(TEMP_DIR.name) / "feature.db"
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
test_db_name = f"olrac_test_{DB_PATH.stem.replace('-', '_')}"
try:
    conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    conn.cursor().execute(f"DROP DATABASE IF EXISTS {test_db_name}")
    conn.cursor().execute(f"CREATE DATABASE {test_db_name} OWNER olrac")
    conn.close()
except Exception as e:
    pass
os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{test_db_name}"
os.environ["SECRET_KEY"] = "feature-test-secret-key-not-for-production"
os.environ["INITIAL_ADMIN_USERNAME"] = "test-owner"
os.environ["INITIAL_ADMIN_PASSWORD"] = "test-password-123"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "mock"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "feature-webhook-secret"

from fastapi.testclient import TestClient

from backend import database, models
from backend.main import app

# Build the schema explicitly. backend.main used to do this as an import side effect,
# so importing the app silently wrote to whatever DATABASE_URL pointed at; it now runs
# in the lifespan, which a bare TestClient(app) never starts. Each isolated script owns
# its own database anyway, so creating it here is the honest version of what was
# happening implicitly before.
from backend import database as _bootstrap_db, models as _bootstrap_models
_bootstrap_models.Base.metadata.create_all(bind=_bootstrap_db.engine)
from backend.routers.auth import get_password_hash


def auth_header(client: TestClient, username: str, password: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/token",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def run() -> None:
    with TestClient(app) as client:
        assert client.post(
            "/api/auth/token", data={"username": "admin", "password": "admin"}
        ).status_code == 401
        owner = auth_header(client, "test-owner", "test-password-123")

        created_viewer = client.post(
            "/api/users/",
            headers=owner,
            json={"username": "test-viewer", "password": "viewer-password", "role": "viewer"},
        )
        assert created_viewer.status_code == 201, created_viewer.text
        viewer = auth_header(client, "test-viewer", "viewer-password")
        assert client.post("/api/playlists/", headers=viewer, json={"name": "Denied"}).status_code == 403

        db = database.SessionLocal()
        owner_user = db.query(models.User).filter(models.User.username == "test-owner").one()
        media = models.Content(
            organization_id=owner_user.organization_id,
            name="Morning welcome",
            type="image",
            file_url="http://localhost:8000/uploads/welcome.png",
            thumbnail="http://localhost:8000/uploads/welcome.png",
            tags="welcome,lobby",
            status="ready",
        )
        db.add(media)
        db.commit()
        db.refresh(media)
        owner_user_id = owner_user.id
        content_id = media.id
        db.close()

        playlist_response = client.post(
            "/api/playlists/", headers=owner, json={"name": "Lobby loop"}
        )
        assert playlist_response.status_code == 201, playlist_response.text
        assert playlist_response.json()["default_transition"] == "fade"
        assert playlist_response.json()["default_transition_ms"] == 600
        playlist_id = playlist_response.json()["id"]

        item_response = client.post(
            f"/api/playlists/{playlist_id}/items",
            headers=owner,
            json={
                "content_id": content_id,
                "duration": 12,
                "order": 0,
                "start_at": "2026-01-01T00:00:00",
                "end_at": "2027-01-01T00:00:00",
                "schedule": {
                    "days_of_week": [0, 1, 2, 3, 4],
                    "start_time": "09:00:00",
                    "end_time": "17:00:00",
                },
            },
        )
        assert item_response.status_code == 201, item_response.text
        assert item_response.json()["schedule"]["days_of_week"] == [0, 1, 2, 3, 4]
        item_id = item_response.json()["id"]

        registration = client.post("/api/screens/register", json={"device_id": "feature-tv"})
        assert registration.status_code == 200, registration.text
        screen_id = registration.json()["id"]
        heartbeat = client.post(
            "/api/screens/heartbeat",
            json={"device_id": "feature-tv", "device_version": "test"},
        )
        assert heartbeat.status_code == 200, heartbeat.text
        assert heartbeat.json()["screen_status"] == "waiting_pairing"
        waiting_sync = client.get("/api/screens/feature-tv/sync")
        assert waiting_sync.status_code == 200, waiting_sync.text
        assert waiting_sync.json()["status"] == "waiting_pairing"
        pair = client.post(
            "/api/screens/pair",
            headers=owner,
            json={"pair_code": registration.json()["pair_code"]},
        )
        assert pair.status_code == 200, pair.text

        # Signing in on the TV itself replaces the pairing round trip: no second person at
        # a dashboard, no five-minute code. It must bind the screen to the caller's own
        # organisation and leave the device able to sync straight away.
        signin_register = client.post("/api/screens/register", json={"device_id": "signin-tv"})
        assert signin_register.status_code == 200, signin_register.text
        assert signin_register.json()["status"] == "waiting_pairing"
        signed_in = client.post(
            "/api/screens/sign-in",
            json={
                "username": "test-owner",
                "password": "test-password-123",
                "device_id": "signin-tv",
                "name": "Lobby TV",
            },
        )
        assert signed_in.status_code == 200, signed_in.text
        assert signed_in.json()["name"] == "Lobby TV"
        # Pairing is instant now; see the note in test_screen_approval.py.
        assert signed_in.json()["status"] == "online"
        assert signed_in.json()["pair_code"] is None, "the claimed code must not stay redeemable"
        assert signed_in.json()["id"] in {
            screen["id"] for screen in client.get("/api/screens/", headers=owner).json()
        }, "a screen signed in on the TV must appear in that account's workspace"
        assert client.get("/api/screens/signin-tv/sync").status_code == 200

        # Signing in again on the same device is a reinstall, not a second screen.
        again = client.post(
            "/api/screens/sign-in",
            json={"username": "test-owner", "password": "test-password-123", "device_id": "signin-tv"},
        )
        assert again.status_code == 200, again.text
        assert again.json()["id"] == signed_in.json()["id"]
        assert again.json()["name"] == "Lobby TV", "an omitted name must not wipe the existing one"

        # PATCH writes only the keys it is given. The dashboard's settings dialog edits
        # the name alone, and the whole-object PUT used to send orientation=0 with it,
        # silently rotating every portrait display back to landscape on rename.
        rotated = client.patch(
            f"/api/screens/{screen_id}", headers=owner, json={"orientation": 90}
        )
        assert rotated.status_code == 200 and rotated.json()["orientation"] == 90, rotated.text
        renamed = client.patch(
            f"/api/screens/{screen_id}", headers=owner, json={"name": "Lobby TV"}
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "Lobby TV"
        assert renamed.json()["orientation"] == 90, "rename must not reset orientation"
        assert client.patch(
            f"/api/screens/{screen_id}", headers=owner, json={"orientation": 45}
        ).status_code == 422
        assert client.patch(
            f"/api/screens/{screen_id}", headers=owner, json={"target_version_code": 99999}
        ).status_code == 422, "an unknown release must not become a fleet update target"

        group_response = client.post("/api/groups/", headers=owner, json={"name": "Lobby"})
        assert group_response.status_code == 201, group_response.text
        group_id = group_response.json()["id"]
        members = client.put(
            f"/api/groups/{group_id}/screens",
            headers=owner,
            json={"screen_ids": [screen_id]},
        )
        assert members.status_code == 200 and members.json()["screen_count"] == 1, members.text
        assigned = client.post(
            f"/api/groups/{group_id}/assign/{playlist_id}", headers=owner
        )
        assert assigned.status_code == 200, assigned.text
        # Clear the auto-provisioned direct assignment so group assignment takes effect
        assert client.delete(f"/api/screens/{screen_id}/assign", headers=owner).status_code == 200

        first_sync = client.get("/api/screens/feature-tv/sync")
        assert first_sync.status_code == 200, first_sync.text
        assert first_sync.json()["sync_interval_seconds"] == 60
        marker = first_sync.json()["playlist_updated_at"]
        assert first_sync.json()["playlist"]["id"] == playlist_id
        unchanged_sync = client.get(
            "/api/screens/feature-tv/sync", params={"since": marker}
        )
        assert unchanged_sync.status_code == 204
        assert unchanged_sync.headers["x-sync-interval-seconds"] == "60"

        updated = client.put(
            f"/api/playlists/{playlist_id}/items/{item_id}",
            headers=owner,
            json={"duration": 20, "transition": "zoom", "transition_ms": 850},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["transition"] == "zoom"
        assert updated.json()["transition_ms"] == 850
        changed_sync = client.get(
            "/api/screens/feature-tv/sync", params={"since": marker}
        )
        assert changed_sync.status_code == 200, changed_sync.text
        assert changed_sync.json()["playlist"]["items"][0]["duration"] == 20
        assert changed_sync.json()["playlist"]["items"][0]["transition"] == "zoom"

        # Transitions are sold from Starter upwards, so the package decides this, not the
        # role. Prove the gate in both directions rather than only the happy path: the
        # feature was in the storefront and enforced nowhere for a long time precisely
        # because nothing ever asserted the refusal.
        refused_transitions = client.put(
            f"/api/playlists/{playlist_id}/transitions",
            headers=owner,
            json={"transition": "slide_left", "transition_ms": 700, "apply_to_all": True},
        )
        assert refused_transitions.status_code == 403, refused_transitions.text
        assert "transitions" in refused_transitions.json()["detail"]

        db = database.SessionLocal()
        business = db.query(models.Plan).filter(models.Plan.slug == "business").one()
        org_row = db.query(models.Organization).filter(models.Organization.id == 1).one()
        package_before_transitions = org_row.plan_id
        org_row.plan_id = business.id
        db.commit()
        db.close()

        applied = client.put(
            f"/api/playlists/{playlist_id}/transitions",
            headers=owner,
            json={"transition": "slide_left", "transition_ms": 700, "apply_to_all": True},
        )
        assert applied.status_code == 200, applied.text

        # Put the package back. The P6 block below shrinks the FREE plan's screen cap and
        # expects this workspace to feel it, which it cannot do while parked on Business.
        db = database.SessionLocal()
        org_row = db.query(models.Organization).filter(models.Organization.id == 1).one()
        org_row.plan_id = package_before_transitions
        db.commit()
        db.close()
        assert applied.json()["default_transition"] == "slide_left"
        assert applied.json()["items"][0]["transition"] == "slide_left"
        assert applied.json()["items"][0]["transition_ms"] == 700
        invalid_transition = client.put(
            f"/api/playlists/{playlist_id}/items/{item_id}",
            headers=owner,
            json={"transition": "fade", "transition_ms": 99},
        )
        assert invalid_transition.status_code == 422

        # P5: create a second company and probe every tenant-owned admin surface.
        db = database.SessionLocal()
        organization_b = models.Organization(name="Second Company", slug="second-company")
        db.add(organization_b)
        db.flush()
        user_b = models.User(
            organization_id=organization_b.id,
            username="second-owner",
            hashed_password=get_password_hash("second-password"),
            role="owner",
            is_active=True,
        )
        db.add(user_b)
        db.commit()
        organization_b_id = organization_b.id
        user_b_id = user_b.id
        db.close()
        owner_b = auth_header(client, "second-owner", "second-password")

        upload_a = client.post(
            "/api/content/upload",
            headers=owner,
            files={"file": ("tenant-a.png", b"tenant-a", "image/png")},
            data={"name": "Tenant A upload"},
        )
        upload_b = client.post(
            "/api/content/upload",
            headers=owner_b,
            files={"file": ("tenant-b.png", b"tenant-b", "image/png")},
            data={"name": "Tenant B upload"},
        )
        assert upload_a.status_code == 201 and upload_b.status_code == 201
        upload_a_payload, upload_b_payload = upload_a.json(), upload_b.json()
        # Asserts the PROPERTY -- each tenant's media lands in its own folder -- rather
        # than the folder's literal name. These pinned "/uploads/1/" and
        # "/uploads/<org id>/", which broke when folders were renamed after the owner's
        # address; a test that fails on a deliberate rename while still passing if two
        # tenants were given the SAME folder is checking the wrong thing.
        folder_a = upload_a_payload["file_url"].split("/uploads/", 1)[1].rsplit("/", 1)[0]
        folder_b = upload_b_payload["file_url"].split("/uploads/", 1)[1].rsplit("/", 1)[0]
        assert folder_a and folder_b, "uploads must be filed under a per-tenant folder"
        assert folder_a != folder_b, (
            f"both tenants uploaded into {folder_a!r}; one organisation's media is stored "
            "among another's"
        )
        content_b_id = upload_b_payload["id"]

        playlist_b = client.post("/api/playlists/", headers=owner_b, json={"name": "B only"})
        assert playlist_b.status_code == 201, playlist_b.text
        playlist_b_id = playlist_b.json()["id"]
        item_b = client.post(
            f"/api/playlists/{playlist_b_id}/items",
            headers=owner_b,
            json={"content_id": content_b_id, "duration": 10, "order": 0},
        )
        assert item_b.status_code == 201, item_b.text
        item_b_id = item_b.json()["id"]
        group_b = client.post("/api/groups/", headers=owner_b, json={"name": "B group"})
        assert group_b.status_code == 201, group_b.text
        group_b_id = group_b.json()["id"]
        registration_b = client.post("/api/screens/register", json={"device_id": "tenant-b-tv"})
        pair_b = client.post(
            "/api/screens/pair",
            headers=owner_b,
            json={"pair_code": registration_b.json()["pair_code"]},
        )
        assert pair_b.status_code == 200, pair_b.text
        screen_b_id = pair_b.json()["id"]
        assert client.put(
            f"/api/groups/{group_b_id}/screens",
            headers=owner_b,
            json={"screen_ids": [screen_b_id]},
        ).status_code == 200
        assert client.post(
            f"/api/groups/{group_b_id}/assign/{playlist_b_id}", headers=owner_b
        ).status_code == 200
        assert client.delete(f"/api/screens/{screen_b_id}/assign", headers=owner_b).status_code == 200

        assert {entry["id"] for entry in client.get("/api/users/", headers=owner).json()} == {
            created_viewer.json()["id"],
            owner_user_id,
        }
        assert content_b_id not in {entry["id"] for entry in client.get("/api/content/", headers=owner).json()}
        assert playlist_b_id not in {entry["id"] for entry in client.get("/api/playlists/", headers=owner).json()}
        assert group_b_id not in {entry["id"] for entry in client.get("/api/groups/", headers=owner).json()}
        assert screen_b_id not in {entry["id"] for entry in client.get("/api/screens/", headers=owner).json()}

        cross_tenant_requests = [
            client.put(f"/api/users/{user_b_id}", headers=owner, json={"role": "viewer"}),
            client.delete(f"/api/users/{user_b_id}", headers=owner),
            client.put(f"/api/content/{content_b_id}", headers=owner, json={"name": "probe", "tags": None}),
            client.delete(f"/api/content/{content_b_id}", headers=owner),
            client.get(f"/api/playlists/{playlist_b_id}", headers=owner),
            client.put(f"/api/playlists/{playlist_b_id}", headers=owner, json={"name": "probe"}),
            client.post(f"/api/playlists/{playlist_b_id}/items", headers=owner, json={"content_id": content_id}),
            client.put(f"/api/playlists/{playlist_b_id}/items/{item_b_id}", headers=owner, json={"duration": 11}),
            client.put(f"/api/playlists/{playlist_b_id}/transitions", headers=owner, json={"transition": "fade", "transition_ms": 500}),
            client.put(f"/api/playlists/{playlist_b_id}/items/reorder", headers=owner, json=[item_b_id]),
            client.delete(f"/api/playlists/{playlist_b_id}/items/{item_b_id}", headers=owner),
            client.delete(f"/api/playlists/{playlist_b_id}", headers=owner),
            client.put(f"/api/groups/{group_b_id}", headers=owner, json={"name": "probe"}),
            client.put(f"/api/groups/{group_b_id}/screens", headers=owner, json={"screen_ids": []}),
            client.post(f"/api/groups/{group_b_id}/assign/{playlist_id}", headers=owner),
            client.delete(f"/api/groups/{group_b_id}", headers=owner),
            client.put(f"/api/screens/{screen_b_id}", headers=owner, json={"name": "probe", "orientation": 0}),
            client.post(f"/api/screens/{screen_b_id}/assign/{playlist_id}", headers=owner),
            client.delete(f"/api/screens/{screen_b_id}/assign", headers=owner),
            client.post(f"/api/screens/{screen_id}/assign/{playlist_b_id}", headers=owner),
            client.post("/api/screens/pair", headers=owner, json={"pair_code": registration_b.json()["pair_code"]}),
        ]
        unexpected_cross_tenant = [
            (index, response.status_code, response.text)
            for index, response in enumerate(cross_tenant_requests)
            if response.status_code not in (403, 404)
        ]
        assert not unexpected_cross_tenant, unexpected_cross_tenant
        assert client.get("/api/screens/tenant-b-tv/sync").json()["playlist"]["id"] == playlist_b_id
        assert client.get("/api/screens/feature-tv/sync").json()["playlist"]["id"] == playlist_id

        # Each organization may delete its own upload; this also removes exact test files.
        assert client.delete(f"/api/content/{upload_a_payload['id']}", headers=owner).status_code == 200
        assert client.delete(f"/api/content/{content_b_id}", headers=owner_b).status_code == 200

        # P6: limits, accurate usage, webhook upgrades, grace, and read-only lapse.
        db = database.SessionLocal()
        organization_a = db.query(models.Organization).filter(models.Organization.id == 1).one()
        free_plan = db.query(models.Plan).filter(models.Plan.slug == "free").one()
        original_screen_limit = free_plan.max_screens
        free_storage_limit = free_plan.max_storage_bytes
        free_plan.max_screens = 1
        db.commit()
        db.close()
        limit_registration = client.post("/api/screens/register", json={"device_id": "over-limit-tv"})
        limit_pair = client.post(
            "/api/screens/pair",
            headers=owner,
            json={"pair_code": limit_registration.json()["pair_code"]},
        )
        assert limit_pair.status_code == 409
        assert "Upgrade" in limit_pair.json()["detail"]

        db = database.SessionLocal()
        free_plan = db.query(models.Plan).filter(models.Plan.slug == "free").one()
        free_plan.max_screens = original_screen_limit
        organization_a = db.query(models.Organization).filter(models.Organization.id == 1).one()
        organization_a.storage_quota_bytes = 5
        db.commit()
        db.close()
        quota_rejection = client.post(
            "/api/content/upload",
            headers=owner,
            files={"file": ("too-large.png", b"123456", "image/png")},
            data={"name": "Too large"},
        )
        assert quota_rejection.status_code == 413

        db = database.SessionLocal()
        organization_a = db.query(models.Organization).filter(models.Organization.id == 1).one()
        organization_a.storage_quota_bytes = 10
        db.commit()
        db.close()
        metered_upload = client.post(
            "/api/content/upload",
            headers=owner,
            files={"file": ("metered.png", b"1234", "image/png")},
            data={"name": "Metered"},
        )
        assert metered_upload.status_code == 201
        summary = client.get("/api/billing/summary", headers=owner)
        assert summary.status_code == 200, summary.text
        assert summary.json()["storage_used_bytes"] == 4
        assert client.delete(f"/api/content/{metered_upload.json()['id']}", headers=owner).status_code == 200

        db = database.SessionLocal()
        organization_a = db.query(models.Organization).filter(models.Organization.id == 1).one()
        organization_a.storage_quota_bytes = free_storage_limit
        db.commit()
        db.close()
        checkout = client.post(
            "/api/billing/checkout",
            headers=owner,
            json={"plan_id": 2, "billing_period": "monthly"},
        )
        assert checkout.status_code == 200, checkout.text
        provider_subscription_id = checkout.json()["provider_subscription_id"]

        def send_webhook(event_type: str, event_id: str, *, valid_signature: bool = True):
            raw = json.dumps(
                {
                    "event": event_type,
                    "payload": {
                        "subscription": {
                            "entity": {
                                "id": provider_subscription_id,
                                # Relative to now. These were fixed epochs picked while they
                                # were still ahead of the calendar; `current_end` fell into
                                # the past during September 2026, so a "recovered"
                                # subscription was being handed a window that had already
                                # closed. Harmless while nothing compared the period to the
                                # clock, and an instant failure once something did.
                                "current_start": int(time.time()) - 86_400,
                                "current_end": int(time.time()) + 30 * 86_400,
                            }
                        }
                    },
                },
                separators=(",", ":"),
            ).encode()
            signature = hmac.new(
                os.environ["RAZORPAY_WEBHOOK_SECRET"].encode(), raw, hashlib.sha256
            ).hexdigest()
            if not valid_signature:
                signature = "0" * 64
            return client.post(
                "/api/billing/webhooks/razorpay",
                content=raw,
                headers={
                    "Content-Type": "application/json",
                    "X-Razorpay-Signature": signature,
                    "X-Razorpay-Event-Id": event_id,
                },
            )

        assert send_webhook("subscription.activated", "evt-activate").status_code == 200
        assert send_webhook("subscription.activated", "evt-activate").json()["status"] == "duplicate"
        assert send_webhook("subscription.activated", "evt-invalid", valid_signature=False).status_code == 401
        upgraded_summary = client.get("/api/billing/summary", headers=owner).json()
        assert upgraded_summary["plan"]["id"] == 2
        assert upgraded_summary["subscription"]["status"] == "active"
        assert upgraded_summary["plan"]["max_screens"] == 10

        assert send_webhook("subscription.pending", "evt-payment-failed").status_code == 200
        grace_summary = client.get("/api/billing/summary", headers=owner).json()
        assert grace_summary["subscription"]["status"] == "grace"
        assert grace_summary["subscription"]["grace_period_end"] is not None
        assert grace_summary["is_read_only"] is False
        grace_write = client.post("/api/playlists/", headers=owner, json={"name": "Grace write"})
        assert grace_write.status_code == 201

        db = database.SessionLocal()
        subscription_a = db.query(models.Subscription).filter(models.Subscription.organization_id == 1).one()
        subscription_a.grace_period_end = models.utcnow() - timedelta(seconds=1)
        db.commit()
        db.close()
        assert client.get("/api/billing/summary", headers=owner).json()["is_read_only"] is True
        blocked_write = client.post("/api/playlists/", headers=owner, json={"name": "Blocked"})
        assert blocked_write.status_code == 403
        # A billing lapse must never make the player's sync FAIL -- the app treats 401/403 as
        # transient and would retry forever on its cached playlist, so a refusal stops
        # nothing. It answers 200 and stops the adverts by serving something else instead:
        # the demo reel, with the reason attached.
        lapsed_sync = client.get("/api/screens/feature-tv/sync")
        assert lapsed_sync.status_code == 200
        assert lapsed_sync.json()["service_state"] == "expired"
        assert lapsed_sync.json()["playlist"]["name"] == "MR Signage Universal Demo Loop"
        assert send_webhook("subscription.activated", "evt-recovered").status_code == 200
        assert client.get("/api/billing/summary", headers=owner).json()["is_read_only"] is False

        telemetry = client.post(
            "/api/screens/heartbeat",
            json={
                "device_id": "feature-tv",
                "playback_state": "error",
                "current_item_id": item_id,
                "last_error": "Decoder rejected corrupt media",
                "app_version": "2.4.0",
            },
        )
        assert telemetry.status_code == 200, telemetry.text
        screen_payload = next(
            screen for screen in client.get("/api/screens/", headers=owner).json()
            if screen["id"] == screen_id
        )
        assert screen_payload["playback_state"] == "error"
        assert screen_payload["current_item_id"] == item_id
        assert screen_payload["last_error"] == "Decoder rejected corrupt media"
        assert screen_payload["last_error_at"] is not None
        assert screen_payload["app_version"] == "2.4.0"

        db = database.SessionLocal()
        screen = db.query(models.Screen).filter(models.Screen.id == screen_id).one()
        screen.status = "online"
        screen.last_seen = models.utcnow() - timedelta(seconds=61)
        db.commit()
        db.close()
        os.environ["SCREEN_OFFLINE_AFTER_SECONDS"] = "60"

        # Presence is authoritative from Redis when Redis is reachable, so back-dating
        # last_seen alone no longer simulates a quiet screen: the heartbeat earlier in
        # this test left a presence key whose TTL has not elapsed. A real TV that stops
        # reporting loses that key, so drop it here to model the same state. Best-effort
        # because the suite must also pass with Redis stopped, where the last_seen
        # threshold above is what decides the answer.
        try:
            import redis as _redis

            _client = _redis.Redis.from_url(
                os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                socket_connect_timeout=2,
            )
            _client.delete(f"screen_presence:{screen_id}")
            _client.close()
        except Exception:
            pass
        offline_payload = next(
            screen for screen in client.get("/api/screens/", headers=owner).json()
            if screen["id"] == screen_id
        )
        assert offline_payload["status"] == "offline"

        assert client.get("/api/playlists/", headers=viewer).status_code == 200
        print("MR Signage feature parity backend check passed")


def main() -> None:
    try:
        run()
    finally:
        # SQLite keeps the Windows temp file locked until the pooled engine closes.
        database.engine.dispose()
        TEMP_DIR.cleanup()


# Deliberately not named test_*: conftest.py collects this file as a subprocess
# so it owns its database engine. A pytest-visible wrapper would also be imported
# into the shared process and run a second time against a torn-down database.
if __name__ == "__main__":
    main()
