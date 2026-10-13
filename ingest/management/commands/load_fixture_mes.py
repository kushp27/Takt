"""manage.py load_fixture_mes [--days 42] [--seed 7] [--end ISO]: create or replace the
synthetic MES (schema fake_mes) with deterministic transactions. Run seed_demo first.

Default range: the `--days` before `--end` (default now), plus the 2026-03-07/08
spring-forward night. Pass e.g. --end 2026-11-03T12:00 to cover the 2026-11-01 fall-back.
"""

from django.core.management.base import BaseCommand, CommandError

from ingest import fake_mes
from ingest.cli import parse_end


class Command(BaseCommand):
    help = "Create or replace the synthetic MES (fake_mes schema). SYNTHETIC data only."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=42)
        parser.add_argument("--seed", type=int, default=7)
        parser.add_argument("--end", help="ISO datetime; naive = plant local time. Default now.")

    def handle(self, *args, days, seed, end, **opts):
        try:
            counts = fake_mes.load(days=days, seed=seed, end=parse_end(end))
        except RuntimeError as e:
            raise CommandError(str(e)) from e
        for hub, (jobs, scrap) in counts.items():
            self.stdout.write(f"fake_mes {hub}: {jobs} job rows, {scrap} scrap rows")
        self.stdout.write(self.style.SUCCESS("Synthetic MES loaded. Next: manage.py ingest --days 400"))
