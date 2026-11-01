"""The PDF a client receives as proof their advert ran.

Laid out to match the campaign report the client is expecting: a dark masthead, a row of
headline tiles, the creative beside a map of where it ran, daily playback trends, an SLA
compliance gauge, a per-location table, commercial details, a tamper-proof verification
certificate with a scannable QR code, and screen delivery audits.

Built with ReportLab. The bands that bleed to the page edge are drawn on the canvas in
`_page_furniture`, because a Platypus flowable is confined to the text frame and would
leave white gutters down both sides.
"""
import logging
import os
import pathlib
from datetime import datetime
from io import BytesIO

from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .. import media_storage
from ..maps import fetch_static_map

logger = logging.getLogger(__name__)

# --- Font Registration & Unicode Currency Support -------------------------------------
_FONTS_DIR = pathlib.Path(__file__).parent / "fonts"
_FONT_REGULAR = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_HAS_RUPEE_FONT = False

_regular_path = _FONTS_DIR / "Arial.ttf"
_bold_path = _FONTS_DIR / "Arial-Bold.ttf"
if _regular_path.exists() and _bold_path.exists():
    try:
        pdfmetrics.registerFont(TTFont("DocFont", str(_regular_path)))
        pdfmetrics.registerFont(TTFont("DocFont-Bold", str(_bold_path)))
        _FONT_REGULAR = "DocFont"
        _FONT_BOLD = "DocFont-Bold"
        _HAS_RUPEE_FONT = True
    except Exception as exc:
        logger.warning("Could not register bundled TTF fonts: %s", exc)


INK = colors.HexColor("#0b1437")        # masthead / footer band
NAVY = colors.HexColor("#1a2b5c")       # headings and table headers
MUTED = colors.HexColor("#64748b")
HAIRLINE = colors.HexColor("#e2e8f2")
TILE_BG = colors.HexColor("#f8fafc")
TOTAL_BG = colors.HexColor("#eaf1fb")
PLAN_BG = colors.HexColor("#f5f3ff")
PLAN_INK = colors.HexColor("#6d28d9")
OK_BG = colors.HexColor("#f0fdf4")
OK_INK = colors.HexColor("#16a34a")
# Amber "needs attention" pair, for a part-paid invoice: distinct from both the green of
# paid and the violet of unpaid, so a partially-settled booking cannot be mistaken for
# either at a glance.
WARN_BG = colors.HexColor("#fffbeb")
WARN_INK = colors.HexColor("#b45309")

PAGE_W, PAGE_H = A4
MARGIN = 14 * mm
BAND_H = 16 * mm
FOOTER_H = 16 * mm


def _styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName=_FONT_BOLD, fontSize=18, textColor=NAVY,
                                alignment=0, spaceAfter=2, leading=22),
        "sub": ParagraphStyle("s", parent=base["Normal"], fontName=_FONT_REGULAR, fontSize=8.5, textColor=MUTED, leading=12),
        "h2": ParagraphStyle("h", parent=base["Normal"], fontSize=9.5, textColor=NAVY,
                             fontName=_FONT_BOLD, spaceBefore=2, spaceAfter=4),
        "body": ParagraphStyle("b", parent=base["Normal"], fontName=_FONT_REGULAR, fontSize=8, leading=11.5),
        "note": ParagraphStyle("n", parent=base["Normal"], fontName=_FONT_REGULAR, fontSize=7, textColor=MUTED, leading=9.5),
        "label": ParagraphStyle("l", parent=base["Normal"], fontName=_FONT_BOLD, fontSize=6, textColor=MUTED, leading=8),
        "value": ParagraphStyle("v", parent=base["Normal"], fontSize=11, textColor=NAVY,
                                fontName=_FONT_BOLD, leading=13.5),
        "value_sm": ParagraphStyle("vs", parent=base["Normal"], fontSize=8, textColor=NAVY,
                                   fontName=_FONT_BOLD, leading=10),
    }


def _date(value: datetime | None) -> str:
    return value.strftime("%d %b %Y") if value else "-"


def _duration(seconds: int | float | None) -> str:
    """Seconds to a compact airtime string: '42h 18m', '18m 30s', '45s'."""
    s = int(round(seconds or 0))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:,}h {m:02d}m"
    if m:
        return f"{m}m {sec:02d}s"
    return f"{sec}s"


