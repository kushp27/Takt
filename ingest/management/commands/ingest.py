"""manage.py ingest [--nightly | --days N] [--hub CODE]

Trailing 48 h each run; trailing 7 days with --nightly (02:00 plant time, eng review D9).
Every run also re-reads all still-open Jobs by MES ID (eng review D3). --days N is a
backfill (first load): it re-reads the trailing N days and is recorded as mode "backfill".

Each hub runs in its own transaction. Any failure for a hub (MES unavailable, a bug)
records a failed IngestRun, commits nothing for that hub, and moves on to the next hub;
the command then exits non-zero. A hub whose previous run still holds its lock is skipped.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError

from core.models import Hub
from ingest.service import IngestFailed, run_ingest


class Command(BaseCommand):
    help = "Pull MES transactions into Takt (read-only)."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--nightly", action="store_true", help="Re-read the trailing 7 days.")
        mode.add_argument("--days", type=int, help="Backfill: re-read the trailing N days.")
        parser.add_argument("--hub", help="Only this hub code.")

    def handle(self, *args, nightly, hub, days, **opts):
        if nightly and days:
            raise CommandError("--nightly and --days are mutually exclusive.")
        if days is not None and days <= 0:
            raise CommandError("--days must be a positive number of days.")
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
            if run is None:
                self.stdout.write(f"ingest {h.code}: skipped, another ingest run holds this hub's lock")
                continue
            self.stdout.write(
                f"ingest {h.code} ({run.mode}): read {run.rows_read}, changed {run.rows_changed}, open re-read "
                f"{run.open_jobs_reread}, skipped {run.rows_skipped}, {run.query_seconds:.2f} s in MES queries"
            )
            if run.skipped_detail:
                self.stdout.write(f"  {run.skipped_detail}")
        if failed:
            raise CommandError("ingest failed for " + "; ".join(failed))
