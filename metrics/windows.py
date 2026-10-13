"""Time windows and planned available time for the metric layer.

Takt time = planned available time / customer demand (premise 5). TaktConfig holds demand
per part family per production day at a line, so for family f on day d:

    takt_seconds(f, d)   = day_planned_seconds(d) / demand(f, d)
    units_required(f, W) = demand(f, d) * planned_elapsed(W, d) / day_planned_seconds(d)

where planned time is net of breaks and planned maintenance (core.shifts.ShiftWindow.planned_seconds)
and day_planned_seconds is the sum over every scheduled shift that STARTS on d (a 23:00-07:00
shift belongs to the day it starts on; a DST night is an hour longer or shorter in real time).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from django.utils import timezone as djtz

from core.models import Shift
from core.shifts import ShiftStamper


@dataclass(frozen=True)
class Window:
    """What a screen is looking at: production days date_from..date_to (inclusive), optionally
    one shift (matched by name in each hub, e.g. "2nd"), as of an instant (default: now).

    `as_of` only limits planned elapsed time (a live shift counts the time so far); counts are
    whatever has been ingested.
    """

    date_from: date
    date_to: date
    shift_name: str | None = None
    as_of: datetime | None = None

    @classmethod
    def day(cls, d, as_of=None):
        return cls(d, d, None, as_of)

    @classmethod
    def days(cls, date_from, date_to, as_of=None):
        return cls(date_from, date_to, None, as_of)

    @classmethod
    def for_shift(cls, shift, production_date, as_of=None):
        """`shift` is a core.Shift or a shift name."""
        name = shift if isinstance(shift, str) else shift.name
        return cls(production_date, production_date, name, as_of)

    def __post_init__(self):
        if self.date_to < self.date_from:
            raise ValueError("Window date_to is before date_from")

    def now(self):
        return self.as_of or djtz.now()

    def dates(self):
        d = self.date_from
        while d <= self.date_to:
            yield d
            d += timedelta(days=1)

    def matches(self, shift):
        return self.shift_name is None or shift.name == self.shift_name

    @property
    def label(self):
        """Header text naming the window (design D21: the header always names the window)."""
        if self.date_from == self.date_to:
            day = f"{self.date_from:%a %b} {self.date_from.day}"
        else:
            day = f"{self.date_from:%b} {self.date_from.day} – {self.date_to:%b} {self.date_to.day}"
        return f"{self.shift_name} shift, {day}" if self.shift_name else day


class HubPlan:
    """Planned available time for one hub, cached per production date."""

    def __init__(self, hub):
        self.hub = hub
        self.stamper = ShiftStamper(hub)
        self._day = {}

    def windows_on(self, d):
        return self.stamper.windows_on(d)

    def day_planned_seconds(self, d):
        if d not in self._day:
            self._day[d] = sum(w.planned_seconds() for w in self.windows_on(d))
        return self._day[d]

    def shift_windows(self, window, d):
        return [w for w in self.windows_on(d) if window.matches(w.shift)]

    def elapsed_seconds(self, window, d):
        now = window.now()
        return sum(w.planned_seconds(until=now) for w in self.shift_windows(window, d))

    def fraction(self, window, d):
        """Share of day d's planned available time that has elapsed inside the window."""
        total = self.day_planned_seconds(d)
        return self.elapsed_seconds(window, d) / total if total else 0.0

    def shift_ids(self, window):
        """Shift ids of this hub matching the window's shift name, or None for all shifts."""
        if window.shift_name is None:
            return None
        return list(Shift.objects.filter(hub=self.hub, name=window.shift_name).values_list("id", flat=True))

    def scheduled(self, window):
        return any(self.shift_windows(window, d) for d in window.dates())

    def started(self, window):
        return any(self.elapsed_seconds(window, d) > 0 for d in window.dates())

    def takt_seconds(self, d, demand_units):
        """Takt time in seconds per unit, or None when demand is 0 or missing (eng review D7)."""
        if not demand_units:
            return None
        total = self.day_planned_seconds(d)
        return total / demand_units if total else None


__all__ = ["Window", "HubPlan"]