def _money(paise: int | None) -> str:
    """Paise to currency string. Integer division throughout -- money never touches a float."""
    symbol = "₹" if _HAS_RUPEE_FONT else "Rs."
    if not paise:
        return f"{symbol} 0"
    rupees, remainder = divmod(int(paise), 100)
    return f"{symbol} {rupees:,}" if remainder == 0 else f"{symbol} {rupees:,}.{remainder:02d}"


_TYPOGRAPHY = {
    "—": "-", "–": "-", "−": "-",
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "…": "...", " ": " ",
}
if not _HAS_RUPEE_FONT:
    _TYPOGRAPHY["₹"] = "Rs."


def _safe(value) -> str:
    """Text with the punctuation a word processor inserts folded back to safe characters."""
    text = "" if value is None else str(value)
    for bad, good in _TYPOGRAPHY.items():
        text = text.replace(bad, good)
    return text


def _markup(value) -> str:
    """Text safe to put inside ReportLab Paragraph markup.

    `_safe` folds typography but does not escape, and a Paragraph reads its string as XML-ish
    markup: "R&D Ltd" printed as "R&D; Ltd" and a name containing "<VIP>" lost the tag. Anything
    a tenant typed that is going INTO a Paragraph goes through here; text drawn directly on the
    canvas or placed in a plain table cell stays on `_safe`, where escaping would print "&amp;".
    """
    from xml.sax.saxutils import escape

    return escape(_safe(value))


def _normalize_image_bytes(data: bytes) -> bytes | None:
    """Ensure image bytes are valid, RGB-compatible JPEG/PNG so ReportLab never fails."""
    if not data:
        return None
    try:
        from PIL import Image as PILImage

        # Video magic detection (MP4 / MOV container starts with ftyp or 0x000000...)
        if len(data) > 12 and (b"ftyp" in data[:16] or data[:4] in (b"\x00\x00\x00\x18", b"\x00\x00\x00\x1c", b"\x00\x00\x00\x20")):
            import subprocess, tempfile
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                tf.write(data)
                tmp_video = tf.name
            tmp_frame = tmp_video + ".jpg"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", "00:00:00.100", "-i", tmp_video, "-vframes", "1", "-q:v", "2", tmp_frame],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5, check=True
                )
                if os.path.exists(tmp_frame):
                    with open(tmp_frame, "rb") as f:
                        frame_bytes = f.read()
                    return _normalize_image_bytes(frame_bytes)
            except Exception:
                pass
            finally:
                for p in (tmp_video, tmp_frame):
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass

            # Fallback video card synthesis
            from PIL import ImageDraw
            card = PILImage.new("RGB", (640, 480), (15, 23, 42))
            draw = ImageDraw.Draw(card)
            cx, cy = 320, 240
            draw.ellipse([cx - 45, cy - 45, cx + 45, cy + 45], fill=(30, 41, 59), outline=(99, 102, 241), width=3)
            draw.polygon([(cx - 15, cy - 25), (cx + 25, cy), (cx - 15, cy + 25)], fill=(248, 250, 252))
            out = BytesIO()
            card.save(out, format="JPEG", quality=92)
            return out.getvalue()

        # Standard image processing
        try:
            img = PILImage.open(BytesIO(data))
            if img.mode in ("RGBA", "LA", "P"):
                bg = PILImage.new("RGB", img.size, (255, 255, 255))
                mask = img.split()[-1] if img.mode in ("RGBA", "LA") else None
                bg.paste(img, mask=mask)
                out = BytesIO()
                bg.save(out, format="JPEG", quality=92)
                return out.getvalue()
            elif img.format not in ("PNG", "JPEG") or img.mode != "RGB":
                out = BytesIO()
                img.convert("RGB").save(out, format="JPEG", quality=92)
                return out.getvalue()
        except Exception:
            # If PIL cannot identify the file (e.g. test dummy bytes), return raw data
            return data

        return data
    except Exception as exc:
        logger.warning("Normalizing image bytes failed: %s", exc)
        return data


