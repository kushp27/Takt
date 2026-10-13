"""Golden numbers: trust grade (Component 4), count-trust auto-revoke and the display matrix (4a)."""

from datetime import date, datetime, time, timedelta
from io import StringIO

import pytest
from django.core.management import call_command

from core import models as m
from disputes.models import Dispute
from tests import factories as f
from tests import golden as g
from tests.mes_fixtures import small_plant
from trust.count_trust import auto_revoke, check_station
from trust.grades import compute_grades, days_of_history, grade_from_shares, majority_grade, save_grades
from trust.matrix import UNVERIFIED_MESSAGE, badge, cell

AS_OF = date(2026, 10, 12)
GRADE_DATES = [date(2026, 9, 15) + timedelta(days=3 * i) for i in range(10)]  # Sep 15 ... Oct 12: 28 days


# ---------------------------------------------------------------- grade boundaries


@pytest.mark.parametrize("share,expected", [
    (0.099, "green"), (0.10, "amber"), (0.25, "amber"), (0.251, "red"),
])
@pytest.mark.parametrize("which", ["d", "c", "o"])
def test_grade_boundaries(share, expected, which):
    shares = {"d": 0.0, "c": 0.0, "o": 0.0, which: share}
    assert grade_from_shares(**shares, days_of_history=28) == expected


def test_batch_is_red_by_definition_even_when_clean_and_new():
    assert grade_from_shares(0, 0, 0, batch=True, days_of_history=3) == "red"


def test_under_four_weeks_is_not_graded():
    assert grade_from_shares(0, 0, 0, days_of_history=27) == "not_graded"
    assert grade_from_shares(0, 0, 0, days_of_history=28) == "green"


def test_days_of_history_counts_from_the_first_job():
    assert days_of_history(date(2026, 10, 4), AS_OF) == 9
    assert days_of_history(date(2026, 9, 15), AS_OF) == 28
    assert days_of_history(None, AS_OF) == 0


def test_history_looks_back_eight_weeks_only(graded_plant):
    """A part that ran 50 days ago and again recently has 50 days of history (no gap rule); the
    March DST slice, outside the 8-week look-back, does not count (Sort/B has 9 days)."""
    p = graded_plant
    g.job(p["s1"], p["part"], p["ops"]["a"], _at(p["hub"], AS_OF - timedelta(days=49), 8))
    res = _by_key(compute_grades(as_of=f.local(p["hub"], 2026, 10, 12, 23, 59)))
    assert res[("Injection 3", p["part"].pk)].days_of_history == 50
    assert res[("Sort", p["part_b"].pk)].days_of_history == 9


def test_majority_grade_by_volume_ties_go_cautious():
    assert majority_grade({"green": 600, "amber": 300, "red": 100}) == "green"
    assert majority_grade({"green": 300, "amber": 300}) == "amber"
    assert majority_grade({}) is None


# ---------------------------------------------------------------- graded end to end (SQL view)


def _at(hub, d, hh, mm=0):
    return f.local(hub, d.year, d.month, d.day, hh, mm)


