"""Shared fixtures for tests that need the synthetic MES (schema fake_mes).

The synthetic MES is reached over its own connection, like a real hub, so rows written
here are committed (outside the test transaction); the fixture drops the schema after.
"""

from datetime import datetime, time

import pytest
from django.core.management import call_command

from core import models as m
from ingest import fake_mes
from tests import factories as f


@pytest.fixture
def mes():
    fake_mes.create_schema()
    yield fake_mes
    fake_mes.drop_schema()


@pytest.fixture
def demo(mes):
    """seed_demo + a 42-day synthetic MES ending Mon 2026-10-12 06:00 local, ingested, graded."""
    end = "2026-10-12T06:00"
    call_command("seed_demo", end=end, verbosity=0)
    call_command("load_fixture_mes", end=end, days=42, seed=7, verbosity=0)
    from ingest.service import run_ingest
    from datetime import timedelta
    from ingest.cli import parse_end

    for hub in m.Hub.objects.all():
        run_ingest(hub, lookback=timedelta(days=400), now=parse_end(end))
    return parse_end(end)


def mes_row(txn, station, part, op, start, complete, qty=10, hub="cores", last_modified=None):
    """One raw synthetic MES job row (naive local timestamps, like the MES stores them)."""
    return (txn, hub, station, part, op, start, complete, qty, last_modified or complete or start)


def small_plant(trusted=True, code="cores"):
    """One hub, one line, two stations, one part, 3 shifts (America/Chicago). Returns a dict."""
    hub = f.hub(code)
    hub.adapter = m.Hub.ADAPTER_FIXTURE
    hub.save()
    line = f.line(f"{code.title()} Line 1", hub)
    s1 = f.station(line, 1, "Injection 3", count_trusted=trusted)
    s2 = f.station(line, 2, "Sort", count_trusted=trusted)
    fam = f.family(f"{code.title()}-S")
    part = f.part(f"{code[0].upper()}-4471", fam, hub)
    first = f.shift(hub, "1st", time(7), time(15), breaks=[(time(11, 30), time(12))])
    second = f.shift(hub, "2nd", time(15), time(23), breaks=[(time(19, 30), time(20))])
    third = f.shift(hub, "3rd", time(23), time(7), breaks=[(time(3, 30), time(4))])
    return dict(hub=hub, line=line, s1=s1, s2=s2, family=fam, part=part, first=first, second=second, third=third)


def naive(y, mo, d, hh=0, mm=0):
    return datetime(y, mo, d, hh, mm)