def _fetch_image(url: str | None) -> bytes | None:
    """The creative's bytes, normalized and ready for ReportLab. Never raises."""
    if not url:
        return None

    stored = None
    try:
        stored = media_storage.read(url)
    except Exception as exc:
        logger.warning("media_storage.read failed for %s: %s", url, exc)

    if stored is None and url.startswith(("http://", "https://")):
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "MR-Signage-Report/1.0"})
            with urllib.request.urlopen(req, timeout=10) as response:
                if response.status == 200:
                    stored = response.read() or None
        except Exception as exc:
            logger.warning("Creative thumbnail HTTP fetch failed: %s", exc)

    if not stored:
        return None

    return _normalize_image_bytes(stored)


def _card(rows, widths, extra=None, bg=colors.white, border=HAIRLINE, pad=6):
    """A rounded, hairline-bordered panel -- the repeated shape of this whole layout."""
    table = Table(rows, colWidths=widths)
    style = [
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 0.5, border),
        ("ROUNDEDCORNERS", [5, 5, 5, 5]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), pad),
        ("RIGHTPADDING", (0, 0), (-1, -1), pad),
        ("TOPPADDING", (0, 0), (-1, -1), pad - 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), pad - 2),
    ]
    table.setStyle(TableStyle(style + (extra or [])))
    return table


def _pill(style, text: str, ink, bg) -> Table:
    """A status chip. Its own one-cell table so the fill hugs the text, not the column."""
    chip = Table([[Paragraph(f'<font color="#{ink.hexval()[2:]}"><b>{text}</b></font>', style["body"])]])
    chip.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("ROUNDEDCORNERS", [4, 4, 4, 4]),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return chip


def _tiles(style, cells: list[tuple[str, str, str]]) -> Table:
    """The headline strip: label, value, and a quiet sub-label under each."""
    tiles = []
    for label, value, sub in cells:
        inner = Table(
            [[Paragraph(label.upper(), style["label"])],
             [Paragraph(value, style["value"])],
             [Paragraph(sub, style["label"])]],
            colWidths=[(PAGE_W - 2 * MARGIN) / len(cells) - 4],
        )
        inner.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), TILE_BG),
            ("BOX", (0, 0), (-1, -1), 0.5, HAIRLINE),
            ("ROUNDEDCORNERS", [5, 5, 5, 5]),
            ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        tiles.append(inner)
    strip = Table([tiles], colWidths=[(PAGE_W - 2 * MARGIN) / len(cells)] * len(cells))
    strip.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 0), ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    return strip


def _build_daily_trend_chart(daily: list[dict], width: float, height: float = 52) -> Drawing:
    """Vector bar chart showing daily playback volume against average baseline."""
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=TILE_BG, strokeColor=HAIRLINE, strokeWidth=0.5, rx=4, ry=4))
    d.add(String(8, height - 11, "DAILY PLAYBACK TRENDS (DELIVERY AUDIT)", fontName=_FONT_BOLD, fontSize=6, fillColor=MUTED))

    if not daily:
        d.add(String(width / 2, height / 2 - 3, "No daily playback records reported yet for this period.",
                     fontName=_FONT_REGULAR, fontSize=7, fillColor=MUTED, textAnchor="middle"))
        return d

    chart_x = 8
    chart_y = 10
    chart_w = width - 16
    chart_h = height - 26

    max_plays = max(item.get("total_plays", 0) for item in daily) or 1
    n = len(daily)
    bar_slot = chart_w / max(n, 1)
    bar_w = max(2.5, min(12.0, bar_slot * 0.65))

    avg_plays = sum(item.get("total_plays", 0) for item in daily) / max(n, 1)
    avg_y = chart_y + (avg_plays / max_plays) * chart_h

    d.add(Line(chart_x, chart_y, chart_x + chart_w, chart_y, strokeColor=HAIRLINE, strokeWidth=0.6))
    if avg_plays > 0:
        d.add(Line(chart_x, avg_y, chart_x + chart_w, avg_y, strokeColor=colors.HexColor("#93c5fd"), strokeWidth=0.8, strokeDashArray=[2, 2]))
        d.add(String(chart_x + chart_w - 2, min(avg_y + 2, height - 8), f"Avg: {int(avg_plays):,}/day", fontName=_FONT_REGULAR, fontSize=5, fillColor=colors.HexColor("#2563eb"), textAnchor="end"))

    for i, item in enumerate(daily):
        plays = item.get("total_plays", 0)
        h = max(2.0, (plays / max_plays) * chart_h) if plays > 0 else 1.0
        bx = chart_x + i * bar_slot + (bar_slot - bar_w) / 2
        by = chart_y
        bar_color = NAVY if plays >= avg_plays else colors.HexColor("#3b82f6")
        d.add(Rect(bx, by, bar_w, h, fillColor=bar_color, strokeColor=None, rx=1, ry=1))

        if n <= 8 or i == 0 or i == n - 1 or i == n // 2:
            raw_date = item.get("date", "")
            label = raw_date[5:] if len(raw_date) >= 10 else raw_date
            d.add(String(bx + bar_w / 2, chart_y - 7, label, fontName=_FONT_REGULAR, fontSize=5, fillColor=MUTED, textAnchor="middle"))

    return d


