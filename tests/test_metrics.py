"""Golden numbers for the metric layer: pace, takt, yield, RTY, status and sentence, zero/missing
demand, kiln batches, operator rows, "worth a conversation", job list and the shift table.

small_plant(): Cores Line 1 with Injection 3 (seq 1) -> Sort (seq 2), part C-4471 (Core-S), and
shifts every day: 1st 07-15 (break 11:30-12:00), 2nd 15-23 (break 19:30-20:00), 3rd 23-07
(break 03:30-04:00). Each shift plans 7.5 h = 27,000 s; a normal day plans 81,000 s.
"""

from datetime import date, timedelta

import pytest

from core import models as m
from core.shifts import ShiftStamper
from metrics import api
from metrics import sentences as S
from metrics.api import Scope, Window
from metrics.windows import HubPlan
from tests import factories as f
from tests import golden as g
from tests.mes_fixtures import small_plant

D = date(2026, 10, 6)  # a Tuesday


def _t(hub, d, hh, mm=0, fold=0):
    return f.local(hub, d.year, d.month, d.day, hh, mm, fold=fold)


def _end_of(hub, d):
    return _t(hub, d + timedelta(days=1), 12)


@pytest.fixture
def plant():
    p = small_plant()
    g.route(p["family"], p["s1"])
    g.route(p["family"], p["s2"])
    p["part_b"] = f.part("C-4472", p["family"], p["hub"])
    p["op"] = f.alias(f.operator(), p["hub"])
    p["void"] = g.scrap_code(p["hub"], "SYN-VOID", p["s1"], "Voids")
    p["short"] = g.scrap_code(p["hub"], "SYN-SHORT", p["s1"], "Short shot")
    p["chip"] = g.scrap_code(p["hub"], "SYN-CHIP", p["s2"], "Chips")
    return p


def _first_shift_day(p, d=D):
    """1st shift on d: Injection 3 makes 300 good (scrap 20 short shots); Sort makes 270 good:
    part A 170 (scrap 10 voids), part B 100 (scrap 15 voids + 5 chips). Demand Core-S 900/day."""
    hub, s1, s2, a, b, op = p["hub"], p["s1"], p["s2"], p["part"], p["part_b"], p["op"]
    g.demand(p["line"], p["family"], d, 900)
    for hh in (8, 9, 10):
        g.job(s1, a, op, _t(hub, d, hh), qty=100)
    g.job(s2, a, op, _t(hub, d, 9, 30), qty=170)
    g.job(s2, b, op, _t(hub, d, 13), qty=100)
    g.scrap(s1, a, p["short"], _t(hub, d, 9), 20)
    g.scrap(s2, a, p["void"], _t(hub, d, 10), 10)
    g.scrap(s2, b, p["void"], _t(hub, d, 13, 30), 15)
    g.scrap(s2, b, p["chip"], _t(hub, d, 14), 5)


# ---------------------------------------------------------------- takt and planned time


def test_takt_time_and_planned_time_net_of_breaks(plant):
    plan = HubPlan(plant["hub"])
    assert plan.day_planned_seconds(D) == 81000
    assert plan.takt_seconds(D, 900) == 90.0
    assert plan.takt_seconds(D, 0) is None and plan.takt_seconds(D, None) is None
    # mid-1st-shift at 12:30: 5.5 h elapsed minus the 30-minute break = 5 h
    w = Window.for_shift("1st", D, as_of=_t(plant["hub"], D, 12, 30))
    assert plan.elapsed_seconds(w, D) == 18000


def test_dst_fall_back_night_is_an_hour_longer():
    p = small_plant()
    plan = HubPlan(p["hub"])
    sat = date(2026, 10, 31)  # 3rd shift 23:00 CDT -> 07:00 CST = 9 h, minus 30 min break
    assert plan.day_planned_seconds(sat) == 27000 + 27000 + 30600
    assert plan.takt_seconds(sat, 846) == 100.0


