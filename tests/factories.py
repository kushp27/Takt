"""Small builders shared by every lane's tests. Plain functions, no factory library."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model

from accounts.models import Profile
from core import models as m

_counter = {"n": 0}


def _next():
    _counter["n"] += 1
    return _counter["n"]


def hub(code="cores", tz="America/Chicago", **kw):
    obj, _ = m.Hub.objects.get_or_create(
        code=code, defaults={"name": f"{code.title()} Hub", "plant_timezone": tz, **kw}
    )
    return obj


def area(name="Area 1"):
    return m.Area.objects.get_or_create(name=name)[0]


def line(name="Line 1", hub_obj=None, area_obj=None):
    return m.Line.objects.create(name=name, hub=hub_obj or hub(), area=area_obj or area())


def station(line_obj, sequence, name=None, station_type=m.Station.FLOW, count_trusted=True):
    return m.Station.objects.create(
        line=line_obj, sequence=sequence, name=name or f"Station {sequence}",
        hub_station_code=f"S{line_obj.pk}-{sequence}", station_type=station_type, count_trusted=count_trusted,
    )


def family(name="Family A"):
    return m.PartFamily.objects.get_or_create(name=name)[0]


def part(code=None, family_obj=None, hub_obj=None):
    return m.Part.objects.create(
        hub=hub_obj or hub(), hub_part_code=code or f"P{_next()}", family=family_obj or family()
    )


def operator(name=None, hire_date=None):
    return m.Operator.objects.create(name=name or f"Operator {_next()}", hire_date=hire_date)


def alias(op=None, hub_obj=None, hub_operator_id=None):
    return m.OperatorAlias.objects.create(
        hub=hub_obj or hub(), hub_operator_id=hub_operator_id or f"OP{_next()}", operator=op
    )


def shift(hub_obj=None, name="1st", start=time(7), end=time(15), weekdays="0123456", breaks=()):
    s = m.Shift.objects.create(hub=hub_obj or hub(), name=name, start_time=start, end_time=end, weekdays=weekdays)
    for b_start, b_end in breaks:
        m.ShiftBreak.objects.create(shift=s, start_time=b_start, end_time=b_end)
    return s


def local(hub_obj, y, mo, d, hh=0, mm=0, fold=0):
    """An aware datetime in the hub's plant timezone."""
    return datetime(y, mo, d, hh, mm, tzinfo=ZoneInfo(hub_obj.plant_timezone), fold=fold)


def job(station_obj, part_obj, alias_obj, started_at, minutes=10, quantity=1, completed=True, **kw):
    return m.Job.objects.create(
        hub=station_obj.line.hub, mes_id=kw.pop("mes_id", f"J{_next()}"), station=station_obj, part=part_obj,
        alias=alias_obj, started_at=started_at,
        completed_at=(started_at + timedelta(minutes=minutes)) if completed else None,
        quantity=quantity, **kw,
    )


def user(username, role, areas=(), shift_obj=None, superuser=False):
    User = get_user_model()
    u = User.objects.create_user(username=username, password="pw-" + username, is_superuser=superuser,
                                 is_staff=superuser or role == Profile.ADMIN)
    if role:
        p = Profile.objects.create(user=u, role=role, shift=shift_obj)
        p.areas.set(areas)
    return u


TODAY = date(2026, 10, 12)
