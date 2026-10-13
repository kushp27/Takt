"""Per-role default time windows and the shared shift/date picker (design review D21, D-T10).

- Line Health opens on the **last completed production day** (all shifts).
- Area opens on the **last completed shift**, with the current shift one click away.
- My Shift opens on the lead's **current shift, live**.
- Every screen has the same picker (?date=YYYY-MM-DD, ?shift_id=<pk>, Line Health also ?days=N)
  and the header always names the window (ScreenWindow.label).

"Now" is ops.freshness.current_time(), the one clock seam tests pin. Metric calls still pass
as_of=None (live screens; a past as_of would cut counts).

Leads (role scope: their area and shift) always look at their own shift: on Line Health and Area
the window's shift is forced to theirs, and naming another shift is refused (D13).
"""
# DEVIATION: the doc gives Line Health to Brian and Area to supervisors and does not say what a lead sees
# there. The roles table scopes a lead to "their area and shift", so the most conservative reading is used:
# a lead's Line Health and Area windows are always their own shift (the 3-shift table shows only theirs).

from dataclasses import dataclass
from datetime import date, timedelta
from urllib.parse import urlencode

from accounts import scope as role_scope
from accounts.models import Profile
from core import shifts as cs
from core.models import Shift
from metrics.windows import Window
from ops import freshness

MAX_DAYS = 31
LOOKBACK_DAYS = 14


def now():
    return freshness.current_time()


class OutOfScopeShift(Exception):
    """?shift_id names a shift the user may not view (leads: anything but their own)."""


@dataclass(frozen=True)
class ScreenWindow:
    """What a screen shows: production days date_from..date_to, one shift or all shifts."""

    date_from: date
    date_to: date
    shift: Shift | None = None
    live: bool = False  # the window contains now (My Shift polls for new data)
    is_default: bool = True  # no ?date= given: the role's default (D21)

    @property
    def days(self):
        return (self.date_to - self.date_from).days + 1

    @property
    def metrics(self):
        """The metrics.windows.Window for the API (as_of=None: live)."""
        return Window(self.date_from, self.date_to, self.shift.name if self.shift else None, None)

    @property
    def label(self):
        """Header text, e.g. "1st shift, Tue Oct 6" or "Tue Oct 6, all shifts" (D21)."""
        text = self.metrics.label
        if self.shift is None:
            text = f"{text}, all shifts"
        return f"{text} (live)" if self.live else text

    def params(self, **extra):
        """Query parameters that reproduce this window (for drill-downs, refresh, print)."""
        p = {"date": self.date_to.isoformat()}
        if self.shift is not None:
            p["shift_id"] = self.shift.pk
        if self.days > 1:
            p["days"] = self.days
        p.update({k: v for k, v in extra.items() if v is not None})
        return p

    def query(self, **extra):
        return urlencode(self.params(**extra))


# --------------------------------------------------------------------------- defaults


def last_completed_day(hubs, at=None):
    """Line Health default: the latest production date on which every scheduled shift of every hub
    has ended by `at` (and at least one was scheduled). Falls back to yesterday."""
    at = at or now()
    hubs = list(hubs)
    if not hubs:
        return (at.date() - timedelta(days=1))
    today = max(cs.local_date(h, at) for h in hubs)
    for back in range(0, LOOKBACK_DAYS + 1):
        d = today - timedelta(days=back)
        wins = [w for h in hubs for w in cs.windows_on(h, d)]
        if wins and all(w.end <= at for w in wins):
            return d
    return today - timedelta(days=1)


def last_completed_shift(hub, at=None, shift=None):
    """Area default: (production date, Shift) of the most recent scheduled window that ended by
    `at`; only `shift`'s windows when given (a lead's own shift). None when nothing ended in 14 days."""
    at = at or now()
    d = cs.local_date(hub, at)
    for back in range(0, LOOKBACK_DAYS + 1):
        for w in reversed(cs.windows_on(hub, d - timedelta(days=back))):
            if w.end <= at and (shift is None or w.shift.pk == shift.pk):
                return w.production_date, w.shift
    return None


def current_window(hub, at=None):
    """The scheduled shift window running now for the hub, or None (D21 "current shift")."""
    return cs.window_at(hub, at or now())


@dataclass(frozen=True)
class ShiftNow:
    """Where a shift stands right now, for My Shift's default (D21) and the D10 states."""

    production_date: date  # the window My Shift opens on
    live: bool  # that window contains now
    started_at: object  # aware start of that window, or None
    scheduled_today: bool  # the shift runs today (local date) or is running now
    last_date: date | None  # the previous run of this shift that has ended (D10 links)


