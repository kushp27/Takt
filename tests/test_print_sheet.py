"""D-T9 + E-T5: the printed shift sheet (design D20, eng re-review D6).

- header: line, shift, date, then ONLY {% print_header hub %}: "Data as of", "Printed", "late-closing jobs
  may change totals", and "STALE: data stopped at" when the hub is stale (fresh vs stale pytest);
- one section per station-part; columns operator, jobs, quantity and a blank Correction column;
- exactly the operator counts matrix 4a allows; no rates, medians, comparisons or "worth a conversation";
- trust as the D16 words; footer "For lead use. Not for posting." and page X of Y;
- letters per page token when Gate 1 is off (D12), names for the builder;
- print.css: US Letter portrait, black and white, the nav hidden.
"""

import re
from datetime import timedelta
from html import unescape
from pathlib import Path

import pytest
from django.conf import settings

from tests.identity_world import set_gate1
from tests.screen_world import D, NAMES, build, pin, t


@pytest.fixture
def p(monkeypatch):
    p = build()
    p["url"] = f"/area/{p['area'].pk}/shift/print/?shift_id={p['first'].pk}&date=2026-09-15"
    return p


def _sheet(client, p, user):
    client.force_login(user)
    resp = client.get(p["url"])
    assert resp.status_code == 200
    return resp.content.decode()


def _text(html):
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip())


def test_fresh_header(p, client, monkeypatch):
    now = t(p["hub"], 14, 41)
    pin(monkeypatch, now)
    p["hub"].last_new_transaction_at = t(p["hub"], 14, 35)
    p["hub"].last_successful_ingest_at = t(p["hub"], 14, 38)
    p["hub"].save()
    html = _sheet(client, p, p["users"]["builder"])
    head = re.search(r'<header class="print-head">(.*?)</header>', html, re.S).group(1)
    assert "<h1>Shift sheet · Cores Area</h1>" in head
    assert "Cores Line 1 · 1st shift · Tuesday, September 15, 2026" in _text(head)
    assert "Data as of 2:38 PM · Printed 2:41 PM · late-closing jobs may change totals" in _text(head)
    assert "STALE" not in head


def test_stale_header(p, client, monkeypatch):
    pin(monkeypatch, t(p["hub"], 14, 41))
    p["hub"].last_new_transaction_at = t(p["hub"], 6, 40)
    p["hub"].last_successful_ingest_at = t(p["hub"], 14, 38)
    p["hub"].save()
    html = _sheet(client, p, p["users"]["builder"])
    head = re.search(r'<header class="print-head">(.*?)</header>', html, re.S).group(1)
    assert '<p class="print-stale"><strong>STALE: data stopped at 6:40 AM</strong></p>' in head
    assert "Data as of 2:38 PM · Printed 2:41 PM · late-closing jobs may change totals" in _text(head)


def test_sheet_body_counts_only_with_correction_column(p, client, monkeypatch):
    pin(monkeypatch, t(p["hub"], 16))
    html = _sheet(client, p, p["users"]["builder"])
    sort = re.search(r"<caption>Sort · part C-4471 · Verified ✓</caption>(.*?)</table>", html, re.S).group(1)
    heads = re.findall(r'<th scope="col"[^>]*>([^<]+)</th>', sort)
    assert heads == ["Operator", "Jobs", "Quantity", "Correction"]
    rows = re.findall(r'<tr><th scope="row">([^<]+)</th><td class="num">(\d+)</td><td class="num">(\d+)</td>'
                      r'<td class="correction"></td></tr>', sort.split("<tfoot>")[0])
    assert rows == [("Fenwick Oddsworth", "2", "168"), ("Mortimer Ashgrove", "2", "140"),
                    ("Nerissa Quell", "2", "48"), ("Unmapped operator (CT-901)", "1", "10")]
    assert re.search(r'<th scope="row">Part total</th><td class="num">7</td><td class="num">366</td>', sort)
    assert "<strong>Station total:</strong> 7 jobs · 366 units" in html
    sheet = html.split('<article class="print-sheet"')[1]
    for banned in ("vs median", "Rate", "u/hr", "Worth a conversation", "This looks wrong", "Time with no transaction",
                   "Upstream", "Tenure", "tenure"):
        assert banned not in sheet, banned
    assert '<footer class="print-foot">For lead use. Not for posting.</footer>' in html


def test_sheet_follows_matrix_for_unverified_station(p, client, monkeypatch):
    pin(monkeypatch, t(p["hub"], 16))
    p["s2"].count_trusted = False
    p["s2"].save()
    html = _sheet(client, p, p["users"]["builder"])
    assert "Sort · part C-4471 · Counts unverified ?" in _text(html)
    assert "Counts unverified, keep tally." in html
    assert not any(n in html for n in NAMES)


def test_letters_per_page_token_before_gate1(p, client, monkeypatch):
    pin(monkeypatch, t(p["hub"], 16))
    set_gate1(False)
    first = _sheet(client, p, p["users"]["lead"])
    assert "Names hidden until HR approves operator-level views (Gate 1)." in first
    assert not any(n in first for n in NAMES)
    labels = re.findall(r'<tr><th scope="row">((?:Unmapped operator|Operator) [A-Z]+)</th>', first)
    assert len(labels) == 5  # feeder at Injection 3, four at Sort
    # every print is a fresh page token: letters are reassigned per page (D12)
    tokens = {re.search(r'hx-vals="([^"]+)"', _sheet(client, p, p["users"]["lead"])).group(1) for _ in range(3)}
    assert len(tokens) == 3


def test_print_css_letter_black_and_white_hides_nav():
    css = (Path(settings.BASE_DIR) / "web/static/css/print.css").read_text()
    assert "size: letter portrait" in css
    assert 'counter(page) " of " counter(pages)' in css
    assert "@bottom-left" not in css  # the footer line is in flow only, so it never prints twice
    assert re.search(r"\.site-header, \.stale-banner, nav,", css) and "display: none !important" in css
    assert "* { color: #000 !important; background: transparent !important;" in css
    assert ".print-station { break-inside: avoid;" in css  # page breaks only between stations
    assert ".print-foot { display: block !important;" in css  # D20 footer prints in every browser, Firefox too
    base = (Path(settings.BASE_DIR) / "web/templates/web/base.html").read_text()
    assert "<link rel=\"stylesheet\" href=\"{% static 'css/print.css' %}\" media=\"print\">" in base


def test_print_link_on_my_shift(p, client, monkeypatch):
    pin(monkeypatch, t(p["hub"], 12))
    client.force_login(p["users"]["lead"])
    html = client.get(f"/area/{p['area'].pk}/shift/?shift_id={p['first'].pk}").content.decode()
    assert f'<a href="/area/{p["area"].pk}/shift/print/?date=2026-09-15&amp;shift_id={p["first"].pk}">Print sheet</a>' in html


def test_print_without_a_shift_redirects_supervisor_to_a_default(p, client, monkeypatch):
    pin(monkeypatch, t(p["hub"], 16, d=D + timedelta(days=1)))
    client.force_login(p["users"]["supervisor"])
    resp = client.get(f"/area/{p['area'].pk}/shift/print/")
    assert resp.status_code == 302 and f"shift_id={p['second'].pk}" in resp["Location"]