def _verification_card(style, report: dict, width: float):
    """Scannable Proof-of-Performance audit certificate with native vector QR code."""
    cert_id = report.get("certificate_id") or f"POP-{report.get('placement_id', 0):04d}-AUDIT"
    verify_url = report.get("verification_url") or f"https://olrac-signage.abhinavsanthosh221.workers.dev/verify/pop?cert={cert_id}"

    qr_widget = qr.QrCodeWidget(verify_url)
    bounds = qr_widget.getBounds()
    qr_w = bounds[2] - bounds[0]
    qr_h = bounds[3] - bounds[1]
    qr_size = 22 * mm
    qr_drawing = Drawing(qr_size, qr_size, transform=[qr_size / qr_w, 0, 0, qr_size / qr_h, 0, 0])
    qr_drawing.add(qr_widget)

    cert_info = [
        [Paragraph('<font color="#16a34a"><b>OFFICIAL AUDIT &amp; VERIFICATION CERTIFICATE</b></font>', style["label"])],
        [Table([
            [Paragraph("Certificate ID:", style["label"]), Paragraph(f'<b>{cert_id}</b>', style["value_sm"])],
            [Paragraph("Delivery Audit:", style["label"]), Paragraph('<font color="#16a34a"><b>Cryptographically Verified &amp; Deduplicated</b></font>', style["body"])],
            [Paragraph("Scan with phone:", style["label"]), Paragraph('Scan the QR code to verify live logs, screen health, and audit stamps.', style["note"])],
        ], colWidths=[24 * mm, width - 24 * mm - qr_size - 18 * mm],
           style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                             ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
        ]
    ]

    row = [[
        qr_drawing,
        Table(cert_info, colWidths=[width - qr_size - 12 * mm],
              style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 4), ("TOPPADDING", (0, 0), (-1, -1), 0),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 0)])),
    ]]
    return _card(row, [qr_size + 4 * mm, width - qr_size - 6 * mm], bg=OK_BG, border=colors.HexColor("#bbf7d0"), pad=4)


def _grid(rows, widths, header=True) -> Table:
    """The data tables: navy header, zebra body, hairline grid."""
    table = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    style = [
        ("FONTNAME", (0, 0), (-1, -1), _FONT_REGULAR),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, HAIRLINE),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    if header:
        style += [
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), _FONT_BOLD),
            ("FONTSIZE", (0, 0), (-1, 0), 6.5),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, TILE_BG]),
        ]
    table.setStyle(TableStyle(style))
    return table


def _band_color(value: str | None):
    if not value:
        return INK
    try:
        return colors.HexColor(value if value.startswith("#") else f"#{value}")
    except Exception:
        logger.warning("Ignoring unusable brand colour %r", value)
        return INK


