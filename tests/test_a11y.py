"""D-T7: the design review D18 accessibility baseline, checked on the rendered screens.

D18: tables are real <table>s with a caption and <th scope>; every clickable row is a real button; focus moves
to the new content's heading after an htmx swap and #live-region (aria-live="polite") announces it; landmarks
header / nav / main; the status word is the accessible name, colour never alone; controls >= 32px.
The keyboard walk-through and the VoiceOver pass are browser checks (see the report).
"""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings

from accounts.policy import TOKEN_PARAM
from disputes import service
from disputes.models import Dispute
from tests.screen_world import D, build, pin, t

TOKEN_RE = re.compile(r'hx-vals="\{&quot;' + TOKEN_PARAM + r'&quot;: &quot;([^&]+)&quot;\}"')
BASE = Path(settings.BASE_DIR)


@pytest.fixture
def p(monkeypatch):
    p = build()
    pin(monkeypatch, t(p["hub"], 14, 40))
    service.submit(p["users"]["lead"], p["jobs"]["new"][:1], Dispute.WRONG_QUANTITY)
    return p


def _pages(client, p):
    """Every full screen and partial, rendered for the builder (named)."""
    client.force_login(p["users"]["builder"])
    a, s = p["area"].pk, p["first"].pk
    out = {}
    for name, url in [("line_health", "/line/?date=2026-09-15"), ("area", f"/area/{a}/?date=2026-09-15"),
                      ("my_shift", f"/area/{a}/shift/?shift_id={s}&date=2026-09-15"),
                      ("print", f"/area/{a}/shift/print/?shift_id={s}&date=2026-09-15"),
                      ("queue", f"/area/{a}/disputes/")]:
        resp = client.get(url)
        assert resp.status_code == 200, name
        out[name] = resp.content.decode()
    tok = TOKEN_RE.search(out["area"]).group(1)
    hx = {"HTTP_HX_REQUEST": "true"}
    out["stations"] = client.get(f"/line/{p['line'].pk}/stations/", {"date": "2026-09-15"}, **hx).content.decode()
    out["operators"] = client.get(f"/station/{p['s2'].pk}/operators/",
                                  {"date": "2026-09-15", TOKEN_PARAM: tok}, **hx).content.decode()
    tok = TOKEN_RE.search(out["my_shift"]).group(1)
    out["jobs"] = client.get(f"/area/{a}/shift/jobs/{p['ops']['mid'].pk}/",
                             {"shift_id": s, "date": "2026-09-15", TOKEN_PARAM: tok}, **hx).content.decode()
    d = Dispute.objects.get()
    tok = TOKEN_RE.search(out["queue"]).group(1)
    out["detail"] = client.get(f"/area/{a}/disputes/{d.pk}/", {TOKEN_PARAM: tok}, **hx).content.decode()
    return out


def test_every_table_has_a_caption_and_scoped_headers(p, client):
    for name, html in _pages(client, p).items():
        for table in re.findall(r"<table\b.*?</table>", html, re.S):
            inner = re.sub(r"<table\b.*?</table>", "", table[6:], flags=re.S)  # nested tables checked on their own
            assert "<caption" in table, f"{name}: table without a caption"
            for th in re.findall(r"<th\b[^>]*>", inner):
                assert "scope=" in th, f"{name}: {th}"


def test_landmarks_and_live_region(p, client):
    html = _pages(client, p)["line_health"]
    assert '<header class="site-header">' in html and '<nav aria-label="Main">' in html and '<main id="main">' in html
    assert '<div id="live-region" class="visually-hidden" aria-live="polite" aria-atomic="true"></div>' in html
    assert re.search(r'<script src="[^"]*js/takt\.js" defer></script>', html)


def test_clickable_things_are_buttons_with_state(p, client):
    pages = _pages(client, p)
    for name in ("line_health", "area", "stations", "my_shift", "queue"):
        toggles = re.findall(r'<button type="button" class="button-link drill-toggle[^"]*"[^>]*>', pages[name])
        assert toggles, name
        for b in toggles:
            assert 'aria-expanded="false"' in b and 'aria-controls="' in b and "hx-get=" in b, b
            assert "data-takt-loaded=" in b and "data-load-error=" in b, b
            target = re.search(r'aria-controls="([^"]+)"', b).group(1)
            assert f'id="{target}"' in pages[name], (name, target)
    # no clickable <tr>/<td> handlers
    for name, html in pages.items():
        assert not re.search(r"<t[rd][^>]*(onclick|hx-get|hx-post)", html), name


def test_swapped_content_starts_with_a_focusable_heading(p, client):
    pages = _pages(client, p)
    for name in ("stations", "operators", "jobs", "detail"):
        first = re.search(r"<h[1-4][^>]*>", pages[name])
        assert first and 'tabindex="-1"' in first.group(0), name
    js = (BASE / "web/static/js/takt.js").read_text()
    for needle in ("htmx:afterSwap", "focusHeading(container)", "aria-expanded", "live-region",
                   "data-takt-loaded", "takt:announce", "data-takt-cancel", "toggle.focus()", "data-takt-focus-here"):
        assert needle in js, needle


def test_status_word_is_text_and_controls_are_32px(p, client):
    html = _pages(client, p)["line_health"]
    assert re.search(r'<span class="status-word status-[a-z]+">(On pace|Behind|Yield problem|No demand set|'
                     r'Pace unavailable)</span>', html)
    tokens = (BASE / "web/static/css/tokens.css").read_text()
    assert "--control-min-height: 32px" in tokens and "min-height: var(--control-min-height)" in tokens
    screens = (BASE / "web/static/css/screens.css").read_text()
    assert ".button-link {" in screens and "min-height: var(--control-min-height)" in screens