def test_dst_spring_forward_night_is_an_hour_shorter():
    p = small_plant()
    plan = HubPlan(p["hub"])
    sat = date(2026, 3, 7)  # 3rd shift 23:00 CST -> 07:00 CDT = 7 h, minus 30 min break
    assert plan.day_planned_seconds(sat) == 27000 + 27000 + 23400
    assert plan.takt_seconds(sat, 774) == 100.0


# ---------------------------------------------------------------- pace


def test_line_and_station_pace_golden(plant):
    _first_shift_day(plant)
    hub = plant["hub"]
    [row] = api.line_health([plant["line"]], D, shift="1st", as_of=_end_of(hub, D))
    # required in the 1st shift = 900 x 27,000 / 81,000 = 300; Sort (final) made 270 good
    assert (row.pace.good, row.pace.required, row.pace.display) == (270, 300, "90%")
    assert row.pace.throughput_display == "90% · −30 units"
    assert row.pace.tooltip == "270 good of 300 required"
    assert (row.status, row.status_key) == ("Behind", "behind")
    assert row.bottleneck.station == plant["s2"]
    assert row.bottleneck_coverage_text == "bottleneck chosen from 2 of 2 stations"
    assert row.sentence == ("Pace and yield in their usual range. "
                            "Bottleneck: Sort at 90% (chosen from 2 of 2 stations).")

    # the whole day: 900 required, 270 good
    [day] = api.line_health([plant["line"]], D, as_of=_end_of(hub, D))
    assert (day.pace.required, day.pace.display, day.pace.throughput_display) == (900, "30%", "30% · −630 units")

    # live, mid-shift at 12:30: 5 h of 22.5 planned have elapsed -> 200 required
    [live] = api.line_health([plant["line"]], D, shift="1st", as_of=_t(hub, D, 12, 30))
    assert (live.pace.required, live.pace.display, live.status) == (200, "135%", "On pace")


def test_station_rows_golden(plant):
    _first_shift_day(plant)
    rows = api.station_rows(plant["line"], Window.for_shift("1st", D, as_of=_end_of(plant["hub"], D)))
    inj, sort = rows
    assert (inj.pace.display, inj.good, inj.scrap, inj.yield_.display, inj.is_bottleneck) == ("100%", 300, 20, "93.8%", False)
    assert (sort.pace.display, sort.good, sort.scrap, sort.yield_.display, sort.is_bottleneck) == ("90%", 270, 30, "90.0%", True)
    # per-part station yield for the drill-down
    assert [(r.part.hub_part_code, r.good, r.scrap, round(r.yield_.value, 4)) for r in sort.parts] == [
        ("C-4471", 170, 10, 0.9444), ("C-4472", 100, 20, 0.8333)]
    assert sort.top_scrap.label == "Voids" and sort.top_scrap.quantity == 25
    # no grades yet: not graded, displayed like amber
    assert (sort.grade, sort.cell.badge.detail) == ("not_graded", "Not graded yet (needs 4 weeks; 0 days so far)")


def test_midnight_crossing_third_shift_is_one_production_day(plant):
    hub, s2, a, op = plant["hub"], plant["s2"], plant["part"], plant["op"]
    g.demand(plant["line"], plant["family"], D, 900)
    g.job(s2, a, op, _t(hub, D, 23, 30), qty=50)  # completes 23:40 on D
    g.job(s2, a, op, _t(hub, D + timedelta(days=1), 1, 20), qty=70)  # completes 01:30 on D+1
    assert set(m.Job.objects.values_list("production_date", flat=True)) == {D}
    # live at 02:00 on D+1: 3 h of the 3rd shift elapsed -> 900 x 10,800 / 81,000 = 120 required
    [row] = api.line_health([plant["line"]], D, shift="3rd", as_of=_t(hub, D + timedelta(days=1), 2))
    assert (row.pace.good, row.pace.required, row.pace.throughput_display, row.status) == (
        120, 120, "100% · +0 units", "On pace")


