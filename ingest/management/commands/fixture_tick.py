"""manage.py fixture_tick: append synthetic MES transactions up to now so a running demo
stays fresh. A no-op unless TAKT_FIXTURE_LIVE=true. Runs from docker/crontab before ingest."""

import os

from django.core.management.base import BaseCommand

from ingest import fake_mes


class Command(BaseCommand):
    help = "Append synthetic MES transactions up to now (only when TAKT_FIXTURE_LIVE=true)."

    def handle(self, *args, **opts):
        if os.environ.get("TAKT_FIXTURE_LIVE", "").lower() not in ("1", "true", "yes", "on"):
            self.stdout.write("fixture_tick: TAKT_FIXTURE_LIVE is not true; nothing to do.")
            return
        if not fake_mes.schema_exists():
            self.stdout.write("fixture_tick: no fake_mes schema; run load_fixture_mes first.")
            return
        for hub, (jobs, scrap) in fake_mes.tick().items():
            self.stdout.write(f"fixture_tick {hub}: {jobs} job rows written, {scrap} scrap rows added")
