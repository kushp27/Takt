"""Helpers for golden-number tests: hand-placed jobs and scrap, stamped exactly like ingest stamps
them (ingest.service.stamp_instant: by START time, user decision)."""

from datetime import timedelta

from core import models as m
from core.shifts import ShiftStamper
from ingest.service import stamp_instant

_n = {"i": 0}


def _id(prefix):
    _n["i"] += 1
    return f"{prefix}{_n['i']}"


def job(station, part, alias, start, minutes=10, qty=10, complete=None, open_=False, **kw):
    """A job at `station` starting at aware `start`, lasting `minutes` (or ending at `complete`)."""
    hub = station.line.hub
    completed = None if open_ else (complete or start + timedelta(minutes=minutes))
    shift, pdate = ShiftStamper(hub).stamp(stamp_instant(start, completed))
    return m.Job.objects.create(
        hub=hub, mes_id=_id("G"), alias=alias, station=station, part=part, started_at=start,
        completed_at=completed, quantity=qty, shift=shift, production_date=pdate, **kw,
    )


def scrap(station, part, code, at, qty):
    hub = station.line.hub
    shift, pdate = ShiftStamper(hub).stamp(at)
    return m.ScrapEvent.objects.create(
        hub=hub, mes_id=_id("S"), occurred_at=at, shift=shift, production_date=pdate, part=part,
        station_found=station, scrap_code=code, quantity=qty,
    )


def scrap_code(hub, code, cause=None, description=""):
    return m.ScrapCode.objects.get_or_create(
        hub=hub, code=code, defaults={"description": description or code, "cause_station_hint": cause}
    )[0]


def demand(line, family, d, units):
    return m.TaktConfig.objects.create(line=line, family=family, production_date=d, demand_units=units)


def route(family, station, visits=1):
    return m.Routing.objects.create(family=family, station=station, visit_count=visits)