@pytest.fixture
def graded_plant():
    p = small_plant()
    hub, line = p["hub"], p["line"]
    kiln = f.station(line, 3, "Kiln", station_type=m.Station.BATCH)
    part_a, part_b = p["part"], f.part("C-4472", p["family"], hub)
    ops = {k: f.alias(f.operator(), hub) for k in ("a", "b", "c", "d", "k")}

    # (s1, A): 20 jobs, two per date; one 0.5-minute and one 40-minute job -> D = 2/20 = 10% -> amber.
    for i, d in enumerate(GRADE_DATES):
        g.job(p["s1"], part_a, ops["a"], _at(hub, d, 8), minutes=40 if i == 0 else 10)
        g.job(p["s1"], part_a, ops["a"], _at(hub, d, 9), minutes=0.5 if i == 1 else 10)
    # (s2, A): 20 clean jobs; one completes at 14:45, in the last 30 min of the 1st shift -> C = 5% -> green.
    for i, d in enumerate(GRADE_DATES):
        g.job(p["s2"], part_a, ops["b"], _at(hub, d, 8))
        g.job(p["s2"], part_a, ops["b"], _at(hub, d, 14, 35) if i == 4 else _at(hub, d, 10))
    # (s1, B): on 3 of 10 dates the operator's two jobs overlap -> O = 6/20 = 30% -> red.
    for i, d in enumerate(GRADE_DATES):
        g.job(p["s1"], part_b, ops["c"], _at(hub, d, 11))
        g.job(p["s1"], part_b, ops["c"], _at(hub, d, 11, 5) if i < 3 else _at(hub, d, 13))
    # (s2, B): the March DST night, then Oct 4-12 only -> 9 days of history -> not graded.
    g.job(p["s2"], part_b, ops["d"], f.local(hub, 2026, 3, 8, 1, 0))
    for day in range(4, 13):
        g.job(p["s2"], part_b, ops["d"], _at(hub, date(2026, 10, day), 12, 30))
    # kiln: 4 jobs per load sharing start and complete timestamps -> batch -> red.
    for d in GRADE_DATES:
        for _ in range(4):
            g.job(kiln, part_a, ops["k"], _at(hub, d, 8), minutes=140, qty=50)
    return dict(p, kiln=kiln, part_b=part_b, ops=ops)


def _by_key(results):
    return {(r.station.name, r.part_id): r for r in results}


def test_trust_grade_golden_numbers(graded_plant):
    p = graded_plant
    as_of = f.local(p["hub"], 2026, 10, 12, 23, 59)
    res = _by_key(compute_grades(as_of=as_of))
    a, b = p["part"].pk, p["part_b"].pk

    r = res[("Injection 3", a)]
    assert (r.grade, r.d_share, r.c_share, r.o_share, r.job_count, r.days_of_history) == ("amber", 0.10, 0.0, 0.0, 20, 28)

    r = res[("Sort", a)]
    assert (r.grade, r.d_share, r.c_share, r.o_share, r.job_count, r.days_of_history) == ("green", 0.0, 0.05, 0.0, 20, 28)

    r = res[("Injection 3", b)]
    assert (r.grade, r.d_share, r.c_share, r.o_share, r.job_count) == ("red", 0.0, 0.0, 0.30, 20)

    r = res[("Sort", b)]  # the March job is outside the 4-week window and the 8-week look-back
    assert (r.grade, r.job_count, r.days_of_history) == ("not_graded", 9, 9)
    assert badge(r.grade, r.days_of_history).detail == "Not graded yet (needs 4 weeks; 9 days so far)"

    r = res[("Kiln", a)]
    # 4 jobs share each completion timestamp (3 others) -> C 100%; they overlap each other -> O 100%.
    assert (r.grade, r.c_share, r.o_share, r.batch_share, r.job_count) == ("red", 1.0, 1.0, 1.0, 40)


def test_compute_trust_grades_command_persists_and_audit_agrees(graded_plant):
    p = graded_plant
    out = StringIO()
    call_command("compute_trust_grades", as_of="2026-10-12T23:59", stdout=out)
    assert "5 station-parts" in out.getvalue()
    rows = {(s.station.name, s.part.hub_part_code): s for s in m.StationPartGrade.objects.select_related("station", "part")}
    assert {k: v.grade for k, v in rows.items()} == {
        ("Injection 3", "C-4471"): "amber", ("Sort", "C-4471"): "green", ("Injection 3", "C-4472"): "red",
        ("Sort", "C-4472"): "not_graded", ("Kiln", "C-4471"): "red",
    }
    assert rows[("Sort", "C-4472")].days_of_history == 9

    audit = StringIO()
    call_command("audit", as_of="2026-10-12T23:59", stdout=audit)
    text = audit.getvalue()
    assert "Not graded yet (needs 4 weeks; 9 days so far)" in text
    assert "Kiln: 100% of jobs (already typed batch)" in text.replace(f"{p['line']} · ", "")
    assert "Counts only #" in text and "Verified ✓" in text and "Excluded –" in text


