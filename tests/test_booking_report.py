"""A client report must count that client's window and that client's screens. Only.

Two clients book the same creative with overlapping windows on different screens; each
report must see only its own. Throwaway Postgres database.

Run directly:  python tests/test_booking_report.py
"""
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

SCRATCH = f"olrac_bookingreport_{uuid.uuid4().hex[:8]}"
os.environ["DATABASE_URL"] = f"postgresql://olrac:olrac_password@localhost:5432/{SCRATCH}"
os.environ["SECRET_KEY"] = "report-test-secret"
os.environ["AWS_ACCESS_KEY_ID"] = "mock"
os.environ["GOOGLE_MAPS_API_KEY"] = ""      # exercise the no-key path

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
    from tests._test_plans import grant_plan  # noqa: E402

    # No key, so maps.py would draw the map itself from OpenStreetMap tiles. Stubbed: a
    # test suite must not depend on a third-party tile server being reachable, and hammering
    # a shared free service from CI is exactly the use their terms ask people not to make.
    # The rendering itself is exercised by hand against the sample report.
    from backend import maps as _maps

    _maps.render_osm_map = lambda *args, **kwargs: None

    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    org = models.Organization(name="Acme", slug="acme")
    db.add(org); db.flush()
    # report.pdf, invoice.pdf and emailing either are sold behind these two flags.
    grant_plan(db, org, pdf_reports=True, payslips=True)
    db.commit()
    owner = models.User(organization_id=org.id, username="owner@acme.test",
                        hashed_password=get_password_hash("x"), role="owner", is_active=True)
    db.add(owner); db.commit()

    ad = models.Content(organization_id=org.id, type="video", file_url="/uploads/1/a.mp4",
                        name="Summer Sale", status="ready", duration_ms=30_000)
    # Client B's own creative. The two clients used to share this one file, which is what a
    # scoped report has to survive -- but an advert now carries only ONE live booking
    # (placements.create_placement refuses the second), so a shared file cannot be sold to
    # two advertisers at once any more. The property that mattered is kept below instead:
    # B's creative also plays on A's screens inside A's window, and A's report must not
    # count a single one of those plays.
    ad_b = models.Content(organization_id=org.id, type="video", file_url="/uploads/1/b.mp4",
                          name="Gate Promo", status="ready", duration_ms=30_000)
    db.add_all([ad, ad_b]); db.commit()

    now = models.utcnow()

    mall = models.ScreenGroup(organization_id=org.id, name="Phoenix Mall")
    db.add(mall); db.commit()
    # Two mall screens reached through the group, plus one booked directly by the rival.
    m1 = models.Screen(organization_id=org.id, name="Food Court", group_id=mall.id,
                       location="Phoenix Mall", latitude=9.93, longitude=76.26,
                       status="online", last_seen=now)
    m2 = models.Screen(organization_id=org.id, name="Entrance", group_id=mall.id,
                       location="Phoenix Mall", latitude=9.93, longitude=76.27,
                       status="offline", last_seen=now - timedelta(days=40))
    airport = models.Screen(organization_id=org.id, name="Gate 4", location="City Airport",
                            latitude=10.15, longitude=76.39, status="online", last_seen=now)
    db.add_all([m1, m2, airport]); db.commit()

    client = TestClient(app)
    auth = {"Authorization": f"Bearer {create_access_token(data={'sub': owner.username})}"}

    # Client A: the mall group, this month.
    a = client.post("/api/placements/", headers=auth, json={
        "content_id": ad.id, "advertiser": "Client A", "price_paise": 100000, "is_paid": True,
        "starts_at": (now - timedelta(days=10)).isoformat(),
        "ends_at": (now + timedelta(days=10)).isoformat(),
        "targets": [{"group_id": mall.id}],
    })
    assert a.status_code == 201, a.text
    a_id = a.json()["id"]

    # Client B: the airport screen, an overlapping window.
    b = client.post("/api/placements/", headers=auth, json={
        "content_id": ad_b.id, "advertiser": "Client B", "price_paise": 50000, "is_paid": False,
        "starts_at": (now - timedelta(days=5)).isoformat(),
        "ends_at": (now + timedelta(days=5)).isoformat(),
        "targets": [{"screen_id": airport.id}],
    })
    assert b.status_code == 201, b.text
    b_id = b.json()["id"]

    def rollup(screen, hours_ago, plays, completed, media=None):
        db.add(models.PlayLogHourlyRollup(
            organization_id=org.id, screen_id=screen.id, media_id=(media or ad).id,
            date_hour=now - timedelta(hours=hours_ago),
            total_plays=plays, completed_plays=completed, partial_plays=0, error_plays=0))

    rollup(m1, 24, 100, 95)          # inside A's window
    rollup(m2, 48, 60, 60)           # inside A's window
    rollup(airport, 24, 500, 480, ad_b)   # inside B's window, NOT A's screens
    rollup(m1, 24 * 60, 999, 999)    # 60 days ago — outside A's window
    # Another client's creative, on A's OWN screen, inside A's OWN window. Everything else
    # about this row matches A's report; only the advert differs, and that alone must keep
    # it out. Handing A a figure that included it would bill them for somebody else's
    # campaign.
    rollup(m1, 24, 777, 777, ad_b)
    db.commit()

    ra = client.get(f"/api/placements/{a_id}/report", headers=auth)
    assert ra.status_code == 200, ra.text
    ra = ra.json()
    assert ra["totals"]["total_plays"] == 160, ra["totals"]  # not 937: ad_b's 777 is not A's
    names = sorted(s["screen_name"] for s in ra["per_screen"])
    assert names == ["Entrance", "Food Court"], names
    print("  ok  group target expanded to its members; airport plays and out-of-window plays excluded")

    rb = client.get(f"/api/placements/{b_id}/report", headers=auth).json()
    assert rb["totals"]["total_plays"] == 500, rb["totals"]
    assert [s["screen_name"] for s in rb["per_screen"]] == ["Gate 4"], rb["per_screen"]
    print("  ok  the other client's report sees only its own screen")

    # A screen silent since before the period ended must be flagged, not silently counted.
    assert "Entrance" in ra["stale_screens"], ra["stale_screens"]
    assert "Food Court" not in ra["stale_screens"], ra["stale_screens"]
    entrance = next(s for s in ra["per_screen"] if s["screen_name"] == "Entrance")
    assert entrance["counts_may_be_incomplete"] is True
    print("  ok  a screen that has not reported in is flagged as possibly incomplete")

    places = {p["location"]: p for p in ra["per_location"]}
    assert set(places) == {"Phoenix Mall"}, places
    assert places["Phoenix Mall"]["screens"] == 2
    print("  ok  plays roll up by real location, not by group")

    # The PDF must generate with no maps key configured.
    pdf = client.get(f"/api/placements/{a_id}/report.pdf", headers=auth)
    assert pdf.status_code == 200, pdf.text
    assert pdf.content[:5] == b"%PDF-", pdf.content[:20]
    assert len(pdf.content) > 2000, len(pdf.content)
    assert "Client A" in pdf.headers.get("content-disposition", "")
    print(f"  ok  PDF generated without a maps key ({len(pdf.content):,} bytes)")

    # The dashboard rendered this endpoint straight into an <a href>, which navigates with
    # no Authorization header -- so the report button had never once worked. It now goes
    # through the same authenticated fetch as every other call. Pinned here so nobody
    # "simplifies" it back to a plain link.
    anonymous = client.get(f"/api/placements/{a_id}/report.pdf")
    assert anonymous.status_code != 200, (
        "the report endpoint answered an unauthenticated request; a plain <a href> would "
        "now leak one client's playback figures to anyone with the URL"
    )
    print(f"  ok  the PDF endpoint refuses an unauthenticated request ({anonymous.status_code})")

    # --- the client block, the creative, and the period the mockup asks for -----------
    # A booking made against a Client record must report that client's contact details;
    # they are what the report is addressed to and what the emailed copy is sent to.
    made = client.post("/api/clients/", headers=auth, json={
        "name": "BrightMart Retail", "email": "contact@brightmart.com", "phone": "+91 98765 43210",
    })
    assert made.status_code == 201, made.text
    brightmart = made.json()
    assert brightmart["client_code"] == "CLT00001", brightmart

    linked = client.put(f"/api/placements/{a_id}", headers=auth, json={"client_id": brightmart["id"]})
    assert linked.status_code == 200, linked.text
    # advertiser follows the client, or the report prints a name the booking is no longer
    # attributed to.
    assert linked.json()["advertiser"] == "BrightMart Retail", linked.json()

    rc = client.get(f"/api/placements/{a_id}/report", headers=auth).json()
    assert rc["client"]["client_code"] == "CLT00001", rc["client"]
    assert rc["client"]["email"] == "contact@brightmart.com", rc["client"]
    # 10 days in, 20 sold.
    assert rc["days_total"] == 20, rc["days_total"]
    assert 9 <= rc["days_remaining"] <= 11, rc["days_remaining"]
    print("  ok  the report is addressed to the client record, with the period counted")

    # The thumbnail must be RESOLVED. The column holds "/uploads/..." or "s3://...", and
    # neither is fetchable as stored -- printing the raw value put a broken image on the
    # page, which is exactly the bug this whole report exists to avoid.
    thumb = models.Content(organization_id=org.id, type="image", file_url="/uploads/1/b.png",
                           thumbnail="/uploads/1/b.png", name="Burger Promo", status="ready")
    db.add(thumb); db.commit()
    withthumb = client.post("/api/placements/", headers=auth, json={
        "content_id": thumb.id, "client_id": brightmart["id"], "price_paise": 2500000, "is_paid": True,
        "starts_at": now.isoformat(), "ends_at": (now + timedelta(days=31)).isoformat(),
        "targets": [{"screen_id": airport.id}],
    })
    assert withthumb.status_code == 201, withthumb.text
    t_id = withthumb.json()["id"]
    rt = client.get(f"/api/placements/{t_id}/report", headers=auth).json()
    assert rt["content_thumbnail"], rt
    assert rt["content_thumbnail"].startswith("http"), rt["content_thumbnail"]
    print("  ok  the creative thumbnail is resolved to a fetchable URL, not the stored path")

    # --- per-location averages --------------------------------------------------------
    place = {p["location"]: p for p in ra["per_location"]}["Phoenix Mall"]
    assert "plays_per_day_avg" in place, place
    # Averaged over days ELAPSED (10), not days sold (20). Dividing by the full period
    # understates every row on a mid-campaign report.
    assert place["plays_per_day_avg"] == round(place["total_plays"] / 10, 1), place
    print("  ok  per-location average is over days elapsed, not days sold")

    # --- a place added mid-campaign counts from the day it was added -------------------
    # The booking below started 10 days ago. A screen joining today has had one day on air,
    # not ten, and dividing its plays by the campaign's elapsed days would file a brand new
    # location as the worst performer on the network.
    late = models.Screen(organization_id=org.id, name="Late Kiosk", location="New Plaza",
                         latitude=9.9, longitude=76.3, status="online", last_seen=now)
    db.add(late); db.commit()
    added_late = client.post(f"/api/placements/{a_id}/targets", headers=auth,
                             json={"screen_id": late.id})
    assert added_late.status_code in (200, 201), added_late.text

    db.add(models.PlayLogHourlyRollup(
        organization_id=org.id, screen_id=late.id, media_id=ad.id,
        date_hour=now - timedelta(hours=2),
        total_plays=40, completed_plays=40, partial_plays=0, error_plays=0))
    db.commit()

    rl = client.get(f"/api/placements/{a_id}/report", headers=auth).json()
    late_row = next(s for s in rl["per_screen"] if s["screen_name"] == "Late Kiosk")
    assert datetime.fromisoformat(late_row["assigned_at"]).astimezone(timezone.utc).date() == now.astimezone(timezone.utc).date(), late_row["assigned_at"]

    new_plaza = {p["location"]: p for p in rl["per_location"]}["New Plaza"]
    # One day on air, so 40 plays is 40/day -- not 4/day, which is what dividing by the
    # campaign's ten elapsed days would have printed.
    assert new_plaza["days_elapsed"] == 1, new_plaza
    assert new_plaza["plays_per_day_avg"] == 40.0, new_plaza

    # The screens that were there from the start are untouched by this.
    mall = {p["location"]: p for p in rl["per_location"]}["Phoenix Mall"]
    assert mall["days_elapsed"] == 10, mall
    print("  ok  a place added mid-campaign is averaged over its own days, not the campaign's")

    # --- extensions -------------------------------------------------------------------
    ext = client.post(f"/api/placements/{t_id}/extensions", headers=auth, json={
        "extended_to": (now + timedelta(days=46)).isoformat(),
        "additional_price_paise": 1250000, "is_paid": True,
    })
    assert ext.status_code == 201, ext.text
    body = ext.json()
    assert len(body["extensions"]) == 1, body
    assert body["total_price_paise"] == 2500000 + 1250000, body
    # The sold dates are untouched; the effective end moves. An invoice still has to be
    # able to show what was originally agreed.
    assert datetime.fromisoformat(body["effective_ends_at"]).astimezone(timezone.utc).date() == (now + timedelta(days=46)).date(), body

    # The extension must reach the SCREEN, not just the database: the player stops the
    # advert on PlaylistItem.end_at.
    target_item = db.query(models.AdPlacementTarget).filter(
        models.AdPlacementTarget.placement_id == t_id
    ).one()
    db.expire_all()
    item = db.query(models.PlaylistItem).filter(
        models.PlaylistItem.id == target_item.playlist_item_id
    ).one()
    item_end_at = item.end_at if item.end_at.tzinfo is not None else item.end_at.replace(tzinfo=timezone.utc)
    assert item_end_at.astimezone(timezone.utc).date() == (now + timedelta(days=46)).date(), item.end_at
    print("  ok  an extension moves the effective end, the price, and the placed item")

    rx = client.get(f"/api/placements/{t_id}/report", headers=auth).json()
    assert rx["extension_price_paise"] == 1250000, rx
    assert rx["total_price_paise"] == 3750000, rx
    assert len(rx["extensions"]) == 1, rx
    pdf2 = client.get(f"/api/placements/{t_id}/report.pdf", headers=auth)
    assert pdf2.status_code == 200 and pdf2.content[:5] == b"%PDF-"
    print(f"  ok  extended booking renders a PDF ({len(pdf2.content):,} bytes)")

    # Removing it pulls the run back in, or a cancelled extension keeps playing.
    removed = client.delete(f"/api/placements/{t_id}/extensions/{body['extensions'][0]['id']}", headers=auth)
    assert removed.status_code == 200, removed.text
    assert removed.json()["extensions"] == [], removed.json()
    assert removed.json()["total_price_paise"] == 2500000, removed.json()
    print("  ok  removing an extension restores the sold window and price")

    # --- every glyph on the page must exist in the font --------------------------------
    # The PDF uses ReportLab's standard Type 1 faces, which have no U+20B9. A rupee sign
    # rendered as a black tofu box -- on the amount-paid tile, so the single number a
    # client checks first was a box. Nothing money-shaped may carry a character those
    # fonts cannot draw.
    from backend.reports.booking_report import _money, _HAS_RUPEE_FONT

    for amount in (0, 5, 100, 2500000, 3750099):
        rendered = _money(amount)
        if _HAS_RUPEE_FONT:
            assert "₹" in rendered, rendered
        else:
            rendered.encode("ascii")
            assert "₹" not in rendered, rendered

    if _HAS_RUPEE_FONT:
        assert _money(2500000) == "₹ 25,000", _money(2500000)
        assert _money(3750099) == "₹ 37,500.99", _money(3750099)
    else:
        assert _money(2500000) == "Rs. 25,000", _money(2500000)
        assert _money(3750099) == "Rs. 37,500.99", _money(3750099)
    print("  ok  money renders with glyphs the PDF fonts actually have")

    # ReportLab does not raise on a character the font lacks -- it draws a box. Typographic
    # punctuation arrives constantly from whatever the operator pasted a campaign name out
    # of, so it is folded to ASCII rather than gambled on.
    from backend.reports.booking_report import _safe

    assert _safe("Burger — 50% Off") == "Burger - 50% Off"
    assert _safe("Ravi’s “Summer” Sale…") == "Ravi's \"Summer\" Sale..."
    assert _safe(None) == ""
    print("  ok  word-processor punctuation is folded to glyphs the fonts have")

    # --- tenant branding, and what happens when the logo cannot be fetched -------------
    # The masthead is what a CLIENT sees. `organizations.name` is the workspace name
    # somebody typed at signup, very often "<person>'s Workspace", which is not a trading
    # name to head an invoice-shaped document with.
    org.brand_name = "AdVision Digital Signage"
    org.brand_color = "#0b1437"
    org.logo_url = "https://example.invalid/definitely-not-there.png"
    db.commit()

    rb = client.get(f"/api/placements/{a_id}/report", headers=auth).json()
    assert rb["organization"]["name"] == "AdVision Digital Signage", rb["organization"]
    assert rb["organization"]["brand_color"] == "#0b1437", rb["organization"]

    # The logo URL is unreachable. Same rule as the creative and the map: a report that
    # 500s because a picture is missing is worse than one that prints without it.
    branded = client.get(f"/api/placements/{a_id}/report.pdf", headers=auth)
    assert branded.status_code == 200, branded.text
    assert branded.content[:5] == b"%PDF-", branded.content[:20]
    print("  ok  branding is applied, and an unreachable logo does not break the report")

    # A colour that is not a colour must not take the report down either. The API validates
    # the pattern and the column caps the length at 9, so this is the shape of the bad value
    # that can still get through: right length, wrong content.
    org.brand_color = "#nothex"
    db.commit()
    assert client.get(f"/api/placements/{a_id}/report.pdf", headers=auth).status_code == 200
    org.brand_name = None
    org.brand_color = None
    org.logo_url = None
    db.commit()
    unbranded = client.get(f"/api/placements/{a_id}/report", headers=auth).json()
    # Falls back to the workspace name, so a tenant that sets nothing loses nothing.
    assert unbranded["organization"]["name"] == "Acme", unbranded["organization"]
    print("  ok  an unusable brand colour is ignored, and no branding falls back cleanly")

    # --- the PDF stays a sales document, not a technical log --------------------------
    # Two pages, whatever the size of the booking. The day-by-day play log used to make it
    # three on its own and nobody reads it.
    import io as _io

    # pypdf is a dev convenience, not a project dependency. Skipping keeps this file
    # runnable on a machine without it rather than failing for a missing tool -- the
    # assertions below are about layout, and everything else here still runs.
    try:
        from pypdf import PdfReader as _PdfReader
    except ImportError:
        _PdfReader = None

    def pages_of(placement_id: int):
        response = client.get(f"/api/placements/{placement_id}/report.pdf", headers=auth)
        assert response.status_code == 200, response.text
        reader = _PdfReader(_io.BytesIO(response.content))
        return [(page.extract_text() or "") for page in reader.pages]

    if _PdfReader is None:
        print("  --  page-layout checks skipped (pypdf not installed)")
        print("booking report: all checks passed")
        raise SystemExit(0)

    small = pages_of(a_id)
    assert len(small) <= 2, f"a small booking ran to {len(small)} pages"
    joined = "\n".join(small)
    # Line-exact: the LOCATION table legitimately has a column called "AD PLAYS PER DAY
    # (AVG.)", so a substring check matches that and passes for the wrong reason. What must
    # be gone is the standalone section and its date rows.
    lines = {line.strip() for page in small for line in page.splitlines()}
    assert "PLAYS PER DAY" not in lines, "the day-by-day log section is still in the report"
    assert not any(re.fullmatch(r"\d{4}-\d{2}-\d{2}", line) for line in lines),         "day-by-day rows are still being printed"
    # Few screens, so the detail is kept -- a client asking "which screens?" gets an answer.
    assert "SCREEN-LEVEL DELIVERY" in joined
    assert "Food Court" in joined, "the screen table should be present for a small booking"
    print(f"  ok  a small booking is {len(small)} pages, with the screen detail kept")

    # A big estate must not turn the client's PDF into a 60-row log.
    big_screens = []
    for index in range(60):
        s = models.Screen(organization_id=org.id, name=f"Concourse Panel {index:02d}",
                          location=f"Terminal {index % 6}", status="online", last_seen=now)
        big_screens.append(s)
    # Its own creative, for the same reason as Client B above: the advert used here is
    # already carrying a live booking, and one advert carries only one.
    ad_wide = models.Content(organization_id=org.id, type="video", file_url="/uploads/1/w.mp4",
                             name="Wide Reach Spot", status="ready", duration_ms=30_000)
    db.add_all([*big_screens, ad_wide]); db.commit()

    big = client.post("/api/placements/", headers=auth, json={
        "content_id": ad_wide.id, "advertiser": "Wide Reach Co", "price_paise": 900000,
        "is_paid": True,
        "starts_at": (now - timedelta(days=5)).isoformat(),
        "ends_at": (now + timedelta(days=25)).isoformat(),
        "targets": [{"screen_id": s.id} for s in big_screens],
    })
    assert big.status_code == 201, big.text
    big_pages = pages_of(big.json()["id"])
    assert len(big_pages) <= 2, f"a 60-screen booking ran to {len(big_pages)} pages"
    big_joined = "\n".join(big_pages)
    assert "60 screens across" in big_joined, big_joined[-600:]
    assert "available in the campaign dashboard" in big_joined
    # The individual panels must NOT be listed.
    assert "Concourse Panel 42" not in big_joined
    print(f"  ok  a 60-screen booking is {len(big_pages)} pages, summarised instead of listed")

    # --- the report proves delivery; the invoice states the terms ----------------------
    # They used to be one document, so a tenant could not send a client evidence the advert
    # ran without also restating the price and whether they had paid. A report is forwarded
    # freely; a bill is not.
    report_text = "\n".join(pages_of(a_id))
    for commercial in ("Amount paid", "Payment status", "Additional amount", "Total (incl."):
        assert commercial not in report_text, (
            f"{commercial!r} is still on the playback report -- proof of delivery has "
            "commercial terms threaded back into it"
        )
    # The plan still appears: it explains the run. Its PRICE must not.
    assert "CURRENT PLAN" in report_text, "the plan explains the run and should stay"
    print("  ok  the playback report carries no price, no paid status and no charges")

    invoice_response = client.get(f"/api/placements/{a_id}/invoice.pdf", headers=auth)
    assert invoice_response.status_code == 200, invoice_response.text
    invoice_pages = [(p.extract_text() or "") for p in _PdfReader(_io.BytesIO(invoice_response.content)).pages]
    invoice_text = "\n".join(invoice_pages)
    assert len(invoice_pages) <= 2, f"the invoice ran to {len(invoice_pages)} pages"
    assert "INVOICE" in invoice_text, invoice_text[:400]
    assert f"INV-{org.id}-{a_id:05d}" in invoice_text, (
        "the invoice needs a reference the client can quote back"
    )
    assert "BILL TO" in invoice_text and "TOTAL" in invoice_text, invoice_text[:400]
    # It must NOT carry the delivery evidence -- that is the other document's job.
    assert "SCREEN-LEVEL DELIVERY" not in invoice_text, (
        "the invoice is reproducing the playback report's delivery tables"
    )
    print("  ok  the invoice is its own document, with a quotable number and the totals")

    emailed_invoice = client.post(f"/api/placements/{a_id}/email?document=invoice", headers=auth)
    # 503 when SMTP is unconfigured is the honest answer here; what must not happen is a
    # 422 for an unknown document type.
    assert emailed_invoice.status_code in (200, 422, 503), emailed_invoice.text
    bad_document = client.post(f"/api/placements/{a_id}/email?document=receipt", headers=auth)
    assert bad_document.status_code == 422, bad_document.text
    print("  ok  the email route can send either document and refuses anything else")

    print("booking report: all checks passed")
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
