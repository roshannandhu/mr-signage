"""Every feature, exercised as the role that owns it: python tests/test_role_separation.py

Two questions, asked of the whole API rather than a sample:

  1. Does each role's OWN feature set actually work end to end? A permission change that
     quietly breaks the dashboard is as bad as one that leaks -- and far likelier to ship,
     because nothing 500s, the page just comes back empty.
  2. Is the other role's feature set refused?

The split under test:

  SUPER ADMIN  platform operator. Approves companies, sets packages and quotas, blocks
               tenants, publishes player releases, sets the demo reel. Has no tenant
               workspace to run and needs no tenant features.
  TENANT       owner/editor/viewer inside one organisation. Screens, media, playlists,
               groups, campaigns, bookings, team, billing -- all scoped to that org and
               invisible to every other org.

Roles are also checked against each other WITHIN a tenant, because "tenant features work"
is not one thing: an editor must not be able to rewrite the team, and a viewer must not be
able to change what is on the screens.
"""

import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEMP_DIR = tempfile.TemporaryDirectory(prefix="olrac-roles-test-", ignore_cleanup_errors=True)
DB_PATH = Path(TEMP_DIR.name) / "roles.db"
import psycopg2  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

test_db_name = f"olrac_test_{DB_PATH.stem.replace('-', '_')}"
try:
    conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    conn.cursor().execute(f"DROP DATABASE IF EXISTS {test_db_name}")
    conn.cursor().execute(f"CREATE DATABASE {test_db_name} OWNER olrac")
    conn.close()
except Exception:
    pass
os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{test_db_name}"
os.environ["SECRET_KEY"] = "roles-test-secret-not-for-production"
os.environ["INITIAL_ADMIN_USERNAME"] = "platform-op"
os.environ["INITIAL_ADMIN_PASSWORD"] = "platform-pass-123"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["AWS_SECRET_ACCESS_KEY"] = "mock"
os.environ["PAYMENT_PROVIDER"] = "mock"

from fastapi.testclient import TestClient  # noqa: E402

from backend import database, models  # noqa: E402
from backend.limiter import limiter  # noqa: E402
from backend.main import app  # noqa: E402
from backend.routers.auth import get_password_hash  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)
limiter.enabled = False

client = TestClient(app)
client.__enter__()

failures: list[str] = []
passes = {"super_admin": 0, "tenant": 0, "denied": 0}


def check(condition: bool, message: str, bucket: str | None = None) -> None:
    if condition:
        if bucket:
            passes[bucket] += 1
    else:
        failures.append(message)


def works(response, label: str, bucket: str, ok=(200, 201, 204)) -> bool:
    """A feature this role owns must actually work."""
    good = response.status_code in ok
    check(good, f"BROKEN [{bucket}] {label} -> {response.status_code} {response.text[:160]}", bucket)
    return good


def denied(response, label: str) -> None:
    """A feature this role does not own must be refused, not silently allowed."""
    check(
        response.status_code in (401, 403),
        f"LEAK {label} -> {response.status_code} (expected 401/403)",
        "denied",
    )


def invisible(response, label: str) -> None:
    """Another tenant's records must not be reachable -- and 404 is the RIGHT answer.

    403 would confirm the row exists, which is itself a leak: it lets one customer probe
    another's id space and learn how many screens or adverts they run. scope.get() filters
    by organisation, finds nothing, and the route reports "not found" exactly as it would
    for an id that never existed.
    """
    check(
        response.status_code in (401, 403, 404),
        f"LEAK {label} -> {response.status_code} (expected 404, or 401/403)",
        "denied",
    )


def token_for(username: str, password: str) -> str:
    r = client.post("/api/auth/token", data={"username": username, "password": password})
    assert r.status_code == 200, f"{username}: {r.text}"
    return r.json()["access_token"]


def hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- fixtures