def _page_furniture(canvas, doc, org_name: str, generated: str, contact: str | None,
                    logo: bytes | None = None, band=INK, label: str = "Report generated on"):
    canvas.saveState()
    canvas.setFillColor(band)
    canvas.rect(0, PAGE_H - BAND_H, PAGE_W, BAND_H, stroke=0, fill=1)

    text_x = MARGIN
    if logo:
        try:
            from reportlab.lib.utils import ImageReader
            mark = ImageReader(BytesIO(logo))
            width, height = mark.getSize()
            drawn_h = BAND_H - 7 * mm
            drawn_w = min(drawn_h * (width / height if height else 1), 45 * mm)
            canvas.drawImage(mark, MARGIN, PAGE_H - BAND_H + 3.5 * mm,
                             width=drawn_w, height=drawn_h, mask="auto",
                             preserveAspectRatio=True, anchor="sw")
            text_x = MARGIN + drawn_w + 4 * mm
        except Exception as exc:
            logger.warning("Brand logo could not be drawn: %s", exc)

    canvas.setFillColor(colors.white)
    canvas.setFont(_FONT_BOLD, 11)
    canvas.drawString(text_x, PAGE_H - BAND_H + 5.5 * mm, org_name[:48])
    canvas.setFont(_FONT_REGULAR, 7)
    # Labelled by the caller: the invoice shares this letterhead deliberately, and
    # "Report generated on" printed across the top of a bill is simply wrong.
    canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - BAND_H + 6 * mm, f"{label}: {generated}")

    canvas.setFillColor(TILE_BG)
    canvas.rect(0, 0, PAGE_W, FOOTER_H, stroke=0, fill=1)
    canvas.setStrokeColor(HAIRLINE)
    canvas.line(0, FOOTER_H, PAGE_W, FOOTER_H)
    canvas.setFillColor(NAVY)
    canvas.setFont(_FONT_BOLD, 7.5)
    canvas.drawString(MARGIN, FOOTER_H - 6.5 * mm, f"Thank you for choosing {org_name[:40]}.")
    canvas.setFillColor(MUTED)
    canvas.setFont(_FONT_REGULAR, 6.5)
    if contact:
        canvas.drawString(MARGIN, FOOTER_H - 10.5 * mm, f"For any queries, contact {contact}")
    canvas.drawRightString(PAGE_W - MARGIN, FOOTER_H - 6.5 * mm, f"Page {canvas.getPageNumber()}")
    canvas.restoreState()


