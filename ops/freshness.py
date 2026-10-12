"""Per-hub data freshness: the one answer used by /healthz, the header, the stale banner (D9),
the live My Shift notice (eng re-review D5) and the print header (eng re-review D6).

Stale rule (eng review D5, threshold = SiteSettings.stale_after_minutes, default 30):
- Outside every scheduled shift (core.shifts.window_at is None) a hub is never stale.
- During a scheduled shift, quiet time is counted from
  max(last_new_transaction_at, block start), where the block is the run of back-to-back
  scheduled windows containing now (walk back while prev.end >= cur.start; a gap or a
  non-working day ends the block). So a shift change does not reset the clock during an outage
  (1st 7-15 then 2nd 15-23: last job 14:20 is stale at 15:05), but the first shift after a gap
  starting with no jobs yet is not instantly stale (pairs with the D10 start-of-shift state).
  The hub is stale once quiet time reaches the threshold (>=, so 29:59 is fresh, 30:00 stale).
- A hub that has never seen a transaction follows the same rule: stale at block start + threshold.

Times are aware UTC throughout. Display strings for a hub use the hub's plant timezone;
`display_time()` without a tz uses the display timezone (settings.TIME_ZONE).
"""

from dataclasses import dataclass
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import DatabaseError, connection
from django.utils import timezone

from core import shifts
from core.models import Hub, NonWorkingDay, Shift, SiteSettings

# A contiguous block (e.g. a 24/7 three-shift plant) is walked back at most this far.
MAX_BLOCK_DAYS = 7


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


def display_time(instant, now=None, tz=None):
    """'6:42 AM' in `tz` (an IANA name or tzinfo; default: the display timezone);
    'Oct 11, 6:42 AM' when not on the same local day as `now`."""
    if instant is None:
        return ""
    if isinstance(tz, str):
        tz = ZoneInfo(tz)
    local = timezone.localtime(instant, tz)
    text = f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"
    ref = timezone.localtime(now or current_time(), tz)
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
        """The 'last good time' for stale text: the last new transaction (None if there never was one)."""
        return self.hub.last_new_transaction_at

    # Display strings, in the hub's plant timezone.
    def display(self, instant):
        return display_time(instant, self.now, self.hub.plant_timezone)

    @property
    def data_as_of_display(self):
        return self.display(self.data_as_of)

    @property
    def stopped_at_display(self):
        return self.display(self.stopped_at)

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


def block_start(hub, window):
    """Start of the contiguous run of scheduled windows that contains `window`.

    Walks back while an earlier scheduled window ends at or after the block's start; a gap or a
    non-working day ends the block. Bounded by MAX_BLOCK_DAYS.
    """
    non_working = set(NonWorkingDay.objects.filter(hub=hub).values_list("date", flat=True))
    hub_shifts = list(Shift.objects.filter(hub=hub, active=True).prefetch_related("breaks").select_related("hub"))
    earlier = [
        shifts.window_for(s, d)
        for back in range(0, MAX_BLOCK_DAYS + 1)
        for d in [window.production_date - timedelta(days=back)]
        for s in hub_shifts
        if shifts.is_scheduled(s, d, non_working)
    ]
    start = window.start
    changed = True
    while changed:
        changed = False
        for w in earlier:
            if w.start < start <= w.end:
                start, changed = w.start, True
    return start


def hub_freshness(hub, now=None, threshold_minutes=None):
    """Freshness of one hub at `now` (aware; default: current_time())."""
    now = now or current_time()
    if threshold_minutes is None:
        threshold_minutes = SiteSettings.load().stale_after_minutes
    threshold = timedelta(minutes=threshold_minutes)
    window = shifts.window_at(hub, now)
    if window is None:
        return HubFreshness(hub, now, threshold_minutes, None, False, None, None)
    start = block_start(hub, window)
    last_tx = hub.last_new_transaction_at
    quiet_from = max(last_tx, start) if last_tx else start
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


def snapshot(now=None, threshold_minutes=None):
    """Freshness for every hub. With zero hubs configured, nothing can be stale."""
    now = now or current_time()
    threshold = SiteSettings.load().stale_after_minutes if threshold_minutes is None else threshold_minutes
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
    data = f"Data as of {f.data_as_of_display}" if f.data_as_of else "Data as of —, no data received yet"
    stale_text = ""
    if f.stale:
        stale_text = (f"STALE: data stopped at {f.stopped_at_display}" if f.stopped_at
                      else "STALE: no data received yet")
    return PrintHeader(data, f"Printed {f.display(f.now)}", f.stale, stale_text)
