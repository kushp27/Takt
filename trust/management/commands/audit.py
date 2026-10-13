"""manage.py audit [--area NAME] [--days N] [--as-of ISO] [--save]

The Assignment's audit (design: "the audit is this job run once over history"): the same
trust-grade code as compute_trust_grades, printed as a table for one area, plus
- suggested batch stations: jobs sharing identical start and complete timestamps (Component 5);
- the count-trust list (who set each flag, when, with what evidence).

--days defaults to the grade window (4 weeks) so the audit and the nightly job agree; the
Assignment allows 4 to 8 weeks (--days 56). --save also writes StationPartGrade rows.
"""

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone as djtz

from core.models import Station
from ingest.cli import parse_end
from trust.grades import compute_grades, save_grades
from trust.matrix import badge

BATCH_SUGGEST_SHARE = 0.5  # over half of a station's jobs share start AND complete timestamps


class Command(BaseCommand):
    help = "Audit: trust-grade table, suggested batch stations and the count-trust list."

    def add_arguments(self, parser):
        parser.add_argument("--area", help="Area name (default: all areas).")
        parser.add_argument("--days", type=int, help="Trailing days to grade (default: the grade window).")
        parser.add_argument("--as-of", help="ISO datetime (naive = plant time). Default now.")
        parser.add_argument("--save", action="store_true", help="Also write StationPartGrade rows.")

    def handle(self, *args, area, days, as_of, save, **opts):
        stations = Station.objects.select_related("line__hub", "line__area").order_by("line__name", "sequence")
        if area:
            stations = stations.filter(line__area__name=area)
            if not stations.exists():
                raise CommandError(f"No stations in area {area!r}")
        stations = list(stations)
        as_of_dt = parse_end(as_of) if as_of else djtz.now()
        results = compute_grades(stations, as_of=as_of_dt, window_days=days)
        if save:
            save_grades(results, stations=stations)
        from core.models import Part

        parts = {p.pk: p.hub_part_code for p in Part.objects.all()}
        w = self.stdout.write
        w("Trust grade (Component 4): D = durations <10%/>300% of median, C = end-of-shift or clustered "
          "completes, O = overlapping jobs by the same operator")
        w(f"{'Line':<16} {'Station':<20} {'Part':<10} {'Jobs':>6} {'D':>6} {'C':>6} {'O':>6} {'Days':>5}  Grade")
        for r in sorted(results, key=lambda r: (r.station.line.name, r.station.sequence, parts.get(r.part_id, ""))):
            b = badge(r.grade, r.days_of_history)
            w(f"{r.station.line.name:<16} {r.station.name:<20} {parts.get(r.part_id, r.part_id):<10} "
              f"{r.job_count:>6} {r.d_share:>6.1%} {r.c_share:>6.1%} {r.o_share:>6.1%} {r.days_of_history:>5}  "
              f"{b.detail}")
        w("")
        w("Suggested batch stations (jobs sharing identical start and complete timestamps):")
        by_station = {}
        for r in results:
            n, shared = by_station.get(r.station, (0, 0.0))
            by_station[r.station] = (n + r.job_count, shared + r.batch_share * r.job_count)
        any_suggested = False
        for st, (n, shared) in by_station.items():
            share = shared / n if n else 0
            if share > BATCH_SUGGEST_SHARE:
                any_suggested = True
                typed = "already typed batch" if st.station_type == Station.BATCH else "typed FLOW: review in admin"
                w(f"  {st}: {share:.0%} of jobs ({typed})")
        if not any_suggested:
            w("  none")
        w("")
        w("Count trust (Component 4a):")
        for st in stations:
            state = "count-trusted" if st.count_trusted else "NOT count-trusted (Counts unverified, keep tally)"
            when = f" set {st.count_trust_set_at:%Y-%m-%d}" if st.count_trust_set_at else ""
            w(f"  {st}: {state}{when}. {st.count_trust_evidence or 'No evidence recorded.'}")
