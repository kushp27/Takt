"""manage.py restamp_shifts [--hub CODE] [--from YYYY-MM-DD]: re-stamp shift and
production_date on Jobs and ScrapEvents after a ShiftCalendar change (eng review D2)."""

from datetime import date

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.models import Hub
from ingest.service import restamp


class Command(BaseCommand):
    help = "Re-stamp shift + production_date after ShiftCalendar changes."

    def add_arguments(self, parser):
        parser.add_argument("--hub")
        parser.add_argument("--from", dest="from_date", type=date.fromisoformat)

    @transaction.atomic
    def handle(self, *args, hub, from_date, **opts):
        hubs = Hub.objects.all()
        if hub:
            hubs = hubs.filter(code=hub)
            if not hubs.exists():
                raise CommandError(f"No hub with code {hub!r}")
        for h in hubs:
            jobs, scrap = restamp(h, from_date)
            self.stdout.write(f"restamp {h.code}: {jobs} job(s), {scrap} scrap event(s) re-stamped")