def test_dst_fall_back_third_shift_pace(plant):
    hub, s2, a, op = plant["hub"], plant["s2"], plant["part"], plant["op"]
    sat = date(2026, 10, 31)
    g.demand(plant["line"], plant["family"], sat, 846)  # takt 100 s
    # one job in each occurrence of the repeated 01:00 hour
    g.job(s2, a, op, _t(hub, sat + timedelta(days=1), 1, 10, fold=0), qty=153)
    g.job(s2, a, op, _t(hub, sat + timedelta(days=1), 1, 10, fold=1), qty=153)
    [row] = api.line_health([plant["line"]], sat, shift="3rd", as_of=_end_of(hub, sat))
    # 3rd shift planned 30,600 s of 84,600 -> 846 x 30,600 / 84,600 = 306 required
    assert (row.pace.good, round(row.pace.required, 6), row.pace.display) == (306, 306, "100%")


def test_dst_spring_forward_third_shift_pace(plant):
    hub, s2, a, op = plant["hub"], plant["s2"], plant["part"], plant["op"]
    sat = date(2026, 3, 7)
    g.demand(plant["line"], plant["family"], sat, 774)  # takt 100 s
    g.job(s2, a, op, _t(hub, sat, 23, 30), qty=117)
    g.job(s2, a, op, _t(hub, sat + timedelta(days=1), 3, 10), qty=117)  # 03:10 CDT, after the jump
    [row] = api.line_health([plant["line"]], sat, shift="3rd", as_of=_end_of(hub, sat))
    # 3rd shift planned 23,400 s of 77,400 -> 774 x 23,400 / 77,400 = 234 required
    assert (row.pace.good, round(row.pace.required, 6), row.pace.display) == (234, 234, "100%")


# ---------------------------------------------------------------- zero and missing demand (T5, eng review D7)


def test_zero_demand_pace_is_a_dash_never_zero_or_infinity(plant):
    _first_shift_day(plant)
    m.TaktConfig.objects.update(demand_units=0)
    [row] = api.line_health([plant["line"]], D, shift="1st", as_of=_end_of(plant["hub"], D))
    assert (row.pace.value, row.pace.display, row.pace.reason, row.pace.throughput_display) == (
        None, "—", "no_demand", "— · no demand set")
    assert (row.status, row.status_key) == ("No demand set", "no_demand")
    assert [(gap.family.name, gap.production_date, gap.reason, gap.text) for gap in row.no_demand] == [
        ("Core-S", D, "zero", "no demand set")]
    assert row.sentence == "No demand set for Core-S. Yield in its usual range."
    rows = api.station_rows(plant["line"], Window.for_shift("1st", D, as_of=_end_of(plant["hub"], D)))
    assert [(r.pace.display, r.pace.reason_text) for r in rows] == [("—", "no demand set")] * 2
    # zero demand is legitimate: not in the admin's missing-row warning
    assert api.missing_demand([plant["line"]], D, D, include_zero=False) == []


def test_missing_demand_row_dash_and_admin_warning(plant, client, settings):
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}
    _first_shift_day(plant)
    m.TaktConfig.objects.all().delete()
    [row] = api.line_health([plant["line"]], D, shift="1st", as_of=_end_of(plant["hub"], D))
    assert (row.pace.display, row.status) == ("—", "No demand set")
    gaps = api.missing_demand([plant["line"]], D, D + timedelta(days=1))
    assert [(gp.production_date, gp.reason) for gp in gaps] == [(D, "missing"), (D + timedelta(days=1), "missing")]

    admin = f.user("boss", None, superuser=True)
    client.force_login(admin)
    resp = client.get("/admin/core/taktconfig/", follow=True)
    text = resp.content.decode()
    assert "No demand set" in text and "Cores Line 1" in text and "Core-S" in text


