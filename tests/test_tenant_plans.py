"""Clients and tenant-sold plans belong to one tenant, and a plan's terms are copied not linked.

Two properties are under test, and both silently corrupt a business rather than raising:

* Isolation. A tenant's client list is their customer list -- names, addresses and phone
  numbers -- and their plans are their pricing. Neither may be readable, bookable against,
  or editable by another tenant.
* Copy-on-sale. A booking takes its price and duration from a plan at the moment it is
  made. If it read through instead, repricing a package next quarter would restate every
  invoice already issued under the old one.

Throwaway Postgres database. Run directly:  python tests/test_tenant_plans.py
"""
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCRATCH = f"olrac_tenantplans_{uuid.uuid4().hex[:8]}"
os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{SCRATCH}"
os.environ["SECRET_KEY"] = "tenant-plans-secret"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"

import psycopg2  # noqa: E402
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT  # noqa: E402

admin = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
admin.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
admin.cursor().execute(f'CREATE DATABASE "{SCRATCH}" OWNER olrac')

db = None
try:
    from fastapi.testclient import TestClient  # noqa: E402
    from backend import models  # noqa: E402
    from backend.database import SessionLocal, engine  # noqa: E402
    from backend.main import app  # noqa: E402
    from backend.routers.auth import create_access_token, get_password_hash  # noqa: E402

    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    acme = models.Organization(name="Acme", slug="acme")
    rival = models.Organization(name="Rival", slug="rival")
    db.add_all([acme, rival]); db.commit()
    owner = models.User(organization_id=acme.id, username="owner@acme.test",
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    intruder = models.User(organization_id=rival.id, username="owner@rival.test",
                           hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add_all([owner, intruder]); db.commit()

    ad = models.Content(organization_id=acme.id, type="video", file_url="/uploads/1/a.mp4",
                        name="Summer Sale", status="ready", duration_ms=30_000)
    ad2 = models.Content(organization_id=acme.id, type="video", file_url="/uploads/1/a2.mp4",
                         name="Winter Sale", status="ready", duration_ms=30_000)
    ad3 = models.Content(organization_id=acme.id, type="video", file_url="/uploads/1/a3.mp4",
                         name="Spring Sale", status="ready", duration_ms=30_000)
    ad4 = models.Content(organization_id=acme.id, type="video", file_url="/uploads/1/a4.mp4",
                         name="Autumn Sale", status="ready", duration_ms=30_000)
    ad5 = models.Content(organization_id=acme.id, type="video", file_url="/uploads/1/a5.mp4",
                         name="Holiday Sale", status="ready", duration_ms=30_000)
    db.add_all([ad, ad2, ad3, ad4, ad5]); db.commit()
    screen = models.Screen(organization_id=acme.id, name="Lobby", status="online")
    db.add(screen); db.commit()

    def make_ad(name="Advert"):
        cnt = models.Content(
            organization_id=acme.id,
            type="video",
            file_url=f"/uploads/1/{uuid.uuid4().hex[:8]}.mp4",
            name=name,
            status="ready",
            duration_ms=30_000,
        )
        db.add(cnt)
        db.commit()
        return cnt

    http = TestClient(app)
    auth = {"Authorization": f"Bearer {create_access_token(data={'sub': owner.username})}"}
    rival_auth = {"Authorization": f"Bearer {create_access_token(data={'sub': intruder.username})}"}
    now = models.utcnow()

    # --- client codes are per tenant, and issued without being asked for ---------------
    first = http.post("/api/clients/", headers=auth, json={"name": "BrightMart"})
    assert first.status_code == 201, first.text
    assert first.json()["client_code"] == "CLT00001", first.json()
    second = http.post("/api/clients/", headers=auth, json={"name": "Phoenix Retail"})
    assert second.json()["client_code"] == "CLT00002", second.json()

    # The rival starts at CLT00001 too. The uniqueness is per organisation on purpose --
    # a global sequence would leak how many clients other tenants have.
    theirs = http.post("/api/clients/", headers=rival_auth, json={"name": "Someone Else"})
    assert theirs.status_code == 201, theirs.text
    assert theirs.json()["client_code"] == "CLT00001", theirs.json()
    print("  ok  client codes are issued per tenant and restart for each one")

    # A new code must never collide with one still in use. Derived from the highest code
    # issued rather than from a count, because a count reissues CLT00002 the moment the
    # first client is deleted -- straight into the unique constraint.
    #
    # A code freed by a deletion IS reused, which is a deliberate limit: the alternative is
    # a persistent per-tenant counter, and the risk it accepts is an old invoice naming a
    # code now held by a different client.
    third = http.post("/api/clients/", headers=auth, json={"name": "Temp"})
    assert third.json()["client_code"] == "CLT00003", third.json()
    http.delete(f"/api/clients/{third.json()['id']}", headers=auth)
    fourth = http.post("/api/clients/", headers=auth, json={"name": "After The Gap"})
    assert fourth.status_code == 201, fourth.text
    live = [c["client_code"] for c in http.get("/api/clients/", headers=auth).json()]
    assert len(live) == len(set(live)), live
    print("  ok  a new client code never collides with a live one")

    # --- isolation --------------------------------------------------------------------
    acme_client_id = first.json()["id"]
    assert [c["name"] for c in http.get("/api/clients/", headers=rival_auth).json()] == ["Someone Else"]
    assert http.get(f"/api/clients/{acme_client_id}", headers=rival_auth).status_code == 404
    assert http.put(f"/api/clients/{acme_client_id}", headers=rival_auth,
                    json={"name": "Hijacked"}).status_code == 404
    assert http.delete(f"/api/clients/{acme_client_id}", headers=rival_auth).status_code == 404
    print("  ok  one tenant cannot read, edit or delete another's clients")

    plan = http.post("/api/tenant-plans/", headers=auth, json={
        "name": "Standard Plan", "duration_days": 31, "max_locations": 5,
        "ad_slots": 1, "price_paise": 2500000, "support_tier": "Basic Support",
    })
    assert plan.status_code == 201, plan.text
    plan_id = plan.json()["id"]
    assert http.get("/api/tenant-plans/", headers=rival_auth).json() == []
    assert http.get(f"/api/tenant-plans/{plan_id}", headers=rival_auth).status_code == 404
    print("  ok  one tenant cannot see or fetch another's plans")

    # --- a plan fills in price and end date, by copying them ---------------------------
    booked = http.post("/api/placements/", headers=auth, json={
        "content_id": ad.id, "client_id": acme_client_id, "plan_id": plan_id,
        "starts_at": now.isoformat(), "targets": [{"screen_id": screen.id}],
    })
    assert booked.status_code == 201, booked.text
    body = booked.json()
    assert body["price_paise"] == 2500000, body
    assert body["advertiser"] == "BrightMart", body
    assert body["client"]["client_code"] == "CLT00001", body
    # duration_days drove the end date, which is the point of selling by package.
    assert datetime.fromisoformat(body["effective_ends_at"]).astimezone(timezone.utc).date() == (now + timedelta(days=31)).date(), body
    print("  ok  booking on a plan takes its price and its duration")

    # Repricing the plan must not restate the sold booking.
    http.put(f"/api/tenant-plans/{plan_id}", headers=auth, json={"price_paise": 9900000})
    after = http.get("/api/placements/", headers=auth).json()[0]
    assert after["price_paise"] == 2500000, after
    print("  ok  repricing a plan leaves an already-sold booking billed as agreed")

    # A plan with bookings is retired, not deleted: the report still prints its name.
    retired = http.delete(f"/api/tenant-plans/{plan_id}", headers=auth)
    assert retired.json()["status"] == "retired", retired.json()
    assert http.get(f"/api/tenant-plans/{plan_id}", headers=auth).json()["is_active"] is False
    print("  ok  a plan that has been sold is retired rather than destroyed")

    # A rival cannot book against Acme's client or plan even with real ids.
    stolen = http.post("/api/placements/", headers=rival_auth, json={
        "content_id": ad.id, "client_id": acme_client_id, "plan_id": plan_id,
        "starts_at": now.isoformat(), "ends_at": (now + timedelta(days=5)).isoformat(),
        "targets": [],
    })
    assert stolen.status_code == 404, stolen.text
    print("  ok  a rival cannot book against another tenant's client or plan")

    # Deleting a client leaves the booking standing; billing history is not theirs to erase.
    assert http.delete(f"/api/clients/{acme_client_id}", headers=auth).status_code == 200
    survivor = http.get("/api/placements/", headers=auth).json()[0]
    assert survivor["client"] is None, survivor
    assert survivor["advertiser"] == "BrightMart", survivor
    print("  ok  deleting a client keeps the booking, still named by advertiser")

    # --- branding: a tenant uploads their own logo -------------------------------------
    # This is a commercial product: the report is a document a tenant hands to a paying
    # advertiser, so the masthead has to be theirs, not the workspace name someone typed at
    # signup. Exercised through the real endpoint rather than by setting the column.
    import base64

    # Smallest valid PNG. The endpoint checks the extension and the size, not the pixels.
    PNG = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )

    before = http.get("/api/branding/", headers=auth).json()
    # No branding set: falls back to the workspace name, so a tenant that configures nothing
    # loses nothing.
    assert before["effective_name"] == "Acme", before
    assert before["logo_url"] is None, before

    up = http.post("/api/branding/logo", headers=auth, files={"file": ("logo.png", PNG, "image/png")})
    assert up.status_code == 200, up.text
    assert up.json()["logo_url"], up.json()
    print("  ok  a tenant can upload their own logo")

    # It must land in THAT tenant's own storage folder, beside their media -- not in a
    # shared bucket root where two tenants' marks could collide.
    db.expire_all()
    acme_row = db.query(models.Organization).filter(models.Organization.slug == "acme").one()
    from backend.media_urls import branding_storage_prefix

    assert branding_storage_prefix(acme_row) in acme_row.logo_url, acme_row.logo_url
    print("  ok  the logo is stored under that tenant's own prefix")

    named = http.put("/api/branding/", headers=auth,
                     json={"brand_name": "AdVision Digital Signage", "brand_color": "#0b1437"})
    assert named.status_code == 200, named.text
    assert named.json()["effective_name"] == "AdVision Digital Signage", named.json()

    # A colour that is not one must be refused at the edge: it is written into a PDF colour
    # and into inline style on the dashboard.
    assert http.put("/api/branding/", headers=auth, json={"brand_color": "red; drop"}).status_code == 422
    print("  ok  brand name is applied and a malformed colour is refused")

    # Only images, and no SVG: /uploads is served from the API's own origin and an SVG can
    # carry a script.
    assert http.post("/api/branding/logo", headers=auth,
                     files={"file": ("mark.svg", b"<svg/>", "image/svg+xml")}).status_code == 415
    assert http.post("/api/branding/logo", headers=auth,
                     files={"file": ("big.png", b"x" * (3 * 1024 * 1024), "image/png")}).status_code == 413
    print("  ok  an SVG and an oversized file are both refused")

    # One tenant's branding is not another's.
    theirs = http.get("/api/branding/", headers=rival_auth).json()
    assert theirs["effective_name"] == "Rival", theirs
    assert theirs["logo_url"] is None, theirs
    print("  ok  branding is per tenant, not shared")

    gone = http.delete("/api/branding/logo", headers=auth)
    assert gone.status_code == 200 and gone.json()["logo_url"] is None, gone.text
    # The name survives removing the logo -- they are separate settings.
    assert gone.json()["effective_name"] == "AdVision Digital Signage", gone.json()
    print("  ok  removing the logo keeps the brand name")

    # --- a plan's screen count actually binds ------------------------------------------
    # "5 TVs / 30 days" was decoration: the 30 days were enforced by the player, the 5 TVs
    # were not, so a plan could be oversold without anything objecting.
    small_plan = http.post("/api/tenant-plans/", headers=auth, json={
        "name": "Basic", "duration_days": 30, "max_locations": 2,
        "ad_slots": 1, "price_paise": 500000, "support_tier": "Basic Support",
    }).json()

    fleet = [models.Screen(organization_id=acme.id, name=f"TV {n}", status="online") for n in range(4)]
    db.add_all(fleet); db.commit()

    within = http.post("/api/placements/", headers=auth, json={
        "content_id": ad2.id, "client_id": second.json()["id"], "plan_id": small_plan["id"],
        "starts_at": now.isoformat(),
        "targets": [{"screen_id": fleet[0].id}, {"screen_id": fleet[1].id}],
    })
    assert within.status_code == 201, within.text

    # The third screen is one past what the client paid for.
    third = http.post(f"/api/placements/{within.json()['id']}/targets", headers=auth,
                      json={"screen_id": fleet[2].id})
    assert third.status_code == 409, third.text
    assert "Basic" in third.json()["detail"] and "2 screens" in third.json()["detail"], third.json()
    print("  ok  a screen past the plan's count is refused, naming the plan and the limit")

    # Booking straight past the limit in one request is refused whole, not half-created.
    over = http.post("/api/placements/", headers=auth, json={
        "content_id": ad3.id, "plan_id": small_plan["id"], "advertiser": "Too Big",
        "starts_at": now.isoformat(),
        "targets": [{"screen_id": s.id} for s in fleet],
    })
    assert over.status_code == 409, over.text
    assert not [p for p in http.get("/api/placements/", headers=auth).json() if p["advertiser"] == "Too Big"]
    print("  ok  a booking that breaches its plan is refused whole")

    # THE case that makes counting targets useless: one group target carries four screens.
    # Counting target rows would see "1" and wave it past a two-screen plan.
    big_group = models.ScreenGroup(organization_id=acme.id, name="Mall Wing")
    db.add(big_group); db.commit()
    for screen_row in fleet:
        screen_row.group_id = big_group.id
    db.commit()

    by_group = http.post("/api/placements/", headers=auth, json={
        "content_id": ad4.id, "plan_id": small_plan["id"], "advertiser": "Group Sneak",
        "starts_at": now.isoformat(), "targets": [{"group_id": big_group.id}],
    })
    assert by_group.status_code == 409, (
        "a group of four screens was accepted on a two-screen plan -- the limit is being "
        "counted on target rows rather than on the screens they expand to"
    )
    print("  ok  a group is counted by the screens it expands to, not as one target")

    # A plan of 0 means unlimited, matching how max_ad_slots already reads elsewhere.
    unlimited = http.post("/api/tenant-plans/", headers=auth, json={
        "name": "Unlimited", "duration_days": 30, "max_locations": 0,
        "ad_slots": 1, "price_paise": 100, "support_tier": "Basic",
    })
    assert unlimited.status_code == 422, "max_locations must be at least 1 in the schema"
    print("  ok  a plan cannot be created with a meaningless zero screen count")

    # --- the cap binds on every path that can change the screens ------------------------
    # It was enforced when the SCREENS changed and not when the PLAN did, so the same
    # breach was one PUT away from being legal.
    roomy = http.post("/api/tenant-plans/", headers=auth, json={
        "name": "Roomy", "duration_days": 30, "max_locations": 3,
        "ad_slots": 1, "price_paise": 900000, "support_tier": "Basic Support",
    }).json()
    spread = http.post("/api/placements/", headers=auth, json={
        "content_id": ad5.id, "plan_id": roomy["id"], "advertiser": "Three Screens",
        "starts_at": now.isoformat(),
        "targets": [{"screen_id": fleet[0].id}, {"screen_id": fleet[1].id}, {"screen_id": fleet[2].id}],
    })
    assert spread.status_code == 201, spread.text
    spread_id = spread.json()["id"]

    downgrade = http.put(f"/api/placements/{spread_id}", headers=auth,
                         json={"plan_id": small_plan["id"]})
    assert downgrade.status_code == 409, (
        "a three-screen booking was moved onto a two-screen plan -- the cap is checked when "
        "the screens change but not when the plan does, which is the same breach by the "
        "other door"
    )
    assert http.get(f"/api/placements/{spread_id}", headers=auth) is not None
    still = [p for p in http.get("/api/placements/", headers=auth).json() if p["id"] == spread_id][0]
    assert still["plan"]["id"] == roomy["id"], "the refused plan swap must not have been applied"
    print("  ok  a plan swap onto a smaller plan is refused, not silently applied")

    # The client-ad editor reaches _place/_unplace directly and was the one door with no
    # check at all on it.
    via_editor = http.put(f"/api/content/{ad.id}/client-ad", headers=auth, json={
        "client_name": "Editor Sneak", "plan_id": small_plan["id"],
        "screen_ids": [fleet[0].id, fleet[1].id, fleet[2].id],
    })
    assert via_editor.status_code == 409, (
        "the client-ad editor placed three screens on a two-screen plan -- this path calls "
        "_place directly and skipped the cap entirely"
    )
    print("  ok  the client-ad editor is held to the plan's screen count too")

    # --- under-use is reported, never refused -------------------------------------------
    # The other half of the same number: a client paying for three screens and running on
    # one is owed two, and nothing was saying so. It must never block the sale.
    short = http.post("/api/placements/", headers=auth, json={
        "content_id": make_ad("Only One").id, "plan_id": roomy["id"], "advertiser": "Only One",
        "starts_at": now.isoformat(), "targets": [{"screen_id": fleet[3].id}],
    })
    assert short.status_code == 201, "selling a plan and filling it later must stay possible"
    body = short.json()
    assert body["screens_used"] == 1, body
    assert body["plan_max_locations"] == 3, body
    assert body["screens_unused"] == 2, body
    print("  ok  a part-filled plan is reported as 1 of 3 with 2 unused, and still saves")

    # A booking with no plan has nothing to be short of.
    planless = http.post("/api/placements/", headers=auth, json={
        "content_id": make_ad("No Plan").id, "advertiser": "No Plan", "starts_at": now.isoformat(),
        "ends_at": (now + timedelta(days=5)).isoformat(),
        "targets": [{"screen_id": fleet[0].id}],
    }).json()
    assert planless["plan_max_locations"] == 0 and planless["screens_unused"] == 0, planless
    print("  ok  a booking with no plan reports no allowance and no shortfall")

    # --- a sold booking is never silently repriced --------------------------------------
    # placements.py copies the price once and says so; the client-ad editor copied it again
    # on EVERY edit, so correcting a phone number rebilled the campaign at today's price.
    agreed_ad = make_ad("Agreed Price")
    sold = http.post("/api/placements/", headers=auth, json={
        "content_id": agreed_ad.id, "plan_id": roomy["id"], "advertiser": "Agreed Price",
        "starts_at": now.isoformat(), "price_paise": 111111,
        "targets": [{"screen_id": fleet[0].id}],
    })
    assert sold.status_code == 201, sold.text
    http.put(f"/api/tenant-plans/{roomy['id']}", headers=auth, json={"price_paise": 5000000})
    http.put(f"/api/content/{agreed_ad.id}/client-ad", headers=auth, json={
        "client_name": "Agreed Price", "plan_id": roomy["id"], "client_phone": "9999999999",
    })
    after = [p for p in http.get("/api/placements/", headers=auth).json() if p["id"] == sold.json()["id"]][0]
    assert after["price_paise"] == 111111, (
        f"the agreed price became {after['price_paise']} -- editing a client detail "
        "rebilled the campaign at the plan's current list price"
    )
    print("  ok  editing client details never rebills a campaign already sold")

    # --- upgrading a client to a bigger plan --------------------------------------------
    # The client outgrew Basic. Moving them used to mean hand-editing the booking, and
    # nothing suggested what to move them to.
    outgrown = http.post("/api/placements/", headers=auth, json={
        "content_id": make_ad("Growing Co").id, "plan_id": small_plan["id"], "advertiser": "Growing Co",
        "starts_at": now.isoformat(), "price_paise": small_plan["price_paise"],
        "targets": [{"screen_id": fleet[0].id}, {"screen_id": fleet[1].id}],
    })
    assert outgrown.status_code == 201, outgrown.text
    grow_id = outgrown.json()["id"]
    sold_ends_at = outgrown.json()["effective_ends_at"]

    options = http.get(f"/api/placements/{grow_id}/plan-options", headers=auth)
    assert options.status_code == 200, options.text
    by_name = {o["plan"]["name"]: o for o in options.json()}
    assert by_name["Basic"]["is_current"] is True, by_name["Basic"]
    recommended = [o for o in options.json() if o["recommended"]]
    assert len(recommended) == 1, f"exactly one plan should be recommended, got {len(recommended)}"
    assert recommended[0]["plan"]["name"] == "Roomy", (
        "the cheapest active plan that covers the screens already assigned should be the "
        f"recommendation, got {recommended[0]['plan']['name']}"
    )
    # The plan's CURRENT price, not the figure it was created with -- an earlier check in
    # this file repriced Roomy, and a change hands over today's list price.
    roomy_now = http.get(f"/api/tenant-plans/{roomy['id']}", headers=auth).json()["price_paise"]
    assert by_name["Basic"]["fits"] is True and by_name["Basic"]["recommended"] is False, (
        "the plan they are already on is never the recommendation"
    )
    print("  ok  plan options recommend exactly one plan: the cheapest that fits the screens in use")

    # Changing a plan REPLACES. It is a correction, not a sale: the price becomes the new
    # plan's, nothing is billed on top and no date moves. This used to charge the difference
    # as an extension AND push the end date out by the new plan's length, so a booking put
    # on the right plan was billed old price + difference over a run nobody had agreed.
    was_extensions = len(outgrown.json()["extensions"])
    changed = http.post(f"/api/placements/{grow_id}/change-plan", headers=auth,
                        json={"plan_id": roomy["id"]})
    assert changed.status_code == 200, changed.text
    after = changed.json()
    assert after["plan"]["id"] == roomy["id"], after["plan"]
    assert after["price_paise"] == roomy_now, (
        "the price must BE the new plan's, never the old one plus a difference: "
        f"{after['price_paise']} (plan lists {roomy_now})"
    )
    assert len(after["extensions"]) == was_extensions, (
        "changing a plan must bill no extension -- that is what turned a correction into an upsell"
    )
    assert after["effective_ends_at"] == sold_ends_at, (
        "changing a plan must not move the run; selling more time is a separate action"
    )
    assert after["ends_at"] == outgrown.json()["ends_at"], (
        "the SOLD window must not be rewritten -- an invoice still has to show the original deal"
    )
    assert after["plan_max_locations"] == 3, after
    print("  ok  changing plan replaces the plan and the price, bills nothing and moves no date")

    # And back off a package onto a bargained figure -- the commonest change there is, and
    # the one the endpoint used to refuse outright.
    custom = http.post(f"/api/placements/{grow_id}/change-plan", headers=auth,
                       json={"plan_id": None, "price_paise": 777_00})
    assert custom.status_code == 200, custom.text
    off = custom.json()
    assert off["plan"] is None, off["plan"]
    assert off["price_paise"] == 777_00, off
    assert off["total_price_paise"] == 777_00 + sum(
        e["additional_price_paise"] for e in off["extensions"]
    ), "the negotiated figure replaces the price outright"
    assert len(off["extensions"]) == was_extensions, "moving to a custom price bills nothing either"
    assert off["effective_ends_at"] == sold_ends_at, "and still moves no date"
    print("  ok  a booking moves back off its package onto a negotiated price, nothing added")

    # And the screens must NOT have been re-windowed by any of that. A plan change touches
    # the plan and the price only; the run a client bought moves when time is actually sold
    # (an extension), never as a side effect of correcting the package.
    db.expire_all()
    placed_ends = [
        t.playlist_item_id for t in db.query(models.AdPlacementTarget).filter(
            models.AdPlacementTarget.placement_id == grow_id
        ).all()
    ]
    items = db.query(models.PlaylistItem).filter(models.PlaylistItem.id.in_(placed_ends)).all()
    sold_end_dt = datetime.fromisoformat(sold_ends_at)
    assert items and all(i.end_at <= sold_end_dt for i in items), (
        "a plan change pushed the screens' run past what was sold -- it must change only "
        "the plan and the price"
    )
    print("  ok  a plan change leaves the screens' run exactly as it was sold")

    # --- a custom-priced booking moved onto a package ------------------------------------
    # The package's price simply becomes the booking's. The old code quoted and billed a
    # "difference" against placement.plan -- None for a custom sale, so it fell back to 0
    # and charged the whole package again on top of the figure already agreed.
    basic_now = http.get(f"/api/tenant-plans/{small_plan['id']}", headers=auth).json()["price_paise"]
    negotiated = http.post("/api/placements/", headers=auth, json={
        "content_id": make_ad("Handshake Ltd").id, "advertiser": "Handshake Ltd",
        "starts_at": now.isoformat(), "ends_at": (now + timedelta(days=30)).isoformat(),
        # Deliberately unlike the package price, so "replaced" and "added" cannot look alike.
        "price_paise": 90_00,
        "targets": [{"screen_id": fleet[0].id}],
    })
    assert negotiated.status_code == 201, negotiated.text
    nego_id = negotiated.json()["id"]
    assert negotiated.json()["plan"] is None, "this booking is deliberately on no package"

    moved = http.post(f"/api/placements/{nego_id}/change-plan", headers=auth,
                      json={"plan_id": small_plan["id"]})
    assert moved.status_code == 200, moved.text
    after_move = moved.json()
    assert after_move["price_paise"] == basic_now, (
        "the package's price should replace the negotiated one outright, got "
        f"{after_move['price_paise']} against a list price of {basic_now}"
    )
    assert after_move["extensions"] == [], (
        "moving a custom booking onto a package must bill nothing extra"
    )
    assert after_move["total_price_paise"] == basic_now, (
        f"the total must be the package price, not 9000 + it: {after_move['total_price_paise']}"
    )
    print("  ok  a custom-priced booking moved onto a package takes the package price, nothing added")

    # The figure is bargained, so an explicit price overrules the package's list price.
    haggled = http.post(f"/api/placements/{nego_id}/change-plan", headers=auth,
                        json={"plan_id": small_plan["id"], "price_paise": 555_00})
    assert haggled.status_code == 200, haggled.text
    assert haggled.json()["price_paise"] == 555_00, haggled.json()
    assert haggled.json()["plan"]["id"] == small_plan["id"], haggled.json()
    print("  ok  a negotiated figure overrules the package's list price")

    # A change that would leave the booking over the new plan's cap is refused, the same as
    # any other route to that breach. Three screens on the roomy plan first, so that moving
    # DOWN to the two-screen plan is a real breach and not merely a tight fit.
    assert http.post(f"/api/placements/{grow_id}/targets", headers=auth,
                     json={"screen_id": fleet[2].id}).status_code == 201
    over_cap = http.post(f"/api/placements/{grow_id}/change-plan", headers=auth,
                         json={"plan_id": small_plan["id"]})
    assert over_cap.status_code == 409, (
        "a booking running on three screens was moved onto a two-screen plan through the "
        f"change-plan route, which is the cap breached by a third door: {over_cap.text}"
    )
    print("  ok  a plan change cannot sneak a booking onto a plan that does not cover it")

    # --- the plan a booking is ON is always listed, even after it is retired -------------
    # Active-only was right for what a booking can move TO and wrong for what it is moving
    # FROM: a campaign on a retired package listed no current plan at all, so the change-
    # plan dialog opened with nothing marked current and no way to tell what the client
    # had actually bought.
    retired = http.post("/api/tenant-plans/", headers=auth, json={
        "name": "Legacy Bundle", "duration_days": 20, "max_locations": 4,
        "ad_slots": 2, "price_paise": 400000, "support_tier": "standard",
    }).json()
    on_retired = http.post("/api/placements/", headers=auth, json={
        "content_id": make_ad("Old Deal").id, "plan_id": retired["id"], "advertiser": "Old Deal",
        "starts_at": now.isoformat(), "price_paise": retired["price_paise"],
        "targets": [{"screen_id": fleet[0].id}],
    })
    assert on_retired.status_code == 201, on_retired.text
    legacy_id = on_retired.json()["id"]
    # Sold on, so deleting retires rather than destroys.
    assert http.delete(f"/api/tenant-plans/{retired['id']}", headers=auth).status_code == 200

    listed = http.get(f"/api/placements/{legacy_id}/plan-options", headers=auth).json()
    current = [o for o in listed if o["is_current"]]
    assert len(current) == 1, (
        "a booking on a retired plan listed no current plan at all: " f"{[o['plan']['name'] for o in listed]}"
    )
    assert current[0]["plan"]["name"] == "Legacy Bundle", current[0]
    assert current[0]["plan"]["is_active"] is False, current[0]
    assert not any(o["recommended"] and not o["plan"]["is_active"] for o in listed), (
        "a plan the tenant has stopped selling was recommended"
    )
    print("  ok  the plan a booking is on is listed even after it is retired")

    # --- a booking can come back OFF a package -------------------------------------------
    # Every plan was offered and "no plan" was not, so a booking put on the wrong plan
    # could be moved between plans but never taken off one, and a client renegotiated onto
    # an agreed figure had nowhere to be recorded.
    off = http.post(f"/api/placements/{legacy_id}/change-plan", headers=auth,
                    json={"plan_id": None})
    assert off.status_code == 200, off.text
    assert off.json()["plan"] is None, off.json()
    assert off.json()["plan_max_locations"] == 0, "a custom sale caps no locations"
    assert off.json()["price_paise"] == retired["price_paise"], (
        "naming no price must keep what the client was already billed, not zero it"
    )
    assert not off.json()["extensions"], (
        "moving off a package sells no time and bills nothing"
    )
    print("  ok  a booking can be moved off its package onto a custom price")

    # A bare call naming no plan at all is a mistake, not a silent move to custom.
    assert http.post(f"/api/placements/{legacy_id}/change-plan", headers=auth,
                     json={}).status_code == 422
    print("  ok  a plan change that names no plan at all is still refused")

    print("tenant plans: all checks passed")
finally:
    try:
        if db: db.close()
    except Exception:
        pass
    try:
        engine.dispose()
    except Exception:
        pass
    cur = admin.cursor()
    cur.execute(f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '{SCRATCH}'")
    cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}"')
    admin.close()
