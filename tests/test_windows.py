"""D-T10: per-role default time windows (design review D21) and the shared picker.

- Line Health opens on the last completed production day (all shifts): ops manager and builder.
- Area opens on the last completed shift, with the current shift one click away: supervisor.
- My Shift opens on the lead's current shift, live; a 3rd shift crossing midnight is one window.
The clock is pinned through ops.freshness.current_time; no test depends on the real date.
"""

from datetime import timedelta

import pytest
from django.test import RequestFactory

from tests.screen_world import D, build, pin, t
from web import windows

rf = RequestFactory()


def _req(user, **get):
    r = rf.get("/", get)
    r.user = user
    return r


@pytest.fixture
def p():
    return build()


@pytest.mark.parametrize("role", ["ops_manager", "builder"])
def test_line_health_default_is_last_completed_production_day(p, monkeypatch, role):
    hub = p["hub"]
    # Wed 12:00: Tuesday's 3rd shift ended at 07:00 Wed, so Tuesday is the last completed day.
    pin(monkeypatch, t(hub, 12, d=D + timedelta(days=1)))
    w = windows.line_health_window(_req(p["users"][role]), [hub])
    assert (w.date_from, w.date_to, w.shift, w.is_default, w.live) == (D, D, None, True, False)
    assert w.label == "Tue Sep 15, all shifts"
    # Wed 06:59: Tuesday's 3rd shift is still running, so Monday is the last completed day.
    pin(monkeypatch, t(hub, 6, 59, d=D + timedelta(days=1)))
    assert windows.line_health_window(_req(p["users"][role]), [hub]).date_to == D - timedelta(days=1)


def test_supervisor_area_default_is_last_completed_shift_with_current_one_click_away(p, monkeypatch, client):
    hub = p["hub"]
    pin(monkeypatch, t(hub, 16))  # Tue 16:00: 1st shift ended at 15:00; 2nd is running
    w = windows.area_window(_req(p["users"]["supervisor"]), p["area"], hub)
    assert (w.date_to, w.shift, w.is_default) == (D, p["first"], True)
    assert w.label == "1st shift, Tue Sep 15"
    client.force_login(p["users"]["supervisor"])
    html = client.get(f"/area/{p['area'].pk}/").content.decode()
    assert "1st shift, Tue Sep 15" in html
    # the current shift (2nd) is one click away in the shared picker
    assert f'href="/area/{p["area"].pk}/?date=2026-09-15&amp;shift_id={p["second"].pk}">Current shift</a>' in html


def test_lead_my_shift_default_is_current_shift_live(p, monkeypatch, client):
    hub = p["hub"]
    pin(monkeypatch, t(hub, 12))
    w, state = windows.my_shift_window(_req(p["users"]["lead"]), p["first"])
    assert (w.date_to, w.shift, w.live, w.is_default) == (D, p["first"], True, True)
    assert w.label == "1st shift, Tue Sep 15 (live)"
    client.force_login(p["users"]["lead"])
    resp = client.get("/", follow=True)
    assert resp.redirect_chain[-1][0] == f"/area/{p['area'].pk}/shift/?shift_id={p['first'].pk}"
    assert "1st shift, Tue Sep 15 (live)" in resp.content.decode()


def test_third_shift_crossing_midnight_opens_as_one_window_for_its_lead(p, monkeypatch):
    """Test plan (re-review): at 02:00 Wednesday the 3rd shift that started Tuesday 23:00 is Tuesday's."""
    pin(monkeypatch, t(p["hub"], 2, d=D + timedelta(days=1)))
    w, state = windows.my_shift_window(_req(p["users"]["lead_3rd"]), p["third"])
    assert (w.date_from, w.date_to, w.live) == (D, D, True)
    assert w.label == "3rd shift, Tue Sep 15 (live)"


def test_lead_before_own_shift_starts_sees_the_last_run(p, monkeypatch):
    pin(monkeypatch, t(p["hub"], 6, 30))  # 1st shift starts 07:00
    w, state = windows.my_shift_window(_req(p["users"]["lead"]), p["first"])
    assert (w.date_to, w.live, state.scheduled_today) == (D - timedelta(days=1), False, True)


def test_explicit_date_and_shift_override_the_default(p, monkeypatch):
    pin(monkeypatch, t(p["hub"], 12))
    w = windows.area_window(_req(p["users"]["supervisor"], date="2026-09-10", shift_id=p["third"].pk),
                            p["area"], p["hub"])
    assert (w.date_to, w.shift, w.is_default) == (D - timedelta(days=5), p["third"], False)
    assert w.params() == {"date": "2026-09-10", "shift_id": p["third"].pk}


def test_lead_is_held_to_their_own_shift(p, monkeypatch):
    pin(monkeypatch, t(p["hub"], 12, d=D + timedelta(days=1)))
    lead = p["users"]["lead"]
    assert windows.line_health_window(_req(lead), [p["hub"]]).shift == p["first"]
    assert windows.area_window(_req(lead), p["area"], p["hub"]).shift == p["first"]
    with pytest.raises(windows.OutOfScopeShift):
        windows.area_window(_req(lead, shift_id=p["second"].pk), p["area"], p["hub"])


def test_line_health_days_range(p, monkeypatch):
    pin(monkeypatch, t(p["hub"], 12, d=D + timedelta(days=1)))
    w = windows.line_health_window(_req(p["users"]["ops_manager"], date="2026-09-15", days="14"), [p["hub"]])
    assert (w.date_from, w.date_to, w.days) == (D - timedelta(days=13), D, 14)
    assert w.label == "Sep 2 – Sep 15, all shifts"


def test_shared_picker_partial_on_every_screen(p, monkeypatch, client):
    pin(monkeypatch, t(p["hub"], 12, d=D + timedelta(days=1)))
    client.force_login(p["users"]["builder"])
    for url in ("/line/", f"/area/{p['area'].pk}/", f"/area/{p['area'].pk}/shift/?shift_id={p['first'].pk}"):
        html = client.get(url).content.decode()
        assert 'class="window-picker no-print"' in html, url
        assert '<input type="date" name="date"' in html and '<select name="shift_id">' in html, url
