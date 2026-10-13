"""D-T8: the design review D19 phone layout. Below 640px each row is a stacked block (status + sentence, then
Label: value pairs), no sparkline and no dispute controls, the note "Open on a desktop to dispute or drill down.",
16px gutters, no horizontal scroll. A real 375px render is a browser check (see the report).
"""

import re
from pathlib import Path

import pytest
from django.conf import settings

from tests.screen_world import build, pin, t
from tests.test_a11y import _pages

BASE = Path(settings.BASE_DIR)


@pytest.fixture
def p(monkeypatch):
    from disputes import service
    from disputes.models import Dispute

    p = build()
    pin(monkeypatch, t(p["hub"], 14, 40))
    service.submit(p["users"]["lead"], p["jobs"]["new"][:1], Dispute.WRONG_QUANTITY)
    return p

def _phone_css():
    css = (BASE / "web/static/css/screens.css").read_text()
    return re.search(r"@media \(max-width: 639\.98px\) \{(.*)\}\s*$", css, re.S).group(1)


def test_phone_stacks_rows_with_16px_gutters_and_no_horizontal_scroll():
    css = _phone_css()
    assert "table, caption, thead, tbody, tfoot, tr, th, td { display: block;" in css
    assert "main { padding: var(--s4);" in css  # --s4 = 16px
    assert "--s4: 16px" in (BASE / "web/static/css/tokens.css").read_text()
    assert "overflow-wrap: anywhere" in css and "max-width: 100%" in css
    assert 'td[data-label]::before { content: attr(data-label) ": ";' in css
    assert ".line-health .evidence-label { display: inline;" in css


def test_phone_hides_sparkline_and_dispute_controls_and_shows_the_note(p, client):
    css = _phone_css()
    hidden = re.search(r"\.sparkline, \.col-trend,(.*?)\{ display: none !important; \}", css, re.S).group(0)
    for sel in (".sparkline", ".drill-toggle", ".dispute-toggle", ".col-dispute", ".excluded-link"):
        assert sel in hidden, sel
    assert ".phone-note { display: block;" in css
    pages = _pages(client, p)
    for name in ("line_health", "area", "my_shift", "queue"):
        assert '<p class="phone-note">Open on a desktop to dispute or drill down.</p>' in pages[name], name
    # Label: value pairs on Line Health rows (Pace, Yield, Bottleneck), the status and sentence first
    row = re.search(r'<tbody class="line-row[^"]*"[^>]*>(.*?)</tbody>', pages["line_health"], re.S).group(1)
    assert row.index("status-cell") < row.index('<span class="evidence-label">Pace: </span>') \
        < row.index('<span class="evidence-label">Yield: </span>') < row.index('<span class="evidence-label">Bottleneck: </span>')