def test_missing_row_for_one_family_sets_status_but_pace_keeps_its_number(plant):
    """Judgment call: one family missing (admin error) -> "No demand set" status; the other family's
    demand still gives a pace number, and the missing family is named in the sentence."""
    _first_shift_day(plant)
    other = f.family("Core-L")
    g.route(other, plant["s1"])
    g.route(other, plant["s2"])
    [row] = api.line_health([plant["line"]], D, shift="1st", as_of=_end_of(plant["hub"], D))
    assert (row.pace.display, row.status) == ("90%", "No demand set")
    assert row.sentence.startswith("No demand set for Core-L. ")


# ---------------------------------------------------------------- yield and RTY


def test_rty_pools_parts_per_station_and_multiplies_stations(plant):
    _first_shift_day(plant)
    [row] = api.line_health([plant["line"]], D, shift="1st", as_of=_end_of(plant["hub"], D))
    # Injection 3: 300 / 320 = 0.9375; Sort: 270 / 300 = 0.9 (pooled over parts); RTY = 0.84375
    assert row.yield_.value == 0.84375
    assert row.yield_.display == "84.4%"
    assert (row.yield_.stations_included, row.yield_.stations_total) == (2, 2)
    assert (row.top_scrap.label, row.top_scrap.quantity, row.top_scrap.cause_station) == ("Voids", 25, plant["s1"])


def test_gross_quantity_hub_nets_scrap_at_the_station(plant):
    hub = plant["hub"]
    hub.quantity_is_gross = True
    hub.save()
    g.demand(plant["line"], plant["family"], D, 900)
    g.job(plant["s2"], plant["part"], plant["op"], _t(hub, D, 9), qty=300)
    g.scrap(plant["s2"], plant["part"], plant["void"], _t(hub, D, 10), 30)
    [row] = api.line_health([plant["line"]], D, shift="1st", as_of=_end_of(hub, D))
    # good = 300 - 30 = 270; yield = 270 / (270 + 30) = 0.9
    assert (row.pace.good, row.pace.display, row.yield_.stations[1][1].value) == (270, "90%", 0.9)


def test_yield_problem_status_and_sentence_against_14_day_baseline(plant):
    hub, s1, s2, a, op = plant["hub"], plant["s1"], plant["s2"], plant["part"], plant["op"]
    for i in range(0, 15):
        d = D - timedelta(days=i)
        g.demand(plant["line"], plant["family"], d, 900)
        g.job(s1, a, op, _t(hub, d, 8), qty=900)
        g.job(s2, a, op, _t(hub, d, 9), qty=900)
        # baseline days alternate yield 0.9 (scrap 100) and 0.8 (scrap 225): mean 0.85, SD 0.0519
        # the window day: 300 voids -> 0.75, 10 pts under the mean
        g.scrap(s2, a, plant["void"], _t(hub, d, 10), 300 if i == 0 else (100 if i % 2 == 0 else 225))
    [row] = api.line_health([plant["line"]], D, as_of=_end_of(hub, D))
    assert (row.pace.display, row.yield_.display) == ("100%", "75.0%")
    assert (row.pace_class, row.yield_class) == ("normal", "low")
    assert round(row.yield_baseline["mean"], 6) == 0.85 and row.yield_baseline["days"] == 14
    assert (row.status, row.status_key) == ("Yield problem", "yield_problem")
    assert row.sentence == ("Pace normal, yield 10.0 pts under baseline, mostly Voids. "
                            "Look upstream at Injection 3, not at pace.")
    assert len(row.trend) == 14 and row.trend[-1].production_date == D
    assert row.trend_text == "Pace 14-day trend: 100% to 100%; yield 80% to 75%"


