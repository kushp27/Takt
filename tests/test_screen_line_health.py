"""D-T2: Line Health, the ops manager's home (design Component 6, review D3, D4, D22), and its drill-downs.

- a status-first plain table: the status word and sentence lead, evidence below; no bordered boxes;
- the merged pace column ("104% · +40 units" / "— · no demand set"), raw counts as the tooltip;
- "Pace unavailable" (user-approved fifth word) in a neutral style; "Behind" in colour with its word;
- the 14-day sparkline with the API's trend_text as its accessible name;
- drill-down: line -> stations (trust badges, raw good/required) -> operators at one station and part.
"""

import re
from datetime import timedelta
from pathlib import Path

import pytest
from django.conf import settings

from accounts.policy import TOKEN_PARAM
from core import models as m
from metrics import api
from tests.screen_world import D, NAMES, build, pin, t
from web import sparkline

TOKEN_RE = re.compile(r'hx-vals="\{&quot;' + TOKEN_PARAM + r'&quot;: &quot;([^&]+)&quot;\}"')


@pytest.fixture
def p(monkeypatch):
    p = build()
    now = t(p["hub"], 12, d=D + timedelta(days=1))
    pin(monkeypatch, now)  # default window: Tue Sep 15
    p["hub"].last_new_transaction_at = now - timedelta(minutes=4)  # fresh
    p["hub"].save()
    return p


def _get(client, user, url, **params):
    client.force_login(user)
    return client.get(url, params)


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _row(p):
    return api.line_health([p["line"]], D)[0]


def test_line_row_golden(p, client):
    resp = _get(client, p["users"]["ops_manager"], "/line/")
    assert resp.status_code == 200
    html = resp.content.decode()
    row = _row(p)
    body = re.search(rf'<tbody class="line-row\s*" id="line-{p["line"].pk}">(.*?)</tbody>', html, re.S).group(1)
    # D3: the row leads with the status word, then the line, then the 18px sentence
    lead = re.search(r'<td class="status-lead" rowspan="2">(.*?)</td>', body, re.S)
    assert lead and f'class="status-word {_status_class(row.status_key)}">{row.status}</span>' in lead.group(1)
    assert body.index("status-lead") < body.index('<th scope="rowgroup"') < body.index(row.sentence[:20])
    assert f'<td colspan="4" class="status-cell"><span class="sentence">{row.sentence}</span></td>' in body
    # evidence: merged pace + units with the raw counts as tooltip, yield + top code, bottleneck + coverage
    assert f'<td title="{row.pace.tooltip}" data-label="Pace">' in body
    assert row.pace.throughput_display in body and re.search(r"\d+% · [+−]\d+ units", row.pace.throughput_display)
    assert f"{row.yield_.display} · top code Voids" in _text(body)
    assert row.bottleneck_coverage_text in body and "bottleneck chosen from 2 of 2 stations" in body
    # D22 sparkline: accessible name is the API's trend_text
    assert f'<svg class="sparkline" role="img" aria-label="{row.trend_text}"' in body
    assert row.trend_text.startswith("Pace 14-day trend: ")
    # the line name is a real button that drills down (D18)
    assert re.search(r'<button type="button" class="button-link drill-toggle line-name" hx-get="/line/\d+/stations/'
                     r'\?date=2026-09-15" hx-target="#drill-line-\d+"', body)


def _status_class(key):
    return {"behind": "status-behind", "yield_problem": "status-yield", "on_pace": "status-ok"}.get(
        key, "status-neutral")


def test_no_bordered_boxes(p, client):
    html = _get(client, p["users"]["ops_manager"], "/line/").content.decode()
    assert not re.search(r'class="[^"]*\b(card|box|panel|tile)\b', html)
    css = (Path(settings.BASE_DIR) / "web/static/css/screens.css").read_text()
    for sel in (".line-health", ".line-health tbody", ".line-row", ".status-row"):
        for m_ in re.finditer(re.escape(sel) + r"\s*\{([^}]*)\}", css):
            assert "border:" not in m_.group(1) and "box-shadow" not in m_.group(1)
    assert "box-shadow" not in css