def shift_now(shift, at=None):
    """My Shift default for `shift`: the run containing now (a 23:00-07:00 shift opened at 02:00
    is the run that started yesterday: one production day, eng D2); else today's run if it has
    started; else the latest run that has started (looking back 14 days)."""
    at = at or now()
    hub = shift.hub
    today = cs.local_date(hub, at)
    stamper = cs.ShiftStamper(hub)

    def run(d):
        return next((w for w in stamper.windows_on(d) if w.shift.pk == shift.pk), None)

    current = None
    for d in (today - timedelta(days=1), today):
        w = run(d)
        if w is not None and w.contains(at):
            current = w
    started = current
    if started is None:
        for back in range(0, LOOKBACK_DAYS + 1):
            w = run(today - timedelta(days=back))
            if w is not None and w.start <= at:
                started = w
                break
    last = None
    for back in range(0, LOOKBACK_DAYS + 1):
        w = run(today - timedelta(days=back))
        if w is not None and w.end <= at:
            last = w.production_date
            break
    scheduled_today = current is not None or run(today) is not None
    if current is not None:
        return ShiftNow(current.production_date, True, current.start, True, last)
    if started is not None and started.production_date == today:
        return ShiftNow(today, False, started.start, True, last)
    if scheduled_today:
        # Today's run hasn't started: show the last run that did (yesterday's sheet), not an empty page.
        if started is not None:
            return ShiftNow(started.production_date, False, started.start, True, last)
        return ShiftNow(today, False, None, True, last)
    return ShiftNow(today, False, None, False, last)


# --------------------------------------------------------------------------- request parsing


def _date(request):
    raw = request.GET.get("date") or request.POST.get("date") or ""
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _days(request):
    try:
        n = int(request.GET.get("days", "1"))
    except ValueError:
        return 1
    return max(1, min(n, MAX_DAYS))


def requested_shift(request, area=None):
    """The ?shift_id the user may view, None when absent. Raises OutOfScopeShift otherwise."""
    raw = request.GET.get("shift_id") or request.POST.get("shift_id") or ""
    if not raw:
        return None
    if not str(raw).isdigit():
        raise OutOfScopeShift(raw)
    shift = Shift.objects.select_related("hub").filter(pk=int(raw)).first()
    if shift is None:
        raise OutOfScopeShift(raw)
    allowed = role_scope.visible_shifts(request.user, area)
    if not allowed.filter(pk=shift.pk).exists():
        raise OutOfScopeShift(raw)
    return shift


def lead_shift(user):
    """A lead's own shift (their scope), else None."""
    if role_scope.role_of(user) == Profile.LEAD:
        return user.profile.shift
    return None


def line_health_window(request, hubs):
    """D21: last completed production day, all shifts. A lead sees only their own shift."""
    shift = requested_shift(request)
    own = lead_shift(request.user)
    if own is not None:
        shift = own
    d = _date(request)
    days = _days(request)
    if d is None:
        return ScreenWindow(*_span(last_completed_day(hubs), days), shift, False, True)
    return ScreenWindow(*_span(d, days), shift, False, False)


def area_window(request, area, hub):
    """D21: the last completed shift. A lead sees only their own shift."""
    shift = requested_shift(request, area)
    own = lead_shift(request.user)
    if own is not None:
        shift = own
    d = _date(request)
    if d is not None:
        return ScreenWindow(d, d, shift, _is_live(hub, d, shift), False)
    if hub is None:
        today = now().date()
        return ScreenWindow(today, today, shift, False, True)
    found = last_completed_shift(hub, shift=shift)
    if found is None:
        today = cs.local_date(hub, now())
        return ScreenWindow(today, today, shift, False, True)
    d, s = found
    return ScreenWindow(d, d, shift or s, False, True)


def my_shift_window(request, shift):
    """D21: the lead's current shift, live. Returns (ScreenWindow, ShiftNow)."""
    state = shift_now(shift)
    d = _date(request)
    if d is None:
        return ScreenWindow(state.production_date, state.production_date, shift, state.live, True), state
    return ScreenWindow(d, d, shift, _is_live(shift.hub, d, shift), False), state


def _span(d, days):
    return d - timedelta(days=days - 1), d


def _is_live(hub, d, shift):
    if hub is None:
        return False
    w = cs.window_at(hub, now())
    return bool(w and w.production_date == d and (shift is None or w.shift.pk == shift.pk))


# --------------------------------------------------------------------------- picker


def picker(request, window, action, *, area=None, shifts=None, allow_all_shifts=True, current=None,
           show_days=False, extra=None):
    """Context for web/partials/window_picker.html, the one picker every screen shares (D21).

    shifts: the Shift choices (default: the user's visible shifts for the area).
    current: (date, Shift) of the shift running now, rendered as a one-click "Current shift" link.
    extra: hidden fields the form must carry (e.g. part_id)."""
    if shifts is None:
        shifts = role_scope.visible_shifts(request.user, area).select_related("hub").order_by("hub__name",
                                                                                             "start_time")
    shifts = list(shifts)
    current_url = None
    if current is not None:
        cd, cshift = current
        q = {"date": cd.isoformat(), "shift_id": cshift.pk, **(extra or {})}
        current_url = f"{action}?{urlencode(q)}"
    many_hubs = len({s.hub_id for s in shifts}) > 1
    return {
        "action": action,
        "date": window.date_to.isoformat(),
        "shift_id": window.shift.pk if window.shift else None,
        "shift_choices": [(s.pk, f"{s.name} ({s.hub.name})" if many_hubs else s.name) for s in shifts],
        "allow_all_shifts": allow_all_shifts,
        "days": window.days if show_days else None,
        "label": window.label,
        "current_url": current_url,
        "extra": sorted((extra or {}).items()),
    }
