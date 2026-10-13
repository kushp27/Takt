"""D-T5: every interaction state from design table 6a renders with the doc's exact copy.

Each state is a partial under web/templates/partials/states/. The screens include them; these tests
render each one on its own (the screen tests check they appear in place).
"""

import re
from datetime import time, timedelta

from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory

from accounts import policy
from accounts.decorators import render_out_of_scope
from ops import freshness
from tests import factories as f
from tests.identity_world import build_world, set_gate1
from trust import matrix


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def test_stale_state_banner_and_row_tag(monkeypatch):
    """D9: the banner names the stale hub and its last good time; rows get a 'stale' word, muted."""
    cores = f.hub("cores")
    dies = f.hub("dies")
    for h in (cores, dies):
        f.shift(h, "1st", time(7), time(15))
    now = f.local(cores, 2026, 10, 12, 10, 0)
    monkeypatch.setattr("ops.freshness.current_time", lambda: now)
    cores.last_new_transaction_at = now - timedelta(minutes=5)
    cores.save()
    dies.last_new_transaction_at = f.local(dies, 2026, 10, 12, 6, 40)
    dies.save()
    snap = freshness.snapshot(now)
    html = render_to_string("partials/states/stale.html", {"freshness": snap})
    assert _text(html) == "Dies Hub data stopped at 6:40 AM ; numbers for Dies lines may be out of date"
    row = Template('{% load takt_freshness %}<tr class="{% stale_class h %}"><td>94% {% stale_tag h %}</td></tr>')
    assert 'class="is-stale"' in row.render(Context({"freshness": snap, "h": dies}))
    assert ">stale</span>" in row.render(Context({"freshness": snap, "h": dies}))
    assert "stale" not in row.render(Context({"freshness": snap, "h": cores})).replace("is-stale", "")


def test_start_of_shift_state():
    html = render_to_string("partials/states/start_of_shift.html",
                            {"shift_name": "2nd", "started": "6:00 AM", "last_sheet_url": "/area/1/shift/print/"})
    assert "2nd shift started 6:00 AM. First jobs usually close within 20 minutes." in _text(html)
    assert '<a href="/area/1/shift/print/">Last shift\'s sheet</a>' in html


def test_no_shift_scheduled_state():
    html = render_to_string("partials/states/no_shift.html",
                            {"last_shift_url": "/last/", "fourteen_day_url": "/line/?days=14"})
    assert "No shift scheduled today" in _text(html)
    assert '<a href="/last/">Last shift</a>' in html and '<a href="/line/?days=14">14-day view</a>' in html


def test_not_graded_yet_state():
    badge = matrix.badge("not_graded", 9)
    html = render_to_string("partials/states/not_graded.html", {"badge": badge})
    assert "Not graded yet (needs 4 weeks; 9 days so far)" in _text(html)
    # displays like amber: counts shown, rates "n/a"
    cell = matrix.cell("not_graded", True, 9)
    assert cell.show_counts and not cell.show_rates and cell.rate_text == "n/a"


def test_gate1_state_notice(rf):
    w = build_world()
    set_gate1(False)
    req = rf.get("/")
    req.user = w.users["ops_manager"]
    ctx = policy.page_context(req, policy.OperatorScope(area_ids=[w.area_in.pk], date_from=w.today,
                                                        date_to=w.today))
    html = render_to_string("partials/states/gate1.html", {"takt_page": ctx})
    assert "Names hidden until HR approves operator-level views (Gate 1)." in html
    req.user = w.users["builder"]
    named = policy.page_context(req, policy.OperatorScope(area_ids=[w.area_in.pk], date_from=w.today,
                                                          date_to=w.today))
    assert "Gate 1" not in render_to_string("partials/states/gate1.html", {"takt_page": named})


def test_drill_down_loading_state():
    row = render_to_string("partials/states/loading.html", {"noun": "stations", "colspan": 5, "indicator_id": "i1"})
    assert _text(row) == "Loading stations…"
    assert row.startswith('<tr class="loading-row htmx-indicator" id="i1"><td colspan="5">')
    para = render_to_string("partials/states/loading.html", {"noun": "operators", "indicator_id": "i2"})
    assert _text(para) == "Loading operators…" and para.startswith("<p ")


def test_drill_down_failed_state():
    html = render_to_string("partials/states/load_failed.html", {"noun": "stations", "retry_url": "/line/1/stations/"})
    assert _text(html) == "Couldn't load stations. Retry"
    assert 'role="alert"' in html and '<a href="/line/1/stations/">Retry</a>' in html


def test_database_unreachable_state():
    html = render_to_string("partials/states/db_unreachable.html")
    assert _text(html) == ("Takt can't reach its database. Data is safe; try again in a few minutes. "
                           "IT has been alerted via /healthz.")
    page = render_to_string("ops/db_unreachable.html")
    assert _text(html) in _text(page)


def test_out_of_scope_state():
    w = build_world()
    req = RequestFactory().get("/", headers={"HX-Request": "true"})
    req.user = w.users["sup_out"]
    html = render_to_string("partials/states/out_of_scope.html",
                            {"own_area_url": "/area/9/", "is_admin_role": False})
    assert "This area isn't in your assignment." in _text(html) and '<a href="/area/9/">Back to your area</a>' in html
    resp = render_out_of_scope(req)
    assert resp.status_code == 403 and "Back to your area" in resp.content.decode()


def test_every_6a_state_has_a_partial(settings):
    from pathlib import Path

    states = Path(settings.BASE_DIR) / "web/templates/partials/states"
    assert {p.stem for p in states.glob("*.html")} >= {
        "stale", "start_of_shift", "no_shift", "not_graded", "gate1", "loading", "load_failed",
        "db_unreachable", "out_of_scope"}