def test_audit_suggests_flow_station_that_behaves_like_a_batch():
    p = small_plant()
    op = f.alias(f.operator(), p["hub"])
    for d in GRADE_DATES:
        for _ in range(3):
            g.job(p["s2"], p["part"], op, _at(p["hub"], d, 8), minutes=60)
    out = StringIO()
    call_command("audit", as_of="2026-10-12T23:59", stdout=out)
    assert "Sort: 100% of jobs (typed FLOW: review in admin)" in out.getvalue()


def test_save_grades_downgrades_station_parts_with_no_recent_jobs(graded_plant):
    p = graded_plant
    stations = list(m.Station.objects.all())
    save_grades(compute_grades(stations, as_of=f.local(p["hub"], 2026, 10, 12, 23)), stations=stations)
    # Six weeks later nothing ran: no stale green survives.
    later = f.local(p["hub"], 2026, 11, 30, 12)
    save_grades(compute_grades(stations, as_of=later), stations=stations)
    grades = dict(m.StationPartGrade.objects.values_list("station__name", "grade").filter(part=p["part"]))
    assert grades == {"Injection 3": "not_graded", "Sort": "not_graded", "Kiln": "red"}


# ---------------------------------------------------------------- count trust auto-revoke


def _dispute(job, user, status, affects_counts=True):
    return Dispute.objects.create(job=job, reason=Dispute.WRONG_QUANTITY, raised_by=user, status=status,
                                  affects_counts=affects_counts)