def test_final_station_not_count_trusted_suppresses_line_pace_only(plant):
    _first_shift_day(plant)
    plant["s2"].count_trusted = False
    plant["s2"].save()
    [row] = api.line_health([plant["line"]], D, shift="1st", as_of=_end_of(plant["hub"], D))
    assert (row.pace.display, row.pace.reason_text) == ("—", "unavailable: final-station counts unverified")
    assert (row.status, row.final_station_trusted) == ("Pace unavailable", False)
    assert row.sentence == "Pace unavailable: final-station counts unverified. Yield in its usual range."
    assert row.yield_.value == 0.84375  # line yield still shows
    assert row.bottleneck.station == plant["s1"]  # chosen over trusted stations only
    assert row.bottleneck_coverage_text == "bottleneck chosen from 1 of 2 stations"
    sort = api.station_rows(plant["line"], Window.for_shift("1st", D, as_of=_end_of(plant["hub"], D)))[1]
    assert (sort.pace.display, sort.pace.reason_text, sort.cell.badge.text) == ("—", "Counts unverified", "Counts unverified ?")


@pytest.mark.parametrize("pc,yc,expected", [
    ("normal", "normal", "Pace and yield in their usual range."),
    ("normal", "low", "Pace normal, yield 4.0 pts under baseline, mostly Voids. Look upstream at Injection 3, not at pace."),
    ("normal", "high", "Pace normal, yield 4.0 pts above baseline."),
    ("low", "normal", "Pace 6.0 pts under its 14-day baseline, yield in its usual range."),
    ("low", "low", "Pace 6.0 pts and yield 4.0 pts under baseline, mostly Voids. Look upstream at Injection 3."),
    ("low", "high", "Pace 6.0 pts under baseline while yield is 4.0 pts above it."),
    ("high", "normal", "Pace 6.0 pts above its 14-day baseline, yield in its usual range."),
    ("high", "low", "Pace 6.0 pts above baseline but yield 4.0 pts under it, mostly Voids. Look upstream at Injection 3."),
    ("high", "high", "Pace 6.0 pts and yield 4.0 pts above baseline."),
])
def test_nine_sentence_templates(pc, yc, expected):
    pd = -0.06 if pc == "low" else 0.06
    yd = -0.04 if yc == "low" else 0.04
    assert S.sentence(status_word="On pace", pace_class=pc, yield_class=yc, pace_delta=pd, yield_delta=yd,
                      top_code="Voids", cause_station="Injection 3", bottleneck="Bottleneck: X.") == expected


def test_status_precedence_and_bottleneck_only_when_behind():
    assert S.status(no_demand=True, pace_value=0.5, pace_suppressed=False, yield_class="low") == "No demand set"
    assert S.status(no_demand=False, pace_value=0.99, pace_suppressed=False, yield_class="low") == "Behind"
    assert S.status(no_demand=False, pace_value=1.0, pace_suppressed=False, yield_class="low") == "Yield problem"
    assert S.status(no_demand=False, pace_value=1.2, pace_suppressed=False, yield_class="high") == "On pace"
    assert S.status(no_demand=False, pace_value=None, pace_suppressed=True, yield_class="low") == "Pace unavailable"
    kw = dict(pace_class="low", yield_class="normal", pace_delta=-0.1, yield_delta=0, bottleneck="Bottleneck: Sort.")
    assert S.sentence(status_word="Behind", **kw).endswith(" Bottleneck: Sort.")
    assert "Bottleneck" not in S.sentence(status_word="Yield problem", **kw)
    assert S.classify(0.9, [1.0]) == "normal"  # under 2 baseline days: no classification


# ---------------------------------------------------------------- kiln (batch station)


