"""Per-hub data freshness: the one answer used by /healthz, the header, the stale banner (D9),
the live My Shift notice (eng re-review D5) and the print header (eng re-review D6).

Stale rule (eng review D5, threshold = SiteSettings.stale_after_minutes, default 30):
- Outside every scheduled shift (core.shifts.window_at is None) a hub is never stale.
- During a scheduled shift, quiet time is counted from
  max(last_new_transaction_at, the current shift window's start). The hub is stale once quiet
  time reaches the threshold (>=, so 29:59 is fresh and 30:00 is stale). Starting a shift with
  no jobs yet is therefore not instantly stale (pairs with the D10 start-of-shift state).
- A hub that has never ingested successfully is stale during any scheduled shift.

Times are aware UTC throughout; `display_time()` formats them in the display timezone
(settings.TIME_ZONE, from TAKT_DISPLAY_TIMEZONE).
"""

from dataclasses import dataclass
from datetime import timedelta

from django.db import DatabaseError, connection
from django.utils import timezone

from core import shifts
from core.models import Hub, SiteSettings


def current_time():
    """The instant freshness is judged at. One seam so tests can pin the clock."""
    return timezone.now()


def database_reachable():
    """True when a trivial query succeeds. Used by /healthz and the D13 middleware."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except DatabaseError:
        return False
    return True


def display_time(instant, now=None):
    """'6:42 AM' in the display timezone; 'Oct 11, 6:42 AM' when not on the same local day as `now`."""
    if instant is None:
        return ""
    local = timezone.localtime(instant)
    text = f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
    ref = timezone.localtime(now or current_time())
    if local.date() != ref.date():
        text = f"{local.strftime('%b')} {local.day}, {text}"
    return text


def short_name(hub):
    """'Dies Hub' -> 'Dies', for 'numbers for Dies lines'."""
    name = hub.name
    return name[: -len(" Hub")] if name.endswith(" Hub") else name


@dataclass(frozen=True)
class HubFreshness:
    hub: Hub
    now: object  # aware datetime the answer was computed at
    threshold_minutes: int
    window: object  # core.shifts.ShiftWindow or None
    stale: bool
    stale_since: object  # aware datetime the hub crossed the threshold, or None
    quiet_from: object  # aware datetime quiet time is counted from, or None outside a shift

    @property
    def code(self):
        return self.hub.code

    @property
    def name(self):
        return self.hub.name

    @property
    def last_new_transaction_at(self):
        return self.hub.last_new_transaction_at

    @property
    def last_successful_ingest_at(self):
        return self.hub.last_successful_ingest_at

    @property
    def in_scheduled_shift(self):
        return self.window is not None

    @property
    def minutes_quiet(self):
        """Minutes of the current shift with no new transaction (None outside a shift)."""
        if self.quiet_from is None:
            return None
        return round((self.now - self.quiet_from).total_seconds() / 60, 2)

    @property
    def data_as_of(self):
        """The data time shown in the header and print header: latest successful ingest (re-review D6)."""
        return self.hub.last_successful_ingest_at

    @property
    def stopped_at(self):
        """The 'last good time' for stale text: last new transaction, else last successful ingest."""
        return self.hub.last_new_transaction_at or self.hub.last_successful_ingest_at

    # Display strings (display timezone).
    @property
    def data_as_of_display(self):
        return display_time(self.data_as_of, self.now)

    @property
    def stopped_at_display(self):
        return display_time(self.stopped_at, self.now)

    @property
    def short_name(self):
        return short_name(self.hub)

    def as_json(self):
        def iso(v):
            return v.isoformat() if v else None

        return {
            "code": self.code,
            "name": self.name,
            "last_new_transaction_at": iso(self.last_new_transaction_at),
            "last_successful_ingest_at": iso(self.last_successful_ingest_at),
            "in_scheduled_shift": self.in_scheduled_shift,
            "shift": self.window.shift.name if self.window else None,
            "stale": self.stale,
            "stale_since": iso(self.stale_since),
            "minutes_quiet": self.minutes_quiet,
        }


def hub_freshness(hub, now=None, threshold_minutes=None):
    """Freshness of one hub at `now` (aware; default: current_time())."""
    now = now or current_time()
    if threshold_minutes is None:
        threshold_minutes = SiteSettings.load().stale_after_minutes
    threshold = timedelta(minutes=threshold_minutes)
    window = shifts.window_at(hub, now)
    if window is None:
        return HubFreshness(hub, now, threshold_minutes, None, False, None, None)
    if hub.last_successful_ingest_at is None:
        # Never ingested during a scheduled shift: stale from the moment the shift is running.
        return HubFreshness(hub, now, threshold_minutes, window, True, window.start, window.start)
    last_tx = hub.last_new_transaction_at
    quiet_from = max(last_tx, window.start) if last_tx else window.start
    stale = now - quiet_from >= threshold
    return HubFreshness(hub, now, threshold_minutes, window, stale, quiet_from + threshold if stale else None,
                        quiet_from)


@dataclass(frozen=True)
class Snapshot:
    """Freshness of every hub at one instant."""

    now: object
    hubs: tuple  # (HubFreshness, ...) ordered by hub name

    @property
    def by_code(self):
        return {h.code: h for h in self.hubs}

    @property
    def stale_hubs(self):
        return [h for h in self.hubs if h.stale]

    @property
    def any_stale(self):
        return any(h.stale for h in self.hubs)

    def get(self, hub_or_code):
        code = getattr(hub_or_code, "code", hub_or_code)
        return self.by_code.get(code)


def snapshot(now=None):
    """Freshness for every hub. With zero hubs configured, nothing can be stale."""
    now = now or current_time()
    threshold = SiteSettings.load().stale_after_minutes
    hubs = Hub.objects.order_by("name")
    return Snapshot(now, tuple(hub_freshness(h, now, threshold) for h in hubs))


def is_stale(hub, now=None):
    """Convenience: is this hub (instance or code) stale right now?"""
    if not isinstance(hub, Hub):
        hub = Hub.objects.get(code=hub)
    return hub_freshness(hub, now).stale


@dataclass(frozen=True)
class PrintHeader:
    """Print sheet header lines (design D20, eng re-review D6)."""

    data_as_of: str  # "Data as of 2:38 PM"
    printed: str  # "Printed 2:41 PM"
    stale: bool
    stale_text: str  # "STALE: data stopped at 6:40 AM", or "" when fresh

    @property
    def lines(self):
        return [t for t in (self.stale_text, self.data_as_of, self.printed) if t]


def print_header(hub, now=None):
    """Header strings for the printed shift sheet for `hub` (instance or code) at `now`."""
    if not isinstance(hub, Hub):
        hub = Hub.objects.get(code=hub)
    f = hub_freshness(hub, now)
    data = f"Data as of {f.data_as_of_display}" if f.data_as_of else "Data as of: no data received yet"
    stale_text = ""
    if f.stale:
        stale_text = (f"STALE: data stopped at {f.stopped_at_display}" if f.stopped_at
                      else "STALE: no data received yet")
    return PrintHeader(data, f"Printed {display_time(f.now, f.now)}", f.stale, stale_text)
