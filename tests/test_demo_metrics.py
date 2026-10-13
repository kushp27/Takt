"""Lane A1's demo fixture, end to end through the metric layer: it loads, grades every intended
grade, and Line Health reads Cores near pace and Dies behind (ingest/fixture_spec.py)."""

from datetime import date, timedelta
from io import StringIO
from statistics import mean

from django.core.management import call_command

from core import models as m
from metrics import api
from metrics.api import Scope, Window
from tests.mes_fixtures import demo, mes  # noqa: F401

LAST_DAY = date(2026, 10, 10)  # Sat: the last completed production day before Mon 06:00 (D21)


def test_demo_grades_and_line_health(demo):
    out = StringIO()
    call_command("compute_trust_grades", as_of="2026-10-12T06:00", stdout=out)
    grades = {(g.station.name, g.part.hub_part_code): g.grade
              for g in m.StationPartGrade.objects.select_related("station", "part")}
    by_station = {}
    for (st, part), grade in grades.items():
        by_station.setdefault(st, set()).add(grade)
    # clean stations green; Deburr (end-of-shift closes) and Machining (overlaps) amber;
    # Leach (40% batch-closed) and the kiln red; part C-4473 (12 days old) not graded yet
    assert by_station["Injection 3"] == {"green", "not_graded"}
    assert by_station["Deburr 1"] == {"amber", "not_graded"}
    assert by_station["Leach 2"] == {"red", "not_graded"}
    assert by_station["Fire (kiln)"] == {"red"}
    assert by_station["Machining 2"] == {"amber"}
    assert by_station["Press 1"] == by_station["Final inspection"] == {"green"}
    assert grades[("Post-fire sort", "C-4473")] == "not_graded"
    # the March DST slice does not count as history
    assert set(m.StationPartGrade.objects.filter(part__hub_part_code="C-4471")
               .values_list("days_of_history", flat=True)) == {43}

    lines = {ln.hub.code: ln for ln in m.Line.objects.select_related("hub")}
    days = [LAST_DAY - timedelta(days=i) for i in range(14)]
    paces = {"cores": [], "dies": []}
    statuses = {"cores": [], "dies": []}
    for d in days:
        for row in api.line_health(lines.values(), d, as_of=demo):
            if row.pace.value is not None:
                paces[row.line.hub.code].append(row.pace.value)
            statuses[row.line.hub.code].append(row.status)
    assert 0.97 <= mean(paces["cores"]) <= 1.10
    assert 0.80 <= mean(paces["dies"]) <= 0.95
    assert statuses["dies"].count("Behind") >= 10
    assert statuses["cores"].count("On pace") + statuses["cores"].count("Yield problem") >= 8

    cores, dies = (api.line_health([lines[c]], LAST_DAY, as_of=demo)[0] for c in ("cores", "dies"))
    assert cores.status == "Yield problem"  # the demo's void spike on its last day
    assert cores.top_scrap.label == "Voids" and cores.top_scrap.cause_station.name == "Injection 3"
    assert "Look upstream at Injection 3" in cores.sentence
    assert cores.bottleneck_coverage_text == "bottleneck chosen from 4 of 5 stations"  # Leach 2 untrusted
    assert dies.status == "Behind" and dies.bottleneck.station.name == "Machining 2"
    assert dies.sentence.endswith("(chosen from 3 of 3 stations).")

    gaps = {(g.line.hub.code, g.family.name, g.reason) for g in api.missing_demand(lines.values(), days[-1], LAST_DAY)}
    assert gaps == {("cores", "Core-L", "zero"), ("dies", "Die-A", "missing")}

    # Leach 2 is not count-trusted: no operator rows anywhere on My Shift
    leach = m.Station.objects.get(name="Leach 2")
    w = Window.for_shift("1st", LAST_DAY, demo)
    assert all(g.rows == [] and g.message == "Counts unverified, keep tally." for g in api.operator_groups(leach, window=w))
    kiln = api.station_rows(lines["cores"], Window.day(LAST_DAY, demo))[3]
    assert kiln.batch.batches_per_shift == 3 and kiln.cell.badge.text == "Excluded –"
    for grp in api.worth_a_conversation(Scope.for_area(lines["cores"].area), w):
        assert grades[(grp.station.name, grp.part.hub_part_code)] == "green"
        assert 1 <= len(grp.items) <= 3
        assert all(not i.row.unmapped for i in grp.items)
