"""manage.py ingest [--nightly] [--hub CODE] [--days N]

Trailing 48 h each run; trailing 7 days with --nightly (02:00 plant time, eng review D9).
Every run also re-reads all still-open Jobs by MES ID (eng review D3). --days N re-reads
the trailing N days instead (first load / backfill). Exits non-zero if any hub's MES was
unavailable; that hub's run commits nothing (a failed IngestRun row is recorded).
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from core.models import Hub
from ingest.service import IngestFailed, run_ingest


class Command(BaseCommand):
    help = "Pull MES transactions into Takt (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--nightly", action="store_true", help="Re-read the trailing 7 days.")
        parser.add_argument("--hub", help="Only this hub code.")
        parser.add_argument("--days", type=int, help="Re-read the trailing N days (backfill).")

    def handle(self, *args, nightly, hub, days, **opts):
        hubs = Hub.objects.order_by("code")
        if hub:
            hubs = hubs.filter(code=hub)
            if not hubs.exists():
                raise CommandError(f"No hub with code {hub!r}")
        failed = []
        for h in hubs:
            try:
                run = run_ingest(h, nightly=nightly, lookback=timedelta(days=days) if days else None)
            except IngestFailed as e:
                failed.append(f"{h.code}: {e.run.error}")
                self.stderr.write(f"ingest {h.code}: FAILED, nothing committed: {e.run.error}")
                continue
            self.stdout.write(
                f"ingest {h.code}: read {run.rows_read}, changed {run.rows_changed}, open re-read "
                f"{run.open_jobs_reread}, skipped {run.rows_skipped}, {run.query_seconds:.2f} s in MES queries"
            )
            if run.rows_skipped:
                self.stdout.write(f"  skipped (unknown codes): {run.skipped_detail}")
        if failed:
            raise CommandError("MES unavailable for " + "; ".join(failed))
