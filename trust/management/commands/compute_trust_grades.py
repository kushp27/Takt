"""manage.py compute_trust_grades [--as-of ISO]

Nightly at 02:30 plant time, after the 02:00 7-day re-read (docker/crontab):
1. grades every station-and-part over the trailing 4 weeks into StationPartGrade (Component 4);
2. auto-revokes count trust where confirmed count disputes exceed 5% of a station's jobs over
   2 weeks, writing a CountTrustChange row (Component 4a).
The audit (`manage.py audit`) runs the same grading code over history.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone as djtz

from core.models import Station
from ingest.cli import parse_end
from trust.count_trust import auto_revoke
from trust.grades import compute_grades, save_grades


class Command(BaseCommand):
    help = "Compute trust grades (StationPartGrade) and auto-revoke count trust."

    def add_arguments(self, parser):
        parser.add_argument("--as-of", help="Grade as of this ISO datetime (naive = plant time). Default now.")

    def handle(self, *args, as_of, **opts):
        as_of_dt = parse_end(as_of) if as_of else djtz.now()
        stations = list(Station.objects.select_related("line__hub"))
        results = compute_grades(stations, as_of=as_of_dt)
        save_grades(results, stations=stations, computed_at=djtz.now())
        counts = {}
        for r in results:
            counts[r.grade] = counts.get(r.grade, 0) + 1
        summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing to grade"
        self.stdout.write(f"trust grades: {len(results)} station-parts ({summary})")
        for chk in auto_revoke(as_of=as_of_dt):
            self.stdout.write(self.style.WARNING(
                f"count trust revoked: {chk.station} ({chk.confirmed_count_disputes}/{chk.jobs} jobs, {chk.share:.1%})"
            ))