def test_kiln_batch_station_metrics_and_red_column(plant):
    hub, line = plant["hub"], plant["line"]
    kiln = f.station(line, 3, "Fire (kiln)", station_type=m.Station.BATCH)
    g.route(plant["family"], kiln)
    g.demand(line, plant["family"], D, 900)
    loader = f.alias(f.operator(hire_date=D - timedelta(days=400)), hub)
    for qty in (40, 50, 60):  # load 1: 08:00-10:20
        g.job(kiln, plant["part"], loader, _t(hub, D, 8), minutes=140, qty=qty)
    for qty in (50, 50):  # load 2: 12:00-14:20
        g.job(kiln, plant["part"], loader, _t(hub, D, 12), minutes=140, qty=qty)
    w = Window.for_shift("1st", D, as_of=_end_of(hub, D))
    k = api.station_rows(line, w)[2]
    assert (k.batch.batches, k.batch.shifts, k.batch.units, k.batch.cycle_minutes) == (2, 1, 250, 140.0)
    assert (k.batch.batches_per_shift, k.batch.units_per_batch) == (2.0, 125.0)
    assert (k.pace.good, k.pace.required, k.pace.display) == (250, 300, "83%")
    assert (k.grade, k.cell.badge.text) == ("red", "Excluded –")
    [grp] = api.operator_groups(kiln, window=w)
    [r] = grp.rows
    assert (r.jobs, r.quantity, r.rate_per_hour, r.rate_text, r.transaction_gap_minutes, r.tenure_under_90) == (
        5, 250, None, None, None, False)
    [line_row] = api.line_health([line], D, shift="1st", as_of=_end_of(hub, D))
    assert line_row.pace.display == "83%"  # the kiln is the final station here


# ---------------------------------------------------------------- operator groups, worth a conversation


@pytest.fixture
def crew(plant):
    """Sort, part A, 1st shift on D, graded green. Injection 3 (upstream) completes a job every
    10 minutes from 07:10 to 10:00 and nothing after: upstream output gap = 90 + 180 = 270 min."""
    hub, s1, s2, a = plant["hub"], plant["s1"], plant["s2"], plant["part"]
    g.demand(plant["line"], plant["family"], D, 900)
    m.StationPartGrade.objects.create(station=s2, part=a, grade="green", d_share=0, c_share=0, o_share=0,
                                      job_count=100, days_of_history=40, computed_at=_t(hub, D, 0))
    feeder = plant["op"]
    for i in range(18):
        g.job(s1, a, feeder, _t(hub, D, 7) + timedelta(minutes=10 * i), qty=5)
    ops = {
        "new": f.alias(f.operator(hire_date=D - timedelta(days=30)), hub),
        "mid": f.alias(f.operator(hire_date=D - timedelta(days=1000)), hub),
        "fast": f.alias(f.operator(hire_date=D - timedelta(days=2000)), hub),
        "temp": f.alias(None, hub, "CT-901"),
    }
    # new: 48 units in 3 h = 16/h; mid: 140 in 7 h = 20/h; fast: 168 in 7 h = 24/h. Median 20/h.
    g.job(s2, a, ops["new"], _t(hub, D, 8), minutes=120, qty=32)
    g.job(s2, a, ops["new"], _t(hub, D, 10), minutes=60, qty=16)
    g.job(s2, a, ops["mid"], _t(hub, D, 7), minutes=270, qty=90)
    g.job(s2, a, ops["mid"], _t(hub, D, 12), minutes=150, qty=50)
    g.job(s2, a, ops["fast"], _t(hub, D, 7), minutes=270, qty=108)
    g.job(s2, a, ops["fast"], _t(hub, D, 12), minutes=150, qty=60)
    g.job(s2, a, ops["temp"], _t(hub, D, 12), minutes=60, qty=10)  # unmapped: counted, never compared
    return dict(plant, ops=ops, window=Window.for_shift("1st", D, as_of=_end_of(hub, D)))


def _row_tuple(r):
    return (r.jobs, r.quantity, r.excluded_jobs, r.rate_per_hour, None if r.rate_vs_median is None else round(r.rate_vs_median, 6),
            r.rate_text, r.transaction_gap_minutes, r.upstream_gap_minutes, r.tenure_days, r.tenure_under_90,
            r.can_dispute)