def test_behind_shows_colour_with_the_word_and_the_bottleneck_sentence(p, client):
    m.TaktConfig.objects.filter(production_date=D).update(demand_units=3000)
    row = _row(p)
    assert row.status == "Behind"
    html = _get(client, p["users"]["ops_manager"], "/line/").content.decode()
    assert '<span class="status-word status-behind">Behind</span>' in html
    assert row.bottleneck.sentence in html  # appended only when behind (API)


def test_no_demand_set_pace_is_a_dash(p, client):
    m.TaktConfig.objects.filter(production_date=D).delete()
    html = _get(client, p["users"]["ops_manager"], "/line/").content.decode()
    assert '<span class="status-word status-neutral">No demand set</span>' in html
    assert ">— · no demand set</td>" in html.replace('<span class="evidence-label">Pace: </span>', "")
    assert "0%" not in _text(re.search(r'data-label="Pace">(.*?)</td>', html, re.S).group(1))


def test_pace_unavailable_is_neutral(p, client):
    p["s2"].count_trusted = False
    p["s2"].save()
    row = _row(p)
    assert (row.status, row.status_key) == ("Pace unavailable", "pace_unavailable")
    html = _get(client, p["users"]["ops_manager"], "/line/").content.decode()
    assert '<span class="status-word status-neutral">Pace unavailable</span>' in html
    assert "— · unavailable: final-station counts unverified" in html


def test_stale_hub_rows_get_the_word_and_muted_values(p, client, monkeypatch):
    p["hub"].last_new_transaction_at = t(p["hub"], 6, 40, d=D + timedelta(days=1))
    p["hub"].save()
    html = _get(client, p["users"]["ops_manager"], "/line/").content.decode()
    assert re.search(rf'<tbody class="line-row is-stale" id="line-{p["line"].pk}">', html)
    assert re.search(r'<button[^>]*line-name[^>]*>Cores Line 1<span class="visually-hidden">: stations</span>'
                     r'</button> <span class="stale-tag"[^>]*>stale</span>', html)


def test_status_neutral_style_has_no_performance_colour():
    css = (Path(settings.BASE_DIR) / "web/static/css/screens.css").read_text()
    rule = re.search(r"\.status-neutral\s*\{([^}]*)\}", css).group(1)
    assert "var(--ink)" in rule and "--behind" not in rule and "--yield" not in rule


def test_sparkline_geometry():
    trend = [api.TrendPoint(D - timedelta(days=13 - i), 0.9 + i * 0.01, 0.8 if i != 5 else None) for i in range(14)]
    s = sparkline.build(trend, {"mean": 0.95, "sd": 0.02}, {"mean": 0.8, "sd": 0.01})
    assert len(s.pace.segments) == 1 and len(s.pace.points.split()) == 14
    assert len(s.yield_.segments) == 2  # a missing day breaks the line
    assert s.pace.last[0] == sparkline.WIDTH - sparkline.PAD  # today's point marked at the right edge
    assert s.pace.band and s.yield_.band
    empty = sparkline.build([api.TrendPoint(D, None, None)])
    assert empty.empty


def test_admin_is_refused_and_lead_sees_only_their_shift(p, client):
    resp = _get(client, p["users"]["admin"], "/line/")
    assert resp.status_code == 403 and "This area isn't in your assignment" in resp.content.decode()
    html = _get(client, p["users"]["lead"], "/line/").content.decode()
    assert "1st shift, Tue Sep 15" in html
    assert _get(client, p["users"]["lead"], "/line/", shift_id=p["second"].pk).status_code == 403


def test_station_drill_down(p, client):
    resp = _get(client, p["users"]["ops_manager"], f"/line/{p['line'].pk}/stations/", date="2026-09-15")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "<html" not in html
    assert "Stations on Cores Line 1 · Tue Sep 15, all shifts" in html
    assert html.index("Injection 3") < html.index("Sort")
    assert 'Verified <span class="badge-glyph" aria-hidden="true">✓</span>' in html
    rows = {r.station.name: r for r in api.station_rows(p["line"], api.Window.day(D))}
    assert rows["Sort"].pace.tooltip in html  # raw good / required in the drill-down (D4)
    assert "Part C-4471" in html
    assert re.search(r'hx-get="/station/\d+/operators/\?date=2026-09-15&amp;part_id=\d+"', html)
    assert 'data-load-error="Couldn\'t load operators."' in html
    assert 'Loading operators…' in html


