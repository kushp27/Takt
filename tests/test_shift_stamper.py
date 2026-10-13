"""ShiftStamper (cached, used by ingest and restamp_shifts) must agree with core.shifts.stamp()."""

from datetime import date, datetime, time, timedelta, timezone

from core import shifts
from core.models import NonWorkingDay
from tests import factories as f


def test_stamper_agrees_with_stamp_across_midnight_dst_and_holidays():
    hub = f.hub("cores", tz="America/Chicago")
    f.shift(hub, "1st", time(7), time(15), weekdays="012345", breaks=[(time(11, 30), time(12))])
    f.shift(hub, "2nd", time(15), time(23), weekdays="012345")
    f.shift(hub, "3rd", time(23), time(7), weekdays="012345", breaks=[(time(3, 30), time(4))])
    NonWorkingDay.objects.create(hub=hub, date=date(2026, 10, 28))
    stamper = shifts.ShiftStamper(hub)
    t = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 11, 4, 0, 0, tzinfo=timezone.utc)  # covers the 2026-11-01 fall-back
    while t < end:
        assert stamper.stamp(t) == shifts.stamp(hub, t), t
        t += timedelta(minutes=20)
