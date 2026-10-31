"""Names typed by a tenant must survive being put into a PDF paragraph.

ReportLab reads a Paragraph's string as markup. `_safe` folds typography but does not escape, so
a client called "R&D Ltd" printed as "R&D; Ltd" and a name containing "<VIP>" lost the tag. Text
going into a Paragraph now goes through `_markup`; plain table cells and canvas text keep `_safe`,
where escaping would print a literal "&amp;". Pure: renders nothing, reads no database.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402
from reportlab.lib.styles import getSampleStyleSheet  # noqa: E402
from reportlab.platypus import Paragraph  # noqa: E402

from backend.reports.booking_report import _markup, _safe  # noqa: E402

STYLE = getSampleStyleSheet()["BodyText"]


def plain_text(markup: str) -> str:
    return Paragraph(markup, STYLE).getPlainText()


@pytest.mark.parametrize("name", [
    "R&D Ltd",
    "A < B > C",
    "<VIP> Lounge",
    "Tom & Jerry's “Cafe”",
    "Fish &amp; Chips",       # already looks escaped: it must stay exactly as typed
])
def test_a_name_survives_a_paragraph(name):
    shown = plain_text(_markup(name))
    # _safe only folds the curly quotes; the characters the user typed must all still be there.
    assert shown == _safe(name), f"{name!r} came out as {shown!r}"


def test_the_old_helper_really_did_mangle_markup():
    """Documents the bug _markup fixes, so nobody 'simplifies' it back to _safe."""
    assert plain_text("R&D Ltd") != "R&D Ltd"
    assert plain_text(_safe("<VIP> Lounge")) != "<VIP> Lounge"


def test_markup_escapes_exactly_the_three_characters_that_matter():
    assert _markup("a & b < c > d") == "a &amp; b &lt; c &gt; d"
    assert _markup(None) == ""
    assert _markup("plain") == "plain"


def test_plain_text_stays_unescaped():
    """Canvas text and plain table cells are not markup; escaping there would print '&amp;'."""
    assert _safe("R&D Ltd") == "R&D Ltd"