def test_operator_groups_green_golden(crew):
    [grp] = api.operator_groups(crew["s2"], crew["part"], crew["window"])
    assert (grp.grade, grp.cell.badge.text, grp.jobs, grp.quantity) == ("green", "Verified ✓", 7, 366)
    assert (grp.median_rate_per_hour, grp.takt_seconds, grp.takt_rate_per_hour) == (20.0, 90.0, 40.0)
    rows = {r.alias.pk: r for r in grp.rows}
    o = crew["ops"]
    assert _row_tuple(rows[o["new"].pk]) == (2, 48, 0, 16.0, -0.2, None, 270.0, 270.0, 30, True, True)
    assert rows[o["new"].pk].rate_vs_median_display == "−20% vs median"
    assert _row_tuple(rows[o["mid"].pk]) == (2, 140, 0, 20.0, 0.0, None, 30.0, 270.0, 1000, False, True)
    assert _row_tuple(rows[o["fast"].pk]) == (2, 168, 0, 24.0, 0.2, None, 30.0, 270.0, 2000, False, True)
    temp = rows[o["temp"].pk]
    assert (temp.unmapped, temp.operator, temp.hub_operator_id) == (True, None, "CT-901")
    assert _row_tuple(temp) == (1, 10, 0, None, None, None, 390.0, 270.0, None, None, True)
    # identity never leaves as a name: rows carry Operator objects only
    assert rows[o["new"].pk].operator == o["new"].operator


@pytest.mark.parametrize("grade,rate_text", [("amber", "n/a"), ("not_graded", "n/a"), ("red", None)])
def test_operator_groups_non_green_hide_rates_and_transaction_gap(crew, grade, rate_text):
    m.StationPartGrade.objects.update(grade=grade)
    [grp] = api.operator_groups(crew["s2"], crew["part"], crew["window"])
    r = next(r for r in grp.rows if r.alias == crew["ops"]["new"])
    assert _row_tuple(r) == (2, 48, 0, None, None, rate_text, None, 270.0, 30, True, True)
    assert grp.median_rate_per_hour is None
    assert api.worth_a_conversation(Scope.for_lines([crew["line"]]), crew["window"]) == []


def test_not_count_trusted_station_has_no_operator_rows(crew):
    crew["s2"].count_trusted = False
    crew["s2"].save()
    [grp] = api.operator_groups(crew["s2"], crew["part"], crew["window"])
    assert (grp.rows, grp.jobs, grp.quantity, grp.message) == ([], None, None, "Counts unverified, keep tally.")
    assert grp.cell.badge.text == "Counts unverified ?"


def test_worth_a_conversation_golden(crew):
    groups = api.worth_a_conversation(Scope.for_area(crew["line"].area), crew["window"])
    [grp] = groups  # Injection 3 is not graded: no comparisons there
    assert (grp.station, grp.part) == (crew["s2"], crew["part"])
    [item] = grp.items  # only "new" is 10%+ under the median; the unmapped temp is never listed
    assert item.row.alias == crew["ops"]["new"]
    assert [r.text for r in item.reasons] == [
        "−20% vs median", "tenure < 90 days", "upstream output gap 270 min", "270 min with no transaction"]
    with pytest.raises(ValueError):
        api.worth_a_conversation(Scope(), crew["window"])  # never plant-wide


def test_worth_a_conversation_caps_at_three(crew):
    hub, s2, a = crew["hub"], crew["s2"], crew["part"]
    slow = [f.alias(f.operator(hire_date=D - timedelta(days=500)), hub) for _ in range(3)]
    for op in slow:
        g.job(s2, a, op, _t(hub, D, 13), minutes=60, qty=10)  # 10/h
    for _ in range(5):
        g.job(s2, a, f.alias(f.operator(), hub), _t(hub, D, 13), minutes=60, qty=30)  # 30/h
    # mapped rates 10, 10, 10, 16, 20, 24, 30 x5 -> median 24; five are 10%+ under; three listed
    [grp] = api.worth_a_conversation(Scope.for_lines([crew["line"]]), crew["window"])
    assert [i.row.alias for i in grp.items] == slow
    assert [i.reasons[0].text for i in grp.items] == ["−58% vs median"] * 3


