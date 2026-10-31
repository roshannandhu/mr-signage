"""The commercial half of a booking: what was sold, what it cost, what was paid.

Split out of booking_report.py, which had grown into both documents at once -- proof of
delivery with the price, the paid/unpaid pill and the extension charges threaded through
its performance layout. That meant a tenant could not send a client evidence their advert
ran without also restating the terms, and there was no document that was actually an
invoice: the report was being forwarded in place of one.

Everything visual is imported from booking_report rather than restated. Two files drawing
the same letterhead would drift, and the one thing a client must never see is two documents
from the same tenant that do not look like the same company.
"""
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .booking_report import (
    BAND_H,
    FOOTER_H,
    HAIRLINE,
    MARGIN,
    OK_BG,
    OK_INK,
    PAGE_H,
    PAGE_W,
    PLAN_BG,
    PLAN_INK,
    TILE_BG,
    TOTAL_BG,
    WARN_BG,
    WARN_INK,
    _band_color,
    _card,
    _date,
    _fetch_image,
    _markup,
    _money,
    _page_furniture,
    _pill,
    _safe,
    _styles,
)

# How a stored method code reads on paper. Anything unrecognised falls back to the code
# itself rather than to "Other", so a method added in the schema and forgotten here still
# prints something true.
METHOD_LABELS = {
    "cash": "Cash",
    "upi": "UPI",
    "bank_transfer": "Bank transfer",
    "cheque": "Cheque",
    "card": "Card",
    "other": "Other",
}


def invoice_number(report: dict) -> str:
    """A stable, human-quotable reference, derived rather than stored.

    Derived from ids that already exist, so there is no counter to keep and no way for two
    invoices to collide. It is NOT gapless -- a deleted booking leaves a hole -- which is
    fine for a reference a client quotes over the phone and would not be if an auditor
    needed a sequence. That is the upgrade path if it is ever required.
    """
    return f"INV-{report.get('organization_id', 0)}-{report['placement_id']:05d}"


