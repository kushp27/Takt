"""ShiftCalendar arithmetic (eng review D2): midnight-crossing and DST shifts."""

from datetime import date, time, timedelta, timezone

from core import shifts
from core.models import NonWorkingDay
from tests import factories as f


def _hub():
    return f.hub("cores", tz="America/Chicago")


def test_third_shift_crossing_midnight_is_one_production_day():
    hub = _hub()
    third = f.shift(hub, "3rd", time(23), time(7))
    before_midnight = f.local(hub, 2026, 10, 13, 23, 30)
    after_midnight = f.local(hub, 2026, 10, 14, 6, 59)
    assert shifts.stamp(hub, before_midnight) == (third, date(2026, 10, 13))
    assert shifts.stamp(hub, after_midnight) == (third, date(2026, 10, 13))


def test_shift_end_is_exclusive_and_outside_is_unstamped():
    hub = _hub()
    first = f.shift(hub, "1st", time(7), time(15))
    assert shifts.stamp(hub, f.local(hub, 2026, 10, 13, 15, 0)) == (None, date(2026, 10, 13))
    assert shifts.stamp(hub, f.local(hub, 2026, 10, 13, 14, 59)) == (first, date(2026, 10, 13))


def test_dst_fall_back_night_shift_is_nine_real_hours():
    # 2026-11-01 02:00 CDT -> 01:00 CST: the 1:00-2:00 hour repeats.
    hub = _hub()
    third = f.shift(hub, "3rd", time(23), time(7))
    w = shifts.window_for(third, date(2026, 10, 31))
    assert w.end - w.start == timedelta(hours=9)
    first_130 = f.local(hub, 2026, 11, 1, 1, 30, fold=0)
    second_130 = f.local(hub, 2026, 11, 1, 1, 30, fold=1)
    assert second_130.astimezone(timezone.utc) - first_130.astimezone(timezone.utc) == timedelta(hours=1)
    assert shifts.stamp(hub, first_130) == (third, date(2026, 10, 31))
    assert shifts.stamp(hub, second_130) == (third, date(2026, 10, 31))


def test_dst_spring_forward_night_shift_is_seven_real_hours():
    hub = _hub()
    third = f.shift(hub, "3rd", time(23), time(7))
    w = shifts.window_for(third, date(2026, 3, 7))
    assert w.end - w.start == timedelta(hours=7)


def test_planned_seconds_subtracts_breaks_including_after_midnight():
    hub = _hub()
    third = f.shift(hub, "3rd", time(23), time(7), breaks=[(time(2), time(2, 30))])
    w = shifts.window_for(third, date(2026, 10, 13))
    assert w.planned_seconds() == 7.5 * 3600
    assert w.planned_seconds(until=f.local(hub, 2026, 10, 14, 1, 0)) == 2 * 3600


def test_non_working_day_and_weekdays_unschedule():
    hub = _hub()
    f.shift(hub, "1st", time(7), time(15), weekdays="01234")
    NonWorkingDay.objects.create(hub=hub, date=date(2026, 10, 13))
    assert shifts.windows_on(hub, date(2026, 10, 13)) == []  # holiday (Tuesday)
    assert shifts.windows_on(hub, date(2026, 10, 17)) == []  # Saturday
    assert len(shifts.windows_on(hub, date(2026, 10, 14))) == 1