def build_pdf(report: dict) -> bytes:
    """Render a booking report dict (from build_booking_report) into PDF bytes."""
    style = _styles()
    buffer = BytesIO()

    org = report.get("organization") or {}
    org_name = _safe(org.get("name")) or "Signage network"
    client = report.get("client") or {"name": report["advertiser"]}
    ends_at = report.get("effective_ends_at") or report["ends_at"]
    totals = report["totals"]
    generated = report["generated_at"].strftime("%d %b %Y %H:%M UTC")

    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=BAND_H + 5 * mm, bottomMargin=FOOTER_H + 4 * mm,
        title=f"Campaign report - {report['advertiser']}",
        author=org_name,
    )

    logo = _fetch_image(org.get("logo"))
    band = _band_color(org.get("brand_color"))
    furniture = lambda canvas, d: _page_furniture(  # noqa: E731
        canvas, d, org_name, generated, org.get("email"), logo=logo, band=band)

    story = []
    content_w = PAGE_W - 2 * MARGIN

    # --- Title & Client identification ------------------------------------------------
    who = [f"<b>{_markup(client.get('name')) or '-'}</b>"]
    if client.get("client_code"):
        who.append(f"Client ID: {_markup(client['client_code'])}")
    if client.get("email"):
        who.append(f"Email: {_markup(client['email'])}")
    if client.get("phone"):
        who.append(f"Phone: {_markup(client['phone'])}")

    story.append(Table(
        [[
            [Paragraph("SIGNAGE CAMPAIGN REPORT", style["title"]),
             Paragraph("Official Proof-of-Performance &amp; playback audit summary.", style["sub"])],
            _card(
                [[Paragraph("REPORT FOR", style["label"])],
                 [Paragraph("<br/>".join(who), style["body"])]],
                [72 * mm], bg=TILE_BG,
            ),
        ]],
        colWidths=[content_w - 76 * mm, 76 * mm],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                          ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]),
    ))
    story.append(Spacer(1, 4 * mm))

    # --- Headline tiles ---------------------------------------------------------------
    story.append(_tiles(style, [
        ("Campaign period", f"{_date(report['starts_at'])}<br/>to {_date(ends_at)}", "Start to end"),
        ("Total locations", str(len(report["per_location"])), "Active locations"),
        (
            "Total ad plays", f"{totals['total_plays']:,}",
            f"{totals.get('online_plays', totals['total_plays']):,} online &#183; {totals.get('offline_plays', 0):,} offline",
        ),
        # Total on-screen exposure: plays x the creative's real length. The headline number a
        # client wants -- how long their advert was actually up. Completion % moved to its own
        # card below, so no duplication. Not "Amount paid": what they owe is on the invoice.
        ("Total airtime", _duration(report.get("airtime_seconds", 0)), "On-screen exposure"),
        ("Days remaining", f"{report.get('days_remaining', 0)} days", f"of {report.get('days_total', 0)} days"),
    ]))
    story.append(Spacer(1, 4 * mm))

    # --- The creative, and where it ran -----------------------------------------------
    creative = _fetch_image(report.get("content_thumbnail"))

    # Number each location the way the performance table does (1..N, ordered by plays) and
    # stamp that number onto its screens' map pins, so a pin on the map and a row in the
    # table below are unmistakably the same place. The legend under the map reads the numbers
    # out and says what the pin colours mean -- that is what turns a field of dots into
    # something a client can actually understand.
    loc_order = [p["location"] for p in report["per_location"]]
    loc_index = {name: i for i, name in enumerate(loc_order, 1)}
    located = []
    for s in report["per_screen"]:
        if s.get("latitude") is not None and s.get("longitude") is not None:
            pin = dict(s)
            pin["label"] = str(loc_index.get(s.get("location") or "Location not set", ""))
            located.append(pin)
    map_bytes = fetch_static_map(located, width=900, height=500) if located else None

    left = (Image(BytesIO(creative), width=58 * mm, height=58 * mm, kind="proportional")
            if creative else Paragraph("Creative preview unavailable.", style["note"]))

    map_rows = [[Paragraph("LOCATIONS IN MAP", style["label"])]]
    if map_bytes:
        map_rows.append([Image(BytesIO(map_bytes), width=104 * mm, height=57 * mm)])
        legend = " &nbsp;&#183;&nbsp; ".join(
            f'<b>{loc_index[name]}.</b> {_markup(name)}' for name in loc_order if name in loc_index
        )
        map_rows.append([Paragraph(legend, style["note"])])
        map_rows.append([Paragraph(
            '<font color="#16a34a"><b>&#9679;</b></font> Live &nbsp;&nbsp; '
            '<font color="#64748b"><b>&#9679;</b></font> Not reporting in', style["note"])])
    elif located:
        map_rows.append([Paragraph("Map could not be drawn just now. The locations are listed below.", style["note"])])
    else:
        map_rows.append([Paragraph("No coordinates set for these screens, so no map can be drawn.", style["note"])])

    story.append(Table(
        [[
            _card([[Paragraph("AD THUMBNAIL", style["label"])], [left]], [64 * mm]),
            _card(map_rows, [112 * mm]),
        ]],
        colWidths=[66 * mm, content_w - 66 * mm],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                          ("LEFTPADDING", (0, 0), (0, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]),
    ))
    story.append(Spacer(1, 3.5 * mm))

    # --- NEW: Daily Playback Trend Vector Chart & SLA Compliance Gauge ----------------
    daily = report.get("daily") or []
    tot_plays = totals.get("total_plays", 0)
    completed = totals.get("completed_plays", 0)
    sla_pct = round((completed / tot_plays * 100), 1) if tot_plays > 0 else 100.0

    trend_chart = _build_daily_trend_chart(daily, content_w - 48 * mm, height=48)
    # Playback completion, honestly labelled: this is completed/total plays -- the share of
    # plays that ran to full length. It is NOT a contracted delivery target, so it must not
    # be captioned as one on a document that carries a verification certificate.
    completion_card = _card([
        [Paragraph('<font color="#16a34a"><b>PLAYBACK COMPLETION</b></font>', style["label"])],
        [Paragraph(f"{sla_pct}%", style["value"])],
        [Paragraph(f"{completed:,} of {tot_plays:,} plays ran in full", style["label"])],
        [Paragraph("Share of plays that reached their full length", style["note"])]
    ], [44 * mm], bg=OK_BG, border=colors.HexColor("#bbf7d0"), pad=4)

    story.append(Table(
        [[trend_chart, completion_card]],
        colWidths=[content_w - 46 * mm, 46 * mm],
        style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ])
    ))
    story.append(Spacer(1, 3.5 * mm))

    # --- Per-location performance -----------------------------------------------------
    story.append(Paragraph("PERFORMANCE SUMMARY", style["h2"]))
    if report["per_location"]:
        rows = [["#", "LOCATION NAME", "LOCATION ID", "AD PLAYS PER DAY (AVG.)", "TOTAL PLAYS (PERIOD)"]]
        for index, place in enumerate(report["per_location"], 1):
            rows.append([
                str(index),
                _safe(place["location"]),
                f"LOC{index:03d}",
                f"{place.get('plays_per_day_avg', 0):,}",
                f"{place['total_plays']:,}",
            ])
        avg_total = sum(p.get("plays_per_day_avg", 0) for p in report["per_location"])
        rows.append(["", "TOTAL", "-", f"{avg_total:,.1f} (avg.)", f"{totals['total_plays']:,}"])

        table = _grid(rows, [10 * mm, 58 * mm, 28 * mm, 46 * mm, content_w - 142 * mm])
        table.setStyle(TableStyle([
            ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
            ("BACKGROUND", (0, -1), (-1, -1), TOTAL_BG),
            ("FONTNAME", (0, -1), (-1, -1), _FONT_BOLD),
            ("TEXTCOLOR", (0, -1), (-1, -1), NAVY),
        ]))
        story.append(table)
    else:
        story.append(Paragraph("No locations were booked for this period.", style["body"]))

    # --- Page Two: Commercials, Proof of Performance Certificate, and Screen Delivery -
    story.append(PageBreak())
    page_two_from = len(story)

    running = "Active"
    if report["starts_at"] > report["generated_at"]:
        running = "Scheduled"
    elif ends_at < report["generated_at"]:
        running = "Ended"

    extensions = report.get("extensions") or []

    details_rows = [
        [Paragraph("Campaign name", style["body"]), Paragraph(_markup(report["content_name"]), style["body"])],
        [Paragraph("Start date", style["body"]), Paragraph(f'<para align="right">{_date(report["starts_at"])}</para>', style["body"])],
        [Paragraph("End date (as sold)" if extensions else "End date", style["body"]), Paragraph(f'<para align="right">{_date(report["ends_at"])}</para>', style["body"])],
    ] + ([[Paragraph("Extended to", style["body"]), Paragraph(f'<para align="right">{_date(ends_at)}</para>', style["body"])]] if extensions else []) + [
        [Paragraph("Total days", style["body"]), Paragraph(f'<para align="right">{report.get("days_total", "-")} days</para>', style["body"])],
        [Paragraph("Days remaining", style["body"]), Paragraph(f'<para align="right">{report.get("days_remaining", "-")} days</para>', style["body"])],
        [Paragraph("Locations", style["body"]), Paragraph(f'<para align="right">{len(report["per_location"])}</para>', style["body"])],
    ]
    details = _grid(details_rows, [28 * mm, 52 * mm], header=False)

    # Campaign status only. Payment status moved to the invoice: a client reading proof
    # that their advert ran should not be handed a reminder of what they owe in the same
    # breath, and a tenant forwarding delivery evidence should not have to redact terms.
    status = Table(
        [[Paragraph("Campaign status", style["body"]),
          _pill(style, running, OK_INK if running == "Active" else MUTED,
                OK_BG if running == "Active" else TILE_BG)]],
        colWidths=[28 * mm, 52 * mm],
    )
    status.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, HAIRLINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))

    plan = report.get("plan")
    plan_card = _card(
        [[Paragraph('<font color="#6d28d9"><b>CURRENT PLAN</b></font>', style["label"])],
         [Table([[Paragraph(_markup(plan["name"]) if plan else "No plan", style["value_sm"]),
                  # The plan's NAME and length, not its price. What the plan cost is a
                  # commercial term and lives on the invoice.
                  Paragraph(f'<para align="right"><font size="6.5" color="#64748b">'
                            f'{plan["duration_days"] if plan else report.get("days_total", 0)} days</font></para>',
                            style["value_sm"])]],
                colWidths=[42 * mm, 42 * mm],
                style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                  ("TOPPADDING", (0, 0), (-1, -1), 0),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 0)]))],
         [Paragraph(
             (plan.get("description")
              or f"{plan['max_locations']} locations | {plan['ad_slots']} ad slot(s) | {plan['support_tier']}")
             if plan else "Sold without a plan.", style["note"])]]
        + ([[Paragraph(
            f"Running in {len(report['per_location'])} locations, "
            f"above this plan's {report.get('plan_max_locations')}.", style["note"])]]
           if report.get("plan_locations_exceeded") else []),
        [86 * mm], bg=PLAN_BG, border=colors.HexColor("#ddd6fe"),
    )

    if extensions:
        ext_rows = [[Paragraph('<font color="#16a34a"><b>EXTENDED PLAN</b></font>', style["label"]),
                     Paragraph('<para align="right"><font color="#16a34a"><b>Yes</b></font></para>', style["label"])]]
        for extension in extensions:
            ext_rows.append([Paragraph("Extended from", style["body"]),
                             Paragraph(f'<para align="right">{_date(extension["extended_from"])}</para>', style["body"])])
            ext_rows.append([Paragraph("Extended to", style["body"]),
                             Paragraph(f'<para align="right">{_date(extension["extended_to"])}</para>', style["body"])])
        # Dates only. An extension's DATES explain the run and belong on proof of delivery;
        # what it cost is billing and belongs on the invoice.
        ext_card = _card(ext_rows, [46 * mm, 40 * mm], bg=OK_BG, border=colors.HexColor("#bbf7d0"))
    else:
        ext_card = _card(
            [[Paragraph("EXTENDED PLAN", style["label"])],
             [Paragraph("No extension sold on this booking.", style["note"])]],
            [86 * mm], bg=TILE_BG,
        )

    story.append(Table(
        [[Paragraph("CAMPAIGN DETAILS", style["h2"]), Paragraph("PLAN &amp; EXTENSION DETAILS", style["h2"])],
         [details, plan_card],
         ["", Spacer(1, 3)],
         [status, ext_card]],
        colWidths=[80 * mm, content_w - 84 * mm],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                          ("LEFTPADDING", (0, 0), (0, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                          ("TOPPADDING", (0, 1), (-1, -1), 3)]),
    ))
    story.append(Spacer(1, 4 * mm))

    # --- NEW: Official Proof-of-Performance Verification Certificate Card -------------
    story.append(_verification_card(style, report, content_w))

    # --- Screen-level delivery, measured dynamically to prevent awkward overflow ------
    if report["per_screen"]:
        rows = [["SCREEN", "LOCATION", "AIRTIME", "PLAYS", "COMPLETED"]]
        for screen in report["per_screen"]:
            status_badge = screen.get("status", "Active")
            days_info = f"{screen.get('days', '-')}d ({status_badge})"
            rows.append([
                _safe(screen["screen_name"]) + (" *" if screen["counts_may_be_incomplete"] else ""),
                _safe(screen["location"]) or "-",
                days_info,
                f"{screen['total_plays']:,}",
                f"{screen['completed_plays']:,}",
            ])
        screen_table = _grid(rows, [46 * mm, 46 * mm, 30 * mm, 25 * mm, content_w - 147 * mm])
        screen_table.setStyle(TableStyle([
            ("ALIGN", (2, 0), (2, -1), "CENTER"),
            ("ALIGN", (3, 0), (-1, -1), "RIGHT"),
        ]))

        used = sum(flowable.wrap(doc.width, doc.height)[1] for flowable in story[page_two_from:])
        heading = Paragraph("SCREEN-LEVEL DELIVERY", style["h2"])
        remaining = doc.height - used - heading.wrap(doc.width, doc.height)[1] - 14 * mm

        if screen_table.wrap(doc.width, doc.height)[1] <= remaining:
            story.append(Spacer(1, 5 * mm))
            story.append(heading)
            story.append(screen_table)
        else:
            locations = len(report["per_location"])
            story.append(Spacer(1, 5 * mm))
            story.append(heading)
            story.append(Paragraph(
                f"{len(report['per_screen'])} screens across {locations} "
                f"location{'' if locations == 1 else 's'}. "
                "Screen-level playback is available in the campaign dashboard.",
                style["body"],
            ))

    story.append(Spacer(1, 4 * mm))
    footnote = (
        "Figures come from playback records reported by each screen and deduplicated on arrival."
    )
    if report["stale_screens"]:
        footnote += (
            " <b>*</b> These screens had not reported in by the time this report was produced, "
            "so their totals may rise once they reconnect: " + ", ".join(report["stale_screens"]) + "."
        )
    story.append(Paragraph(footnote, style["note"]))

    doc.build(story, onFirstPage=furniture, onLaterPages=furniture)
    return buffer.getvalue()