def test_station_drill_down_unverified_station(p, client):
    p["s1"].count_trusted = False
    p["s1"].save()
    html = _get(client, p["users"]["ops_manager"], f"/line/{p['line'].pk}/stations/", date="2026-09-15").content.decode()
    block = re.search(rf'<tbody id="station-{p["s1"].pk}">(.*?)</tbody>', html, re.S).group(1)
    assert "Counts unverified" in block and "Counts unverified, keep tally." in block
    assert "operators/" not in block  # no operator drill for a station that is not count-trusted


def _operators(client, p, user, page="/line/", **params):
    client.force_login(user)
    token = TOKEN_RE.search(client.get(page).content.decode()).group(1)
    return client.get(f"/station/{p['s2'].pk}/operators/",
                      {"date": "2026-09-15", "part_id": p["part"].pk, TOKEN_PARAM: token, **params},
                      HTTP_HX_REQUEST="true")


def test_operator_drill_down_named_for_builder_a_to_z(p, client):
    html = _operators(client, p, p["users"]["builder"]).content.decode()
    names = [n for n in re.findall(r'<th scope="row" data-label="Operator">([^<]+)</th>', html)]
    assert names == NAMES + ["Unmapped operator (CT-901)"]  # A-Z by name, never by rate
    assert "−20% vs median" in html and "tenure &lt; 90 days" in html
    assert "Time with no transaction" in html and "Upstream output gap" in html
    assert "This looks wrong" not in html  # disputes are raised on My Shift
    thead = re.search(r"<thead>(.*?)</thead>", html, re.S).group(1)
    assert "aria-sort" not in html and "<a " not in thead and "<button" not in thead  # rate columns not sortable


def test_operator_drill_down_letters_for_ops_manager(p, client):
    html = _operators(client, p, p["users"]["ops_manager"]).content.decode()
    assert "Names hidden until HR approves operator-level views (Gate 1)." in html
    labels = re.findall(r'<th scope="row" data-label="Operator">([^<]+)</th>', html)
    assert len(labels) == 4 and not any(n in html for n in NAMES)
    letters = [lab.split()[-1] for lab in labels]
    assert letters == sorted(letters, key=lambda x: (len(x), x))


@pytest.mark.parametrize("grade,rate,txn", [("amber", "n/a", False), ("not_graded", "n/a", False), ("red", None, False)])
def test_operator_drill_down_follows_matrix(p, client, grade, rate, txn):
    m.StationPartGrade.objects.filter(station=p["s2"]).update(grade=grade, days_of_history=9)
    html = _operators(client, p, p["users"]["builder"]).content.decode()
    assert ("Rate vs median" in html) is (rate is not None)
    if rate:
        assert '<td data-label="Rate vs median">n/a</td>' in html
    assert ("Time with no transaction" in html) is txn
    assert "Upstream output gap" in html and "Tenure" in html  # every count-trusted row (R3-5)
    assert "vs median" not in html.replace("Rate vs median", "")
    if grade == "not_graded":
        assert "Not graded yet (needs 4 weeks; 9 days so far)" in html


def test_operator_drill_down_not_count_trusted(p, client):
    p["s2"].count_trusted = False
    p["s2"].save()
    html = _operators(client, p, p["users"]["builder"]).content.decode()
    assert "Counts unverified, keep tally." in html
    assert not any(n in html for n in NAMES) and "<table" not in html


def test_operator_drill_down_for_a_lead_is_their_shift_only(p, client):
    resp = _operators(client, p, p["users"]["lead_3rd"])
    html = resp.content.decode()
    assert resp.status_code == 200 and "Your shift only." in html
    assert "No jobs at Sort · part C-4471 in this window." in html  # the crew worked 1st shift
    assert not any(n in html for n in NAMES)