def test_exclusions_drop_from_rates_or_counts_and_job_list(crew):
    mid = crew["ops"]["mid"]
    j1, j2 = m.Job.objects.filter(alias=mid).order_by("started_at")
    j2.excluded = True
    j2.exclusion_reason = "time looks wrong"
    j2.save()
    [grp] = api.operator_groups(crew["s2"], crew["part"], crew["window"])
    r = next(r for r in grp.rows if r.alias == mid)
    assert (r.jobs, r.quantity, r.excluded_jobs, r.rate_per_hour) == (2, 140, 1, 20.0)  # 90 in 4.5 h
    j1.excluded = j1.excluded_from_counts = True
    j1.save()
    [grp] = api.operator_groups(crew["s2"], crew["part"], crew["window"])
    r = next(r for r in grp.rows if r.alias == mid)
    assert (r.jobs, r.quantity, r.excluded_jobs, r.rate_per_hour) == (1, 50, 2, None)

    items = api.job_list(Scope(alias=mid), crew["window"])
    assert [(i.job.quantity, i.counted, i.in_rates, i.excluded, i.exclusion_reason) for i in items] == [
        (90, False, False, True, ""), (50, True, False, True, "time looks wrong")]
    with pytest.raises(ValueError):
        api.job_list(Scope(), crew["window"])


def test_job_list_shows_open_jobs_but_never_counts_them(crew):
    hub = crew["hub"]
    new = crew["ops"]["new"]
    g.job(crew["s2"], crew["part"], new, _t(hub, D, 13), open_=True)
    items = api.job_list(Scope(alias=new), crew["window"])
    assert [(i.is_open, i.counted) for i in items] == [(False, True), (False, True), (True, False)]
    [grp] = api.operator_groups(crew["s2"], crew["part"], crew["window"])
    assert next(r for r in grp.rows if r.alias == new).jobs == 2


def test_open_job_covers_time_in_live_transaction_gap(crew):
    hub = crew["hub"]
    new = crew["ops"]["new"]
    g.job(crew["s2"], crew["part"], new, _t(hub, D, 12), open_=True)
    live = Window.for_shift("1st", D, as_of=_t(hub, D, 13))
    [grp] = api.operator_groups(crew["s2"], crew["part"], live)
    r = next(r for r in grp.rows if r.alias == new)
    # 07:00-13:00 minus the break = 330 min planned; jobs cover 08:00-11:00 and 12:00-13:00 (open)
    assert r.transaction_gap_minutes == 330 - 180 - 60


# ---------------------------------------------------------------- shift comparison


def test_shift_comparison_three_shifts(plant):
    hub, s2, a, op = plant["hub"], plant["s2"], plant["part"], plant["op"]
    g.demand(plant["line"], plant["family"], D, 900)
    g.job(s2, a, op, _t(hub, D, 9), qty=300)
    g.job(s2, a, op, _t(hub, D, 16), qty=150)
    [row] = api.shift_comparison([plant["line"]], D, as_of=_end_of(hub, D))
    assert [(c.shift_name, c.pace.display, c.pace.throughput_display) for c in row.shifts] == [
        ("1st", "100%", "100% · +0 units"), ("2nd", "50%", "50% · −150 units"), ("3rd", "0%", "0% · −300 units")]
    assert row.shifts[0].window.label == "1st shift, Tue Oct 6"


def test_no_shift_scheduled_and_not_started(plant):
    hub = plant["hub"]
    m.NonWorkingDay.objects.create(hub=hub, date=D)
    [row] = api.line_health([plant["line"]], D, as_of=_end_of(hub, D))
    assert (row.status, row.status_key, row.pace.display, row.sentence) == (None, "no_shift", "—", "No shift scheduled.")
    m.NonWorkingDay.objects.all().delete()
    g.demand(plant["line"], plant["family"], D, 900)
    [row] = api.line_health([plant["line"]], D, shift="2nd", as_of=_t(hub, D, 14))
    assert (row.status_key, row.pace.reason_text) == ("not_started", "shift not started")
