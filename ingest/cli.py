"""Small helpers shared by the ingest management commands."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from ingest import fixture_spec as spec


def parse_end(value):
    """--end: an ISO datetime; naive means local plant time (America/Chicago for the demo). Default now."""
    if not value:
        return datetime.now(timezone.utc)
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(spec.TIMEZONE))
    return dt.astimezone(timezone.utc)