def test_count_trust_auto_revokes_above_five_percent_of_two_weeks():
    p = small_plant()
    st, hub = p["s1"], p["hub"]
    op = f.alias(f.operator(), hub)
    lead = f.user("lead1", "lead")
    jobs = [g.job(st, p["part"], op, _at(hub, date(2026, 10, 1 + i // 4), 8 + i % 4)) for i in range(40)]
    old = g.job(st, p["part"], op, _at(hub, date(2026, 9, 20), 8))  # outside the 2 weeks
    as_of = f.local(hub, 2026, 10, 12, 23)

    _dispute(jobs[0], lead, Dispute.CONFIRMED)
    _dispute(jobs[1], lead, Dispute.CONFIRMED)
    _dispute(jobs[1], lead, Dispute.CONFIRMED)  # same job twice counts once
    _dispute(jobs[2], lead, Dispute.REJECTED)
    _dispute(jobs[3], lead, Dispute.OPEN)
    _dispute(jobs[4], lead, Dispute.CONFIRMED, affects_counts=False)  # a rate dispute, not a count dispute
    _dispute(old, lead, Dispute.CONFIRMED)
    chk = check_station(st, as_of)
    assert (chk.jobs, chk.confirmed_count_disputes, chk.share, chk.revoke) == (40, 2, 0.05, False)
    assert auto_revoke(as_of=as_of) == []

    _dispute(jobs[5], lead, Dispute.CONFIRMED)  # 3/40 = 7.5%
    revoked = auto_revoke(as_of=as_of)
    assert [r.station for r in revoked] == [st]
    st.refresh_from_db()
    assert st.count_trusted is False
    change = m.CountTrustChange.objects.get(station=st)
    assert change.action == m.CountTrustChange.AUTO_REVOKE
    assert "3 confirmed count disputes on 40 jobs (7.5%)" in change.evidence
    assert auto_revoke(as_of=as_of) == []  # already revoked: no second row
    assert m.CountTrustChange.objects.filter(station=st).count() == 1


# ---------------------------------------------------------------- display matrix (Component 4a, D16, R3-5)

FIELDS = ("badge_text", "show_operator_rows", "show_counts", "show_rates", "rate_text", "show_comparisons",
          "show_transaction_gap", "show_upstream_gap", "show_tenure", "show_dispute", "in_station_pace",
          "in_bottleneck", "on_print_sheet", "message")

MATRIX = {
    # (grade, count_trusted): (badge, rows, counts, rates, rate_text, comparisons, txn gap, upstream, tenure,
    #                          dispute, station pace, bottleneck, print, message)
    ("green", True): ("Verified ✓", True, True, True, None, True, True, True, True, True, True, True, True, None),
    ("amber", True): ("Counts only #", True, True, False, "n/a", False, False, True, True, True, True, True, True, None),
    ("not_graded", True): ("Not graded yet …", True, True, False, "n/a", False, False, True, True, True, True, True,
                           True, None),
    ("red", True): ("Excluded –", True, True, False, None, False, False, True, True, True, True, True, True, None),
}
UNTRUSTED = ("Counts unverified ?", False, False, False, None, False, False, False, False, False, False, False, False,
             "Counts unverified, keep tally.")
for _g in ("green", "amber", "not_graded", "red"):
    MATRIX[(_g, False)] = UNTRUSTED


@pytest.mark.parametrize("key", list(MATRIX))
def test_display_matrix_cells(key):
    grade, trusted = key
    c = cell(grade, trusted, days_of_history=9)
    got = (c.badge.text,) + tuple(getattr(c, k) for k in FIELDS[1:])
    assert got == MATRIX[key]


def test_matrix_batch_station_is_red_column_and_missing_grade_is_not_graded():
    c = cell("green", True, station_type=m.Station.BATCH)
    assert (c.grade, c.badge.text, c.show_counts, c.show_rates, c.show_transaction_gap) == (
        "red", "Excluded –", True, False, False)
    c = cell(None, True)
    assert (c.grade, c.badge.detail, c.rate_text) == ("not_graded", "Not graded yet (needs 4 weeks; 0 days so far)", "n/a")
    assert UNVERIFIED_MESSAGE == "Counts unverified, keep tally."
    assert cell("amber", False).grade_badge.text == "Counts only #"
    assert cell("green", True).as_dict()["badge"]["text"] == "Verified ✓"


# ---------------------------------------------------------------- start-time stamping (user decision)


def test_c_share_uses_the_shift_that_contains_the_completion():
    """A job started in 1st and closed in the last 20 minutes of 2nd is stamped 1st (start time)
    but counts for C at 2nd's end: C = 1/20."""
    p = small_plant()
    hub, op = p["hub"], f.alias(f.operator(), p["hub"])
    for i, d in enumerate(GRADE_DATES):
        g.job(p["s2"], p["part"], op, _at(hub, d, 8))
        if i == 4:
            late = g.job(p["s2"], p["part"], op, _at(hub, d, 14), complete=_at(hub, d, 22, 45))
        else:
            g.job(p["s2"], p["part"], op, _at(hub, d, 10))
    assert late.shift == p["first"]
    [r] = compute_grades(as_of=f.local(hub, 2026, 10, 12, 23, 59))
    assert (r.c_share, r.d_share, r.o_share) == (0.05, 0.05, 0.0)


def test_batch_station_jobs_do_not_count_as_overlaps_at_flow_stations():
    p = small_plant()
    hub = p["hub"]
    kiln = f.station(p["line"], 3, "Kiln", station_type=m.Station.BATCH)
    op = f.alias(f.operator(), hub)
    for d in GRADE_DATES:
        g.job(kiln, p["part"], op, _at(hub, d, 8), minutes=140)  # the load fires while they work
        g.job(p["s1"], p["part"], op, _at(hub, d, 8, 30))
        g.job(p["s1"], p["part"], op, _at(hub, d, 9))
    res = _by_key(compute_grades(as_of=f.local(hub, 2026, 10, 12, 23, 59)))
    assert res[("Injection 3", p["part"].pk)].o_share == 0.0
    assert res[("Injection 3", p["part"].pk)].grade == "green"