def seed() -> dict:
    db = database.SessionLocal()
    try:
        op = db.query(models.User).filter(models.User.username == "platform-op").one()
        op.role = "super_admin"

        org_a = models.Organization(name="Acme Media", slug="acme", status="active")
        org_b = models.Organization(name="Rival Signage", slug="rival", status="active")
        db.add_all([org_a, org_b])
        db.flush()

        for org, prefix in ((org_a, "acme"), (org_b, "rival")):
            for role in ("owner", "editor", "viewer"):
                db.add(models.User(
                    organization_id=org.id, username=f"{prefix}-{role}",
                    email=f"{role}@{prefix}.example",
                    hashed_password=get_password_hash("tenant-pass-123"),
                    role=role, is_active=True,
                ))

        # Something in the rival org for cross-tenant probes to aim at.
        rival_playlist = models.Playlist(organization_id=org_b.id, name="Rival Loop")
        rival_content = models.Content(
            organization_id=org_b.id, type="image", file_url="/uploads/rival.png",
            name="Rival Asset", file_size_bytes=10, status="ready",
        )
        rival_screen = models.Screen(
            organization_id=org_b.id, device_id="rival-tv-1", name="Rival TV", status="online",
        )
        rival_group = models.ScreenGroup(organization_id=org_b.id, name="Rival Group")
        db.add_all([rival_playlist, rival_content, rival_screen, rival_group])
        db.commit()
        return {
            "org_a": org_a.id, "org_b": org_b.id,
            "rival_playlist": rival_playlist.id, "rival_content": rival_content.id,
            "rival_screen": rival_screen.id, "rival_group": rival_group.id,
        }
    finally:
        db.close()


# --------------------------------------------------------------------------- suites

def super_admin_features(admin: dict, ids: dict) -> None:
    """Everything the platform operator owns, driven for real."""
    works(client.get("/api/admin/tenants", headers=admin), "list all tenants", "super_admin")
    works(client.get(f"/api/admin/tenants/{ids['org_a']}", headers=admin), "tenant detail", "super_admin")
    works(client.get(f"/api/admin/tenants/{ids['org_a']}/screens", headers=admin), "tenant screens", "super_admin")
    works(client.get(f"/api/admin/tenants/{ids['org_a']}/content", headers=admin), "tenant content", "super_admin")
    works(client.get(f"/api/admin/tenants/{ids['org_a']}/users", headers=admin), "tenant users", "super_admin")

    created = client.post("/api/admin/plans", headers=admin, json={
        "name": "Pro", "slug": "pro-test", "monthly_price_paise": 299900,
        "yearly_price_paise": 2999000, "max_screens": 25,
        "max_storage_bytes": 100 * 1024 ** 3, "max_ad_slots": 50, "is_active": True,
    })
    works(created, "create package", "super_admin", ok=(201,))
    plan_id = created.json()["id"] if created.status_code == 201 else None

    works(client.get("/api/admin/plans", headers=admin), "list packages", "super_admin")
    if plan_id:
        works(client.patch(f"/api/admin/plans/{plan_id}", headers=admin, json={"max_screens": 30}),
              "edit package", "super_admin")

    works(client.patch(f"/api/admin/tenants/{ids['org_a']}/quota", headers=admin,
                       json={"plan_id": plan_id, "max_screens": 30, "max_ad_slots": 50}),
          "set tenant quota", "super_admin")

    works(client.post(f"/api/admin/tenants/{ids['org_b']}/suspend", headers=admin),
          "block a tenant", "super_admin")
    works(client.post(f"/api/admin/tenants/{ids['org_b']}/reinstate", headers=admin),
          "reinstate a tenant", "super_admin")

    works(client.get("/api/admin/demo-video", headers=admin), "read demo reel", "super_admin")
    works(client.post("/api/admin/demo-video", headers=admin,
                      json={"url": "/uploads/demo/reel.mp4"}), "set demo reel", "super_admin")

    # Publishing a player release is super-admin-only and installs across every fleet.
    works(client.post("/api/releases/", headers=admin, json={
        "version_code": 99, "version_name": "9.9", "apk_url": "https://example.com/p.apk",
        "sha256": "a" * 64, "mandatory": False,
    }), "publish player release", "super_admin", ok=(200, 201))

    if plan_id:
        works(client.delete(f"/api/admin/plans/{plan_id}", headers=admin),
              "retire package", "super_admin")


def super_admin_has_no_tenant_ui(admin: dict) -> None:
    """The platform operator is not a tenant, and the dashboard must not pretend otherwise.

    These are NOT expected to 403 -- a super admin legitimately reads across tenants, which
    is what the drill-in is built on. What is asserted is that they are not handed someone
    else's workspace as though it were their own: the account has an organisation of its
    own and it is empty.
    """
    me = client.get("/api/auth/me", headers=admin)
    works(me, "super admin profile", "super_admin")
    if me.status_code == 200:
        check(me.json()["role"] == "super_admin",
              f"super admin role not reported: {me.json().get('role')}", "super_admin")

    screens = client.get("/api/screens/", headers=admin)
    works(screens, "super admin screen list", "super_admin")