def build_pdf(report: dict) -> bytes:
    """Render a booking report dict (from build_booking_report) as an invoice."""
    style = _styles()
    buffer = BytesIO()

    org = report.get("organization") or {}
    org_name = _safe(org.get("name")) or "Signage network"
    client = report.get("client") or {"name": report["advertiser"]}
    ends_at = report.get("effective_ends_at") or report["ends_at"]
    generated = report["generated_at"].strftime("%d %b %Y %H:%M UTC")
    number = invoice_number(report)

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=BAND_H + 5 * mm, bottomMargin=FOOTER_H + 4 * mm,
        title=f"Invoice {number} - {report['advertiser']}",
        author=org_name,
    )

    logo = _fetch_image(org.get("logo"))
    band = _band_color(org.get("brand_color"))
    furniture = lambda canvas, d: _page_furniture(  # noqa: E731
        canvas, d, org_name, generated, org.get("email"), logo=logo, band=band,
        label="Invoice issued")

    story = []
    content_w = PAGE_W - 2 * MARGIN

    # --- Title and who it is for --------------------------------------------------------
    who = [f"<b>{_markup(client.get('name')) or '-'}</b>"]
    if client.get("client_code"):
        who.append(f"Client ID: {_markup(client['client_code'])}")
    if client.get("email"):
        who.append(_markup(client["email"]))
    if client.get("phone"):
        who.append(_markup(client["phone"]))

    story.append(Table(
        [[Paragraph("INVOICE", style["title"]),
          _card([[Paragraph('<font color="#64748b"><b>BILL TO</b></font>', style["label"])],
                 [Paragraph("<br/>".join(who), style["body"])]],
                [72 * mm], bg=TILE_BG)]],
        colWidths=[content_w - 76 * mm, 76 * mm],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                          ("LEFTPADDING", (0, 0), (-1, -1), 0),
                          ("RIGHTPADDING", (0, 0), (-1, -1), 0)]),
    ))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        f"Invoice <b>{number}</b> &nbsp;|&nbsp; Issued {_date(report['generated_at'])}", style["note"]))
    story.append(Spacer(1, 5 * mm))

    # --- What was sold ------------------------------------------------------------------
    plan = report.get("plan")
    extensions = report.get("extensions") or []
    booking_price = report["price_paise"]

    # One row per thing charged. The booking, then each extension -- which is where an
    # upgrade's price difference lands, so an upgraded campaign reads as what it is: the
    # original deal plus what was added, on one invoice for one client.
    rows = [[Paragraph("<b>DESCRIPTION</b>", style["label"]),
             Paragraph('<para align="center"><b>PERIOD</b></para>', style["label"]),
             Paragraph('<para align="right"><b>AMOUNT</b></para>', style["label"])]]

    plan_line = _markup(plan["name"]) if plan else "Advertising campaign"
    rows.append([
        Paragraph(f"{plan_line}<br/><font size='7' color='#64748b'>"
                  f"{_markup(report['content_name'])} &middot; "
                  f"{len(report.get('per_location') or [])} location(s)</font>", style["body"]),
        Paragraph(f'<para align="center">{_date(report["starts_at"])}<br/>to {_date(report["ends_at"])}</para>',
                  style["body"]),
        Paragraph(f'<para align="right">{_money(booking_price)}</para>', style["body"]),
    ])
    for extension in extensions:
        rows.append([
            Paragraph(f"Extension<br/><font size='7' color='#64748b'>"
                      f"{_markup(extension.get('notes') or 'Additional airtime')}</font>", style["body"]),
            Paragraph(f'<para align="center">{_date(extension["extended_from"])}<br/>'
                      f'to {_date(extension["extended_to"])}</para>', style["body"]),
            Paragraph(f'<para align="right">{_money(extension["additional_price_paise"])}</para>', style["body"]),
        ])

    total = report.get("total_price_paise", booking_price)
    rows.append([
        Paragraph("<b>TOTAL</b>", style["body"]),
        Paragraph("", style["body"]),
        Paragraph(f'<para align="right"><b>{_money(total)}</b></para>', style["body"]),
    ])

    line_items = Table(rows, colWidths=[content_w - 76 * mm, 40 * mm, 36 * mm])
    line_items.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, HAIRLINE),
        ("BACKGROUND", (0, 0), (-1, 0), TILE_BG),
        ("BACKGROUND", (0, -1), (-1, -1), TOTAL_BG),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(line_items)

    # --- Location Schedule Breakdown (for multi-location / custom day campaigns) --------
    target_schedule = report.get("target_schedule") or []
    if target_schedule:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("<b>LOCATION SCHEDULE BREAKDOWN</b>", style["label"]))
        story.append(Spacer(1, 1.5 * mm))
        sched_rows = [[
            Paragraph("<b>LOCATION / VENUE</b>", style["label"]),
            Paragraph('<para align="center"><b>ACTIVE DATES</b></para>', style["label"]),
            Paragraph('<para align="right"><b>AIRTIME</b></para>', style["label"]),
        ]]
        for item in target_schedule:
            sched_rows.append([
                Paragraph(f"<b>{_markup(item['name'])}</b><br/><font size='6.5' color='#64748b'>{_markup(item['location'])}</font>", style["body"]),
                Paragraph(f'<para align="center">{_date(item["starts_at"])} to {_date(item["ends_at"])}</para>', style["body"]),
                Paragraph(f'<para align="right"><b>{item["days"]} day{"s" if item["days"] != 1 else ""}</b></para>', style["body"]),
            ])
        sched_table = Table(sched_rows, colWidths=[content_w - 80 * mm, 48 * mm, 32 * mm])
        sched_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.3, HAIRLINE),
            ("BACKGROUND", (0, 0), (-1, 0), TILE_BG),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 3.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ]))
        story.append(sched_table)

    story.append(Spacer(1, 5 * mm))

    # --- What was paid ------------------------------------------------------------------
    # A list now: a booking carries every receipt taken against it, and a deposit followed
    # by a balance is two. The singular fallback keeps an older report payload rendering.
    payments = report.get("payments")
    if payments is None:
        single = report.get("payment")
        payments = [single] if single else []
    # Read off the report, which derives all three from one place (placements.settlement),
    # rather than subtracting them again here. "Part paid" when the money received does not
    # cover the total -- which is exactly what an upgrade or an extension does until it is
    # settled. Printing "Paid" above a line reading "Outstanding 5,000" is the kind of
    # contradiction a client rings up about.
    #
    # The fallbacks keep an older report payload rendering rather than crashing on a key.
    received = report.get("amount_paid_paise")
    if received is None:
        received = sum(p.get("amount_paise") or 0 for p in payments)
    outstanding = report.get("balance_due_paise")
    if outstanding is None:
        outstanding = max(0, (total or 0) - received)
    status = report.get("payment_status") or (
        "paid" if report.get("is_paid") and outstanding <= 0 else
        "part_paid" if received > 0 else "unpaid"
    )
    if status == "paid":
        status_label, status_ink, status_bg = "Paid", OK_INK, OK_BG
    elif status == "part_paid":
        status_label, status_ink, status_bg = "Part paid", WARN_INK, WARN_BG
    else:
        status_label, status_ink, status_bg = "Unpaid", PLAN_INK, PLAN_BG

    settled_rows = [[Paragraph("Status", style["body"]),
                     _pill(style, status_label, status_ink, status_bg)]]
    # One row per receipt, in the order the money arrived, each carrying the method and the
    # reference under its date. A client checking this against their own bank statement is
    # looking for a UTR and a date, and a single collapsed "amount received" gives them
    # neither -- it also cannot be told apart from a part payment of the same size.
    for entry in payments:
        method = METHOD_LABELS.get(entry.get("method"), _markup(entry.get("method")))
        detail = f"{method} &middot; {_markup(entry['reference'])}" if entry.get("reference") else method
        settled_rows.append([
            Paragraph(f'{_date(entry.get("paid_at"))}<br/><font size="7">{detail}</font>', style["body"]),
            Paragraph(f'<para align="right">{_money(entry.get("amount_paise"))}</para>', style["body"]),
        ])
    # Stated only when it is not simply the row above it, so a booking settled in one
    # transfer does not print the same figure twice.
    if len(payments) > 1:
        settled_rows.append([Paragraph("Total received", style["body"]),
                             Paragraph(f'<para align="right">{_money(received)}</para>', style["body"])])
    # An amount received that does not match the total is the single most useful thing this
    # document can point at, so it is stated rather than left to be worked out.
    if payments and outstanding > 0:
        settled_rows.append([Paragraph("<b>Outstanding</b>", style["body"]),
                             Paragraph(f'<para align="right"><b>{_money(outstanding)}</b></para>', style["body"])])

    settled = Table(settled_rows, colWidths=[36 * mm, 48 * mm])
    settled.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, HAIRLINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))

    terms = [[Paragraph('<font color="#6d28d9"><b>CAMPAIGN</b></font>', style["label"])],
             [Paragraph(f"Runs {_date(report['starts_at'])} to {_date(ends_at)}", style["body"])]]
    if plan:
        terms.append([Paragraph(
            f"{_markup(plan['name'])} &middot; {plan['duration_days']} days &middot; "
            f"up to {plan['max_locations']} location(s)", style["note"])])
    if extensions:
        terms.append([Paragraph(f"{len(extensions)} extension(s) sold against this booking.", style["note"])])

    story.append(Table(
        [[settled, _card(terms, [content_w - 92 * mm], bg=PLAN_BG,
                         border=colors.HexColor("#ddd6fe"))]],
        colWidths=[88 * mm, content_w - 88 * mm],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                          ("LEFTPADDING", (0, 0), (-1, -1), 0),
                          ("RIGHTPADDING", (0, 0), (-1, -1), 0)]),
    ))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        "This invoice covers the airtime sold for this campaign. Proof of delivery -- where "
        "the advert ran and how many times it played -- is issued separately as the "
        "playback report.", style["note"]))

    doc.build(story, onFirstPage=furniture, onLaterPages=furniture)
    return buffer.getvalue()
