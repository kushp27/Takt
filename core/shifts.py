"""ShiftCalendar arithmetic, shared by ingest stamping, metrics and /healthz freshness.

Rules (eng review D2):
- Shift times are local plant time in the hub's timezone; everything returned is aware UTC.
- A shift belongs to the production date it STARTS on, so 23:00-07:00 is one production day.
- DST is handled by zoneinfo: a window's length is real elapsed time (a fall-back night shift
  is an hour longer, a spring-forward one an hour shorter).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import NonWorkingDay, Shift


@dataclass(frozen=True)
class ShiftWindow:
    shift: Shift
    production_date: date
    start: datetime  # aware UTC
    end: datetime  # aware UTC, exclusive
    breaks: tuple  # ((start_utc, end_utc), ...) breaks and planned maintenance

    def contains(self, instant):
        return self.start <= instant < self.end

    def planned_seconds(self, until=None):
        """Planned available time in seconds from shift start to `until` (default: shift end),
        minus breaks and planned maintenance (premise 5)."""
        stop = self.end if until is None else max(self.start, min(until, self.end))
        total = (stop - self.start).total_seconds()
        for b_start, b_end in self.breaks:
            overlap = (min(b_end, stop) - max(b_start, self.start)).total_seconds()
            total -= max(0.0, overlap)
        return max(0.0, total)


def _local(tz, d, t):
    return datetime.combine(d, t, tzinfo=tz).astimezone(timezone.utc)


def _span(tz, d, start_t, end_t):
    """UTC start and end for a local [start_t, end_t) starting on date d; end before start = next day."""
    end_day = d + timedelta(days=1) if end_t <= start_t else d
    return _local(tz, d, start_t), _local(tz, end_day, end_t)


def window_for(shift, production_date):
    """The ShiftWindow for `shift` starting on `production_date` (whether or not it is scheduled)."""
    tz = ZoneInfo(shift.hub.plant_timezone)
    start, end = _span(tz, production_date, shift.start_time, shift.end_time)
    breaks = []
    for b in shift.breaks.all():
        # A break belongs to the day after start if its local start is before the shift's start time.
        b_day = production_date + timedelta(days=1) if b.start_time < shift.start_time else production_date
        breaks.append(_span(tz, b_day, b.start_time, b.end_time))
    return ShiftWindow(shift, production_date, start, end, tuple(breaks))


def is_scheduled(shift, production_date, non_working=None):
    if not shift.active or not shift.runs_on(production_date.weekday()):
        return False
    if non_working is None:
        non_working = set(NonWorkingDay.objects.filter(hub=shift.hub).values_list("date", flat=True))
    return production_date not in non_working


def windows_on(hub, production_date):
    """Scheduled shift windows of `hub` that start on `production_date`, in start order."""
    non_working = set(NonWorkingDay.objects.filter(hub=hub).values_list("date", flat=True))
    shifts = Shift.objects.filter(hub=hub, active=True).prefetch_related("breaks").select_related("hub")
    return sorted(
        (window_for(s, production_date) for s in shifts if is_scheduled(s, production_date, non_working)),
        key=lambda w: w.start,
    )


def local_date(hub, instant):
    return instant.astimezone(ZoneInfo(hub.plant_timezone)).date()


def window_at(hub, instant):
    """The scheduled ShiftWindow containing `instant` (aware), or None outside every scheduled shift.

    Checks windows starting on the local date and the day before (night shifts crossing midnight).
    """
    d = local_date(hub, instant)
    for day in (d - timedelta(days=1), d):
        for w in windows_on(hub, day):
            if w.contains(instant):
                return w
    return None


def stamp(hub, instant):
    """(shift or None, production_date) for an event at `instant` (eng review D2).

    Inside a scheduled shift: that shift and its start date. Outside: no shift, local calendar date.
    """
    w = window_at(hub, instant)
    if w is None:
        return None, local_date(hub, instant)
    return w.shift, w.production_date


class ShiftStamper:
    """Cached, bulk-friendly equivalent of `stamp()` / `window_at()` for one hub.

    Ingest and restamp_shifts stamp thousands of rows; `stamp()` re-queries the shift
    calendar per call. This loads the hub's active shifts, breaks and non-working days
    once and caches windows per production date. Same rules, same answers as `stamp()`.
    """

    def __init__(self, hub):
        self.hub = hub
        self.tz = ZoneInfo(hub.plant_timezone)
        self.shifts = list(
            Shift.objects.filter(hub=hub, active=True).prefetch_related("breaks").select_related("hub")
        )
        self.non_working = set(NonWorkingDay.objects.filter(hub=hub).values_list("date", flat=True))
        self._by_date = {}
        self._by_key = {}

    def windows_on(self, production_date):
        if production_date not in self._by_date:
            self._by_date[production_date] = sorted(
                (window_for(s, production_date) for s in self.shifts
                 if is_scheduled(s, production_date, self.non_working)),
                key=lambda w: w.start,
            )
        return self._by_date[production_date]

    def window(self, shift_id, production_date):
        """The window of a stamped (shift_id, production_date), scheduled or not; None if shift unknown."""
        key = (shift_id, production_date)
        if key not in self._by_key:
            shift = next((s for s in self.shifts if s.pk == shift_id), None)
            self._by_key[key] = window_for(shift, production_date) if shift else None
        return self._by_key[key]

    def window_at(self, instant):
        d = instant.astimezone(self.tz).date()
        for day in (d - timedelta(days=1), d):
            for w in self.windows_on(day):
                if w.contains(instant):
                    return w
        return None

    def stamp(self, instant):
        w = self.window_at(instant)
        if w is None:
            return None, instant.astimezone(self.tz).date()
        return w.shift, w.production_date


def last_completed_window(hub, now):
    """The most recent scheduled window that has ended by `now`, looking back up to 14 days."""
    d = local_date(hub, now)
    for back in range(0, 15):
        for w in reversed(windows_on(hub, d - timedelta(days=back))):
            if w.end <= now:
                return w
    return None


__all__ = [
    "ShiftWindow", "window_for", "windows_on", "window_at", "stamp", "is_scheduled",
    "local_date", "last_completed_window", "ShiftStamper",
]