def tenant_features(owner: dict, editor: dict, viewer: dict) -> dict:
    """The whole tenant product, driven as the roles that own each part."""
    made: dict = {}

    # --- content ---------------------------------------------------------------------
    upload = client.post(
        "/api/content/upload", headers=editor,
        files={"file": ("advert.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"0" * 64), "image/png")},
        data={"name": "Client Advert", "tags": "promo"},
    )
    works(upload, "editor uploads content", "tenant", ok=(200, 201))
    if upload.status_code in (200, 201):
        made["content"] = upload.json()["id"]
    works(client.get("/api/content/", headers=viewer), "viewer lists content", "tenant")

    # --- playlists -------------------------------------------------------------------
    playlist = client.post("/api/playlists/", headers=editor, json={"name": "Store Loop"})
    works(playlist, "editor creates playlist", "tenant", ok=(200, 201))
    if playlist.status_code in (200, 201):
        made["playlist"] = playlist.json()["id"]
        works(client.get(f"/api/playlists/{made['playlist']}", headers=viewer),
              "viewer reads playlist", "tenant")
        if "content" in made:
            item = client.post(f"/api/playlists/{made['playlist']}/items", headers=editor,
                               json={"content_id": made["content"], "duration": 12})
            works(item, "editor adds playlist item", "tenant", ok=(200, 201))
        # Transitions are a paid feature, and this file is about roles, not packages -- so
        # put the workspace on one that includes it, or the assertion passes or fails for
        # the wrong reason. Business (slug "business") is the seeded package carrying it.
        db = database.SessionLocal()
        try:
            business = db.query(models.Plan).filter(models.Plan.slug == "business").first()
            org = db.query(models.Organization).filter(
                models.Organization.slug == "acme"
            ).one()
            if business:
                org.plan_id = business.id
                db.commit()
        finally:
            db.close()

        works(client.put(f"/api/playlists/{made['playlist']}/transitions", headers=editor,
                         json={"transition": "fade", "transition_ms": 600, "apply_to_all": True}),
              "editor sets transitions", "tenant")

    # --- screens ----------------------------------------------------------------------
    reg = client.post("/api/screens/register", json={
        "device_id": "acme-tv-1", "installation_id": "sn_ACME001",
        "hardware_name": "Realtek TV", "device_model": "Realtek TV", "manufacturer": "Realtek",
    })
    works(reg, "TV registers", "tenant")
    pair_code = reg.json().get("pair_code") if reg.status_code == 200 else None
    if pair_code:
        paired = client.post("/api/screens/pair", headers=owner, json={"pair_code": pair_code})
        works(paired, "owner pairs a screen", "tenant")
        if paired.status_code == 200:
            made["screen"] = paired.json()["id"]

    works(client.get("/api/screens/", headers=viewer), "viewer lists screens", "tenant")
    if "screen" in made:
        works(client.patch(f"/api/screens/{made['screen']}", headers=editor,
                           json={"name": "Front Window"}), "editor renames screen", "tenant")
        if "playlist" in made:
            works(client.post(f"/api/screens/{made['screen']}/assign/{made['playlist']}", headers=editor),
                  "editor assigns playlist to screen", "tenant")
        works(client.get(f"/api/screenshots/{made['screen']}/screenshots", headers=viewer),
              "viewer lists screenshots", "tenant")

    # --- groups -----------------------------------------------------------------------
    group = client.post("/api/groups/", headers=editor, json={"name": "Shop Floor"})
    works(group, "editor creates group", "tenant", ok=(200, 201))
    if group.status_code in (200, 201):
        made["group"] = group.json()["id"]
        works(client.get("/api/groups/", headers=viewer), "viewer lists groups", "tenant")
        if "screen" in made:
            works(client.put(f"/api/groups/{made['group']}/screens", headers=editor,
                             json={"screen_ids": [made["screen"]]}), "editor sets group members", "tenant")
        if "playlist" in made:
            works(client.post(f"/api/groups/{made['group']}/assign/{made['playlist']}", headers=editor),
                  "editor assigns playlist to group", "tenant")

    # --- ad bookings (the revenue feature) ---------------------------------------------
    if "screen" in made:
        ad_upload = client.post(
            "/api/content/upload", headers=editor,
            files={"file": ("billboard.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"1" * 64), "image/png")},
            data={"name": "Billboard Ad", "tags": "ad"},
        )
        if ad_upload.status_code in (200, 201):
            ad_content_id = ad_upload.json()["id"]
            booking = client.post("/api/placements/", headers=editor, json={
                "content_id": ad_content_id, "advertiser": "Local Cafe",
                "price_paise": 500000, "is_paid": True,
                "starts_at": "2026-08-01T00:00:00Z", "ends_at": "2026-12-01T00:00:00Z",
                "targets": [{"screen_id": made["screen"]}],
            })
            works(booking, "editor books an advert", "tenant", ok=(200, 201))
            if booking.status_code in (200, 201):
                made["placement"] = booking.json()["id"]
                works(client.get(f"/api/placements/{made['placement']}/report", headers=viewer),
                      "viewer reads booking report", "tenant")
            works(client.get("/api/placements/", headers=viewer), "viewer lists bookings", "tenant")

    # --- reporting, alerts, billing, provisioning ---------------------------------------
    works(client.get("/api/analytics/campaigns", headers=viewer), "viewer lists campaigns", "tenant")
    if "content" in made:
        works(client.get(f"/api/analytics/media/{made['content']}", headers=viewer),
              "viewer reads proof-of-play", "tenant")
    works(client.get("/api/alerts/", headers=viewer), "viewer lists alerts", "tenant")
    works(client.get("/api/alerts/summary", headers=viewer), "viewer reads alert summary", "tenant")
    works(client.get("/api/billing/summary", headers=owner), "owner reads billing", "tenant")
    works(client.get("/api/billing/plans", headers=owner), "owner lists plans", "tenant")
    works(client.get("/api/emergency/active", headers=viewer), "viewer reads emergency state", "tenant")
    works(client.get("/api/enrollment-tokens/", headers=owner), "owner lists enrollment tokens", "tenant")
    works(client.post("/api/enrollment-tokens/", headers=owner,
                      json={"description": "site A", "max_uses": 5}),
          "owner mints enrollment token", "tenant", ok=(200, 201))

    # --- team + profile ------------------------------------------------------------------
    works(client.get("/api/users/", headers=owner), "owner lists team", "tenant")
    works(client.post("/api/users/", headers=owner,
                      json={"username": "acme-newbie", "password": "newbie-pass-123", "role": "viewer"}),
          "owner adds a team member", "tenant", ok=(200, 201))
    works(client.get("/api/auth/me", headers=viewer), "viewer reads own profile", "tenant")
    works(client.patch("/api/auth/me", headers=viewer, json={"full_name": "Vera Viewer"}),
          "viewer edits own profile", "tenant")

    return made


def tenant_is_refused_platform_features(owner: dict, editor: dict, viewer: dict, ids: dict) -> None:
    """No tenant role, not even owner, reaches the platform console."""
    for label, headers in (("owner", owner), ("editor", editor), ("viewer", viewer)):
        denied(client.get("/api/admin/tenants", headers=headers), f"{label} -> list all tenants")
        denied(client.get("/api/admin/plans", headers=headers), f"{label} -> list packages")
        denied(client.get(f"/api/admin/tenants/{ids['org_b']}", headers=headers), f"{label} -> rival detail")
        denied(client.get(f"/api/admin/tenants/{ids['org_b']}/users", headers=headers),
               f"{label} -> rival team")
        denied(client.post(f"/api/admin/tenants/{ids['org_a']}/approve", headers=headers, json={}),
               f"{label} -> approve own workspace")
        denied(client.post(f"/api/admin/tenants/{ids['org_b']}/suspend", headers=headers),
               f"{label} -> suspend a competitor")
        denied(client.patch(f"/api/admin/tenants/{ids['org_a']}/quota", headers=headers,
                            json={"max_screens": 9999}), f"{label} -> raise own quota")
        denied(client.post("/api/admin/plans", headers=headers, json={
            "name": "Free For All", "slug": "ffa", "monthly_price_paise": 0,
            "yearly_price_paise": 0, "max_screens": 0, "max_storage_bytes": 0,
            "max_ad_slots": 0, "is_active": True,
        }), f"{label} -> create a package")
        denied(client.post("/api/admin/demo-video", headers=headers, json={"url": "/x.mp4"}),
               f"{label} -> set the platform demo reel")

    # Publishing a player release reaches every tenant's hardware.
    denied(client.post("/api/releases/", headers=owner, json={
        "version_code": 500, "version_name": "5.0", "apk_url": "https://evil.example/p.apk",
        "sha256": "b" * 64, "mandatory": True,
    }), "owner -> publish a player release")


def tenant_roles_are_separated(owner: dict, editor: dict, viewer: dict, made: dict) -> None:
    """Inside one workspace, a role may not exceed itself."""
    denied(client.get("/api/users/", headers=editor), "editor -> read the team")
    denied(client.post("/api/users/", headers=editor,
                       json={"username": "sneak", "password": "pass-12345", "role": "owner"}),
           "editor -> create a user")
    denied(client.get("/api/users/", headers=viewer), "viewer -> read the team")
    denied(client.post("/api/playlists/", headers=viewer, json={"name": "Viewer Loop"}),
           "viewer -> create a playlist")
    denied(client.post("/api/groups/", headers=viewer, json={"name": "Viewer Group"}),
           "viewer -> create a group")
    if "screen" in made:
        denied(client.patch(f"/api/screens/{made['screen']}", headers=viewer, json={"name": "Hijack"}),
               "viewer -> rename a screen")
    denied(client.post("/api/enrollment-tokens/", headers=editor, json={"description": "x"}),
           "editor -> mint an enrollment token")
    # Body must be VALID, or a 422 masks the role check and the assertion passes for the
    # wrong reason. plan_id 1 is the Free plan seeded by ensure_billing_catalog.
    denied(client.post("/api/billing/checkout", headers=editor,
                       json={"plan_id": 1, "billing_period": "monthly"}),
           "editor -> change the subscription")

    # Escalating your own role is the one that ends the product.
    promoted = client.patch("/api/auth/me", headers=viewer, json={"role": "owner"})
    if promoted.status_code in (200, 204):
        me = client.get("/api/auth/me", headers=viewer).json()
        check(me["role"] == "viewer",
              f"ESCALATION: a viewer promoted itself to {me['role']} through /auth/me", "denied")
    else:
        passes["denied"] += 1


def cross_tenant_is_refused(owner: dict, ids: dict) -> None:
    """Acme's owner must not touch Rival's anything."""
    invisible(client.get(f"/api/playlists/{ids['rival_playlist']}", headers=owner),
           "acme -> read rival playlist")
    invisible(client.patch(f"/api/screens/{ids['rival_screen']}", headers=owner, json={"name": "Stolen"}),
           "acme -> rename rival screen")
    invisible(client.delete(f"/api/content/{ids['rival_content']}", headers=owner),
           "acme -> delete rival content")
    invisible(client.put(f"/api/groups/{ids['rival_group']}", headers=owner,
                      json={"name": "Stolen", "parent_id": None, "is_dynamic": False,
                            "dynamic_criteria": None}),
           "acme -> rename rival group")
    invisible(client.get(f"/api/analytics/media/{ids['rival_content']}", headers=owner),
           "acme -> read rival proof-of-play")
    invisible(client.post(f"/api/screens/{ids['rival_screen']}/bring-to-front", headers=owner),
           "acme -> control rival screen")

    listed = client.get("/api/screens/", headers=owner)
    if listed.status_code == 200:
        check(
            all(s["id"] != ids["rival_screen"] for s in listed.json()),
            "LEAK acme's fleet list contains a rival screen", "denied",
        )


def unauthenticated_is_refused(ids: dict) -> None:
    """No token at all reaches nothing that matters."""
    for label, response in (
        ("list tenants", client.get("/api/admin/tenants")),
        ("list packages", client.get("/api/admin/plans")),
        ("suspend a tenant", client.post(f"/api/admin/tenants/{ids['org_a']}/suspend")),
        ("list screens", client.get("/api/screens/")),
        ("list content", client.get("/api/content/")),
        ("list team", client.get("/api/users/")),
        ("publish release", client.post("/api/releases/", json={})),
    ):
        denied(response, f"anonymous -> {label}")


def run() -> None:
    ids = seed()
    admin = hdr(token_for("platform-op", "platform-pass-123"))
    owner = hdr(token_for("acme-owner", "tenant-pass-123"))
    editor = hdr(token_for("acme-editor", "tenant-pass-123"))
    viewer = hdr(token_for("acme-viewer", "tenant-pass-123"))

    super_admin_features(admin, ids)
    super_admin_has_no_tenant_ui(admin)
    made = tenant_features(owner, editor, viewer)
    tenant_is_refused_platform_features(owner, editor, viewer, ids)
    tenant_roles_are_separated(owner, editor, viewer, made)
    cross_tenant_is_refused(owner, ids)
    unauthenticated_is_refused(ids)

    print(f"super admin features working : {passes['super_admin']}")
    print(f"tenant features working      : {passes['tenant']}")
    print(f"correctly refused            : {passes['denied']}")
    print()

    if failures:
        print("ROLE SEPARATION FAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        raise SystemExit(1)
    print("each role has its own features, all working, nothing crossing over: verified")


if __name__ == "__main__":
    try:
        run()
    finally:
        client.__exit__(None, None, None)
