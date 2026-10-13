"""manage.py fixture_tick [--now ISO]: append synthetic MES transactions up to now so a
running demo stays fresh. A no-op unless TAKT_FIXTURE_LIVE=true. Only hubs whose adapter
is "fixture" are ticked. Runs from docker/crontab before ingest."""

import os

from django.core.management.base import BaseCommand

from ingest import fake_mes
from ingest.cli import parse_end


class Command(BaseCommand):
    help = "Append synthetic MES transactions up to now (only when TAKT_FIXTURE_LIVE=true)."

    def add_arguments(self, parser):
        parser.add_argument("--now", help="Tick up to this ISO datetime instead of now (naive = plant local).")

    def handle(self, *args, now=None, **opts):
        if os.environ.get("TAKT_FIXTURE_LIVE", "").lower() not in ("1", "true", "yes", "on"):
            self.stdout.write("fixture_tick: TAKT_FIXTURE_LIVE is not true; nothing to do.")
            return
        if not fake_mes.schema_exists():
            self.stdout.write("fixture_tick: no fake_mes schema; run load_fixture_mes first.")
            return
        for hub, (jobs, scrap) in fake_mes.tick(now=parse_end(now)).items():
            self.stdout.write(f"fixture_tick {hub}: {jobs} job rows written, {scrap} scrap rows added")
