"""The metric API the screens call (Line Health, Area, My Shift, print sheet). Lane D renders it.

Everything here returns plain dataclasses. Operator identity is NEVER returned as a name:
rows carry OperatorAlias / Operator objects (alias.operator is select_related) and Lane B's
`operator_display(user, operator, context)` decides named / letter / hidden. An unmapped alias
has operator=None; show it as "Unmapped operator (<hub_operator_id>)" (Component 2).

Definitions (design Component 3, premise 5):
- takt time = planned available time / demand (seconds per unit), per part family per day at the
  line; planned time is net of breaks and planned maintenance (core.shifts.ShiftWindow.planned_seconds).
- pace = good units / units required so far, where units required = demand x planned elapsed /
  planned day. Line pace counts good units at the final station only; station pace uses only the
  families routed through the station, times their visit count.
- zero or missing demand adds 0 units required and is listed "no demand set"; when units required
  is 0, pace is "—" with the reason, never 0% or ∞ (eng review D7).
- station yield = good / (good + scrap found there); line yield (RTY) = product over stations of
  each station's yield pooled over parts, Σgood / Σ(good + scrap). Per-part yield is in station rows.
- bottleneck = the lowest station pace among count-trusted stations, with coverage.
- operator rate (green only) = units per hour on a station-part / the median of mapped operators'
  shift rates on that station-part over the trailing 4 weeks.
- transaction gap = shift time minus breaks minus time covered by the operator's jobs ("time with no
  transaction", never "idle"); upstream output gap = working-time windows of 15+ minutes in which the
  upstream station completed nothing.
What a cell may show is decided by trust/matrix.py (Component 4a), never here.
"""

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from django.utils import timezone as djtz

from core.models import (Job, Line, OperatorAlias, Part, PartFamily, Routing, ScrapCode, SiteSettings, Station,
                         StationPartGrade, TaktConfig)
from core.shifts import local_date
from trust.grades import majority_grade
from trust.matrix import Cell, cell as matrix_cell

from . import queries
from . import sentences as S
from .windows import HubPlan, Window

DASH = "—"
BASELINE_DAYS = 14  # pace and yield are classified against the 14 days before the window
TREND_DAYS = 14  # the sparkline: 14 days ending on the window's last day
RATE_MEDIAN_DAYS = 28  # operator rate median: trailing 4 weeks
WORTH_A_CONVERSATION_LIMIT = 3  # design review D5: up to 3 names per green station-part group
# Judgment call (the doc does not say who makes the list): mapped operators at least 10% under the
# station-part median, biggest shortfall first.
WORTH_A_CONVERSATION_MIN_SHORTFALL = 0.10
TXN_GAP_REASON_MINUTES = 30  # a transaction gap this long is named as a context reason

REASON_TEXT = {
    "no_demand": "no demand set",
    "no_shift": "no shift scheduled",
    "not_started": "shift not started",
    "counts_unverified": "Counts unverified",
    "final_station_unverified": "unavailable: final-station counts unverified",
}


def _minus(n):
    return f"−{abs(n)}" if n < 0 else f"+{n}"


# ---------------------------------------------------------------- value types


@dataclass(frozen=True)
class Pace:
    good: int  # good units counted
    required: float  # units required so far
    value: float | None  # good / required, or None (see reason)
    reason: str | None = None  # no_demand | no_shift | not_started | counts_unverified | final_station_unverified

    @property
    def reason_text(self):
        return REASON_TEXT.get(self.reason)

    @property
    def percent(self):
        """Whole percent, rounded down so that 99.6% reads 99% and matches "Behind"."""
        return None if self.value is None else math.floor(self.value * 100 + 1e-9)

    @property
    def display(self):
        return DASH if self.value is None else f"{self.percent}%"

    @property
    def delta_units(self):
        return None if self.value is None else round(self.good - self.required)

    @property
    def throughput_display(self):
        """Design review D4: one column, "104% · +40 units"; "— · no demand set" without a number."""
        if self.value is None:
            return f"{DASH} · {self.reason_text}"
        return f"{self.percent}% · {_minus(self.delta_units)} units"

    @property
    def tooltip(self):
        return f"{self.good} good of {round(self.required)} required"


@dataclass(frozen=True)
class Yield:
    good: int
    scrap: int

    @property
    def value(self):
        total = self.good + self.scrap
        return self.good / total if total else None

    @property
    def display(self):
        return DASH if self.value is None else f"{self.value * 100:.1f}%"


@dataclass(frozen=True)
class LineYield:
    """Rolled throughput yield: the product of station yields along the line."""

    value: float | None
    stations: tuple  # ((Station, Yield), ...) every station of the line, in sequence
    stations_included: int  # stations with output or scrap in the window (the others are skipped)
    stations_total: int

    @property
    def display(self):
        return DASH if self.value is None else f"{self.value * 100:.1f}%"


@dataclass(frozen=True)
class TopScrap:
    scrap_code: ScrapCode
    quantity: int
    cause_station: Station | None  # static admin hint, not attribution

    @property
    def label(self):
        return self.scrap_code.description or self.scrap_code.code


@dataclass(frozen=True)
class Bottleneck:
    station: Station
    pace: Pace
    stations_used: int  # count-trusted stations with a pace
    stations_total: int

    @property
    def coverage_text(self):
        return f"bottleneck chosen from {self.stations_used} of {self.stations_total} stations"

    @property
    def sentence(self):
        return (f"Bottleneck: {self.station.name} at {self.pace.display} "
                f"(chosen from {self.stations_used} of {self.stations_total} stations).")


@dataclass(frozen=True)
class DemandGap:
    line: Line
    family: PartFamily
    production_date: date
    reason: str  # "missing" (no TaktConfig row: red admin warning) | "zero" (demand 0)
    text: str = "no demand set"


@dataclass(frozen=True)
class TrendPoint:
    production_date: date
    pace: float | None
    yield_: float | None


@dataclass
class LineHealthRow:
    line: Line
    window: Window
    status: str | None  # On pace | Behind | Yield problem | No demand set | Pace unavailable | None
    status_key: str | None  # on_pace | behind | yield_problem | no_demand | pace_unavailable | no_shift | not_started
    sentence: str
    pace: Pace
    yield_: LineYield
    top_scrap: TopScrap | None
    bottleneck: Bottleneck | None
    bottleneck_coverage_text: str  # always present, e.g. "bottleneck chosen from 0 of 5 stations"
    pace_class: str | None  # low | normal | high (None without a pace)
    yield_class: str | None
    pace_baseline: dict | None  # {"mean", "sd", "days"} or None (under 2 days of history)
    yield_baseline: dict | None
    trend: list  # [TrendPoint] x 14, oldest first, the window's last day last ("today's point")
    trend_text: str  # accessible name for the sparkline (design review D22)
    no_demand: list  # [DemandGap] in the window
    final_station_trusted: bool


@dataclass(frozen=True)
class BatchStats:
    """Batch stations (Component 5): a batch is jobs sharing identical start and complete timestamps."""

    batches: int
    shifts: int
    units: int
    cycle_minutes: float | None  # mean load-to-unload

    @property
    def batches_per_shift(self):
        return self.batches / self.shifts if self.shifts else None

    @property
    def units_per_batch(self):
        return self.units / self.batches if self.batches else None


@dataclass
class PartRow:
    part: Part
    family: PartFamily
    jobs: int
    quantity: int
    good: int
    scrap: int
    yield_: Yield
    grade: str  # green | amber | red | not_graded
    days_of_history: int | None
    cell: Cell


@dataclass
class StationRow:
    station: Station
    count_trusted: bool
    grade: str | None  # majority-volume grade (None: nothing ran and nothing graded)
    cell: Cell  # station-level cell: badge, in_station_pace, message
    pace: Pace  # reason counts_unverified when the station is not count-trusted
    jobs: int
    quantity: int
    good: int
    scrap: int
    yield_: Yield
    parts: list  # [PartRow]
    top_scrap: TopScrap | None
    is_bottleneck: bool
    batch: BatchStats | None


@dataclass
class OperatorRow:
    alias: OperatorAlias
    operator: object  # core.Operator or None (unmapped): render through operator_display()
    unmapped: bool
    hub_operator_id: str  # for "Unmapped operator (hub ID)"
    jobs: int
    quantity: int
    excluded_jobs: int  # design review D23: "N jobs excluded" link
    rate_per_hour: float | None  # green and mapped only
    rate_vs_median: float | None  # e.g. -0.2 = 20% under the median; green and mapped only
    rate_text: str | None  # "n/a" on amber / not graded; None where there is no rate column
    transaction_gap_minutes: float | None  # green only (R3-5)
    upstream_gap_minutes: float | None  # every count-trusted row; None at a line's first station
    tenure_days: int | None
    tenure_under_90: bool | None
    can_dispute: bool

    @property
    def rate_vs_median_display(self):
        if self.rate_vs_median is None:
            return None
        return f"{_minus(round(self.rate_vs_median * 100))}% vs median"


@dataclass
class OperatorGroup:
    station: Station
    part: Part
    grade: str
    days_of_history: int | None
    cell: Cell
    message: str | None  # "Counts unverified, keep tally." where no rows may show
    rows: list  # [OperatorRow]; sort A-Z by the DISPLAYED label (design review D6), never by rate
    jobs: int | None  # station-part totals (None where counts are not shown)
    quantity: int | None
    takt_seconds: float | None  # the family's takt time on the window's last day
    takt_rate_per_hour: float | None  # 3600 / takt time, shown for reference beside rates
    median_rate_per_hour: float | None


@dataclass(frozen=True)
class Reason:
    kind: str  # rate | tenure | upstream_gap | transaction_gap
    text: str  # e.g. "−20% vs median", "tenure < 90 days"


@dataclass
class ConversationItem:
    row: OperatorRow
    station: Station
    part: Part
    reasons: list  # [Reason]; Lane D joins: "<name> · <station> · <reason> · <reason>"


@dataclass
class ConversationGroup:
    station: Station
    part: Part
    items: list  # [ConversationItem], at most 3


@dataclass
class JobItem:
    job: Job  # alias__operator, station, part, excluded_by are select_related
    is_open: bool
    counted: bool  # in counts (completed and not excluded from counts)
    in_rates: bool
    excluded: bool
    exclusion_reason: str
    excluded_by: object  # User or None
    excluded_at: object


@dataclass
class ShiftCell:
    shift_name: str
    window: Window
    pace: Pace
    yield_: LineYield


@dataclass
class ShiftComparisonRow:
    line: Line
    production_date: date
    shifts: list  # [ShiftCell] in shift start order


@dataclass(frozen=True)
class Scope:
    """Which lines / stations / person a list covers. Never plant-wide for operator lists."""

    lines: tuple = ()
    stations: tuple = ()
    part: Part | None = None
    operator: object = None
    alias: OperatorAlias | None = None

    @classmethod
    def for_area(cls, area, **kw):
        return cls(lines=tuple(Line.objects.filter(area=area).select_related("hub")), **kw)

    @classmethod
    def for_lines(cls, lines, **kw):
        return cls(lines=tuple(lines), **kw)

    def station_list(self):
        if self.stations:
            return list(self.stations)
        return list(Station.objects.filter(line__in=self.lines).select_related("line__hub").order_by("line", "sequence"))


# ---------------------------------------------------------------- per-line data


class _LineData:
    """One line's counts, scrap, demand and routing for a date range, aggregated in Python per window."""

    def __init__(self, line, date_from, date_to, plan=None):
        self.line = line
        self.plan = plan or HubPlan(line.hub)
        self.stations = list(line.stations.order_by("sequence"))
        self.final = self.stations[-1] if self.stations else None
        self.sp = defaultdict(list)
        for sid, pid, fid, d, shid, jobs, qty, scrap, good in queries.station_part_shift([line.pk], date_from, date_to):
            self.sp[d].append((sid, pid, fid, shid, jobs, qty, scrap, good))
        self.scrap = defaultdict(list)
        for sid, cid, pid, d, shid, qty in queries.scrap_codes([line.pk], date_from, date_to):
            self.scrap[d].append((sid, cid, shid, qty))
        self.demand = defaultdict(dict)
        for fid, d, units in TaktConfig.objects.filter(line=line, production_date__range=(date_from, date_to)) \
                .values_list("family_id", "production_date", "demand_units"):
            self.demand[d][fid] = units
        self.routing = defaultdict(dict)
        for sid, fid, visits in Routing.objects.filter(station__line=line).values_list("station_id", "family_id",
                                                                                         "visit_count"):
            self.routing[sid][fid] = visits
        self.routed_families = {fid for v in self.routing.values() for fid in v}
        self._shift_ids = {}

    def shift_ids(self, window):
        if window.shift_name not in self._shift_ids:
            ids = self.plan.shift_ids(window)
            self._shift_ids[window.shift_name] = None if ids is None else set(ids)
        return self._shift_ids[window.shift_name]

    def agg(self, window, key):
        """{key(station_id, part_id, family_id): [jobs, quantity, scrap, good]} over the window."""
        ids = self.shift_ids(window)
        out = defaultdict(lambda: [0, 0, 0, 0])
        for d in window.dates():
            for sid, pid, fid, shid, jobs, qty, scrap, good in self.sp.get(d, ()):
                if ids is not None and shid not in ids:
                    continue
                a = out[key(sid, pid, fid)]
                a[0] += jobs
                a[1] += qty
                a[2] += scrap
                a[3] += good
        return out

    def by_station(self, window):
        return self.agg(window, lambda sid, pid, fid: sid)

    def required(self, window, station=None):
        """Units required so far: the line's (station=None) or a station's (routed families x visits)."""
        visits = None if station is None else self.routing.get(station.pk, {})
        total = 0.0
        for d in window.dates():
            day = self.demand.get(d, {})
            if not day:
                continue
            frac = self.plan.fraction(window, d)
            if not frac:
                continue
            if visits is None:
                total += frac * sum(day.values())
            else:
                total += frac * sum(units * visits[fid] for fid, units in day.items() if fid in visits)
        return total

    def _reason(self, window, required):
        if not self.plan.scheduled(window):
            return "no_shift"
        if not self.plan.started(window):
            return "not_started"
        if required <= 0:
            return "no_demand"
        return None

    def line_pace(self, window, totals=None):
        totals = totals if totals is not None else self.by_station(window)
        good = totals.get(self.final.pk, [0, 0, 0, 0])[3] if self.final else 0
        required = self.required(window)
        reason = self._reason(window, required)
        if reason is None and (self.final is None or not self.final.count_trusted):
            reason = "final_station_unverified"
        return Pace(good, required, None if reason else good / required, reason)

    def station_pace(self, window, station, totals=None):
        totals = totals if totals is not None else self.by_station(window)
        good = totals.get(station.pk, [0, 0, 0, 0])[3]
        required = self.required(window, station)
        reason = self._reason(window, required)
        if reason is None and not station.count_trusted:
            reason = "counts_unverified"
        return Pace(good, required, None if reason else good / required, reason)

    def line_yield(self, window, totals=None):
        totals = totals if totals is not None else self.by_station(window)
        per, value, included = [], None, 0
        for st in self.stations:
            jobs, qty, scrap, good = totals.get(st.pk, [0, 0, 0, 0])
            y = Yield(good, scrap)
            per.append((st, y))
            if y.value is not None:
                included += 1
                value = y.value if value is None else value * y.value
        return LineYield(value, tuple(per), included, len(self.stations))

    def bottleneck(self, window, totals=None):
        totals = totals if totals is not None else self.by_station(window)
        cands = []
        for st in self.stations:
            p = self.station_pace(window, st, totals)
            if st.count_trusted and p.value is not None:
                cands.append((p.value, st.sequence, st, p))
        if not cands:
            return None, len(cands), len(self.stations)
        _, _, st, p = min(cands, key=lambda c: (c[0], c[1]))
        return Bottleneck(st, p, len(cands), len(self.stations)), len(cands), len(self.stations)

    def top_scrap(self, window, station=None):
        ids = self.shift_ids(window)
        by_code = defaultdict(int)
        for d in window.dates():
            for sid, cid, shid, qty in self.scrap.get(d, ()):
                if (ids is None or shid in ids) and (station is None or sid == station.pk):
                    by_code[cid] += qty
        if not by_code:
            return None
        cid, qty = max(by_code.items(), key=lambda kv: (kv[1], -kv[0]))
        code = ScrapCode.objects.select_related("cause_station_hint").get(pk=cid)
        return TopScrap(code, qty, code.cause_station_hint)

    def demand_gaps(self, window):
        gaps = []
        families = PartFamily.objects.in_bulk(self.routed_families)
        for d in window.dates():
            if not self.plan.shift_windows(window, d):
                continue
            day = self.demand.get(d, {})
            for fid in sorted(self.routed_families, key=lambda f: families[f].name):
                units = day.get(fid)
                if units is None:
                    gaps.append(DemandGap(self.line, families[fid], d, "missing"))
                elif units == 0:
                    gaps.append(DemandGap(self.line, families[fid], d, "zero"))
        return gaps

    def daily(self, d, as_of):
        w = Window.day(d, as_of)
        totals = self.by_station(w)
        return self.line_pace(w, totals).value, self.line_yield(w, totals).value


def _dates(date_from, date_to):
    d = date_from
    while d <= date_to:
        yield d
        d += timedelta(days=1)


def _trend_text(trend):
    def span(vals):
        vals = [v for v in vals if v is not None]
        if not vals:
            return "no data"
        return f"{math.floor(vals[0] * 100 + 1e-9)}% to {math.floor(vals[-1] * 100 + 1e-9)}%"

    return (f"Pace 14-day trend: {span([t.pace for t in trend])}; "
            f"yield {span([t.yield_ for t in trend])}")


def _line_row(data, window):
    totals = data.by_station(window)
    pace = data.line_pace(window, totals)
    ly = data.line_yield(window, totals)
    series = {}
    for d in _dates(min(window.date_from - timedelta(days=BASELINE_DAYS),
                        window.date_to - timedelta(days=TREND_DAYS - 1)), window.date_to):
        series[d] = data.daily(d, window.as_of)
    base_dates = list(_dates(window.date_from - timedelta(days=BASELINE_DAYS), window.date_from - timedelta(days=1)))
    pace_hist = [series[d][0] for d in base_dates]
    yield_hist = [series[d][1] for d in base_dates]
    pace_class = S.classify(pace.value, pace_hist)
    yield_class = S.classify(ly.value, yield_hist)
    pace_base, yield_base = S.baseline(pace_hist), S.baseline(yield_hist)
    pace_delta = pace.value - pace_base["mean"] if pace.value is not None and pace_base else 0
    yield_delta = ly.value - yield_base["mean"] if ly.value is not None and yield_base else 0

    gaps = data.demand_gaps(window)
    gap_families = sorted({g.family.name for g in gaps})
    bottleneck, used, total = data.bottleneck(window, totals)
    top = data.top_scrap(window)

    if pace.reason in ("no_shift", "not_started"):
        word, key = None, pace.reason
        sentence = "No shift scheduled." if pace.reason == "no_shift" else "Shift not started."
    else:
        # Judgment call: a MISSING demand row anywhere in the window (an admin error) also sets
        # "No demand set", even when other families give pace a number; demand 0 alone does not.
        no_demand = pace.reason == "no_demand" or any(g.reason == "missing" for g in gaps)
        word = S.status(no_demand=no_demand, pace_value=pace.value,
                        pace_suppressed=pace.reason == "final_station_unverified", yield_class=yield_class)
        key = S.STATUS_KEYS[word]
        note = None
        if pace.reason == "no_demand" and not gap_families:
            note = "No demand set."
        elif pace.reason == "final_station_unverified":
            note = "Pace unavailable: final-station counts unverified."
        sentence = S.sentence(
            status_word=word, pace_class=pace_class, yield_class=yield_class, pace_delta=pace_delta,
            yield_delta=yield_delta, top_code=top.label if top else None,
            cause_station=top.cause_station.name if top and top.cause_station else None,
            no_demand_families=gap_families, pace_note=note,
            bottleneck=bottleneck.sentence if bottleneck else None,
        )
    trend = [TrendPoint(d, *series[d]) for d in _dates(window.date_to - timedelta(days=TREND_DAYS - 1), window.date_to)]
    return LineHealthRow(
        line=data.line, window=window, status=word, status_key=key, sentence=sentence, pace=pace, yield_=ly,
        top_scrap=top, bottleneck=bottleneck,
        bottleneck_coverage_text=f"bottleneck chosen from {used} of {total} stations",
        pace_class=pace_class, yield_class=yield_class, pace_baseline=pace_base, yield_baseline=yield_base,
        trend=trend, trend_text=_trend_text(trend), no_demand=gaps,
        final_station_trusted=bool(data.final and data.final.count_trusted),
    )


# ---------------------------------------------------------------- public API


def line_health(lines, date_from, date_to=None, *, shift=None, as_of=None):
    """Line Health / Area rows, one per line (design Component 6, design review D3, D4, D22).

    lines: iterable of Line. date_from..date_to: production days (Line Health opens on the last
    completed production day, D21). shift: a Shift or shift name to narrow to one shift (Area opens
    on the last completed shift). as_of: instant for a live window (default now).
    Returns [LineHealthRow].
    """
    window = Window(date_from, date_to or date_from,
                    None if shift is None else (shift if isinstance(shift, str) else shift.name), as_of)
    start = min(window.date_from - timedelta(days=BASELINE_DAYS), window.date_to - timedelta(days=TREND_DAYS - 1))
    return [_line_row(_LineData(line, start, window.date_to), window)
            for line in Line.objects.filter(pk__in=[ln.pk for ln in lines]).select_related("hub")]


def station_rows(line, window):
    """The station drill-down for a line: one StationRow per station in sequence, with per-part rows."""
    data = _LineData(line, window.date_from, window.date_to)
    totals = data.by_station(window)
    per_part = data.agg(window, lambda sid, pid, fid: (sid, pid))
    grades = {(g.station_id, g.part_id): g for g in StationPartGrade.objects.filter(station__line=line)}
    bottleneck, _, _ = data.bottleneck(window, totals)
    part_ids = {pid for (_, pid) in per_part} | {pid for (_, pid) in grades}
    parts = Part.objects.select_related("family").in_bulk(part_ids)
    out = []
    for st in data.stations:
        prow = []
        for (sid, pid), (jobs, qty, scrap, good) in sorted(per_part.items(), key=lambda kv: parts[kv[0][1]].hub_part_code):
            if sid != st.pk:
                continue
            g = grades.get((sid, pid))
            c = matrix_cell(g.grade if g else None, st.count_trusted, g.days_of_history if g else None,
                            st.station_type)
            prow.append(PartRow(parts[pid], parts[pid].family, jobs, qty, good, scrap, Yield(good, scrap), c.grade,
                                g.days_of_history if g else None, c))
        volume = defaultdict(int)
        for r in prow:
            volume[r.grade] += r.quantity
        if not volume:
            for (sid, pid), g in grades.items():
                if sid == st.pk:
                    volume[matrix_cell(g.grade, True, station_type=st.station_type).grade] += g.job_count
        grade = majority_grade(volume)
        days = min((r.days_of_history or 0 for r in prow if r.grade == grade), default=None)
        jobs, qty, scrap, good = totals.get(st.pk, [0, 0, 0, 0])
        out.append(StationRow(
            station=st, count_trusted=st.count_trusted, grade=grade,
            cell=matrix_cell(grade, st.count_trusted, days, st.station_type),
            pace=data.station_pace(window, st, totals), jobs=jobs, quantity=qty, good=good, scrap=scrap,
            yield_=Yield(good, scrap), parts=prow, top_scrap=data.top_scrap(window, st),
            is_bottleneck=bool(bottleneck and bottleneck.station.pk == st.pk),
            batch=_batch_stats(st, window, data) if st.station_type == Station.BATCH else None,
        ))
    return out


def _batch_stats(station, window, data):
    qs = Job.objects.filter(station=station, completed_at__isnull=False, excluded_from_counts=False,
                            production_date__range=(window.date_from, window.date_to))
    ids = data.shift_ids(window)
    if ids is not None:
        qs = qs.filter(shift_id__in=ids)
    loads = defaultdict(int)
    for start, end, qty in qs.values_list("started_at", "completed_at", "quantity"):
        loads[(start, end)] += qty
    shifts = sum(1 for d in window.dates() for w in data.plan.shift_windows(window, d)
                 if w.planned_seconds(until=window.now()) > 0)
    cycle = (sum((e - s).total_seconds() for s, e in loads) / len(loads) / 60) if loads else None
    return BatchStats(len(loads), shifts, sum(loads.values()), cycle)


def _segments(w, until):
    """Working segments of shift window w up to `until`: [start, end) minus breaks."""
    stop = min(w.end, until)
    segs, t = [], w.start
    for b_start, b_end in sorted(w.breaks):
        if b_start > t:
            segs.append((t, min(b_start, stop)))
        t = max(t, b_end)
    if t < stop:
        segs.append((t, stop))
    return [(a, b) for a, b in segs if b > a]


def _covered_seconds(segs, intervals):
    merged = []
    for s, e in sorted(intervals):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return sum(max(0.0, (min(b, e) - max(a, s)).total_seconds()) for a, b in segs for s, e in merged)


def _upstream_gap_seconds(segs, completions, threshold_s):
    """Total working time in gaps of `threshold_s` or more between upstream completions (and from
    the window start / to the window end)."""
    def offset(t):
        return sum(max(0.0, (min(b, t) - a).total_seconds()) for a, b in segs if t > a)

    total = sum((b - a).total_seconds() for a, b in segs)
    pts = sorted([0.0, total] + [offset(c) for c in completions])
    return sum(g for g in (b - a for a, b in zip(pts, pts[1:])) if g >= threshold_s)


def operator_groups(station, part=None, window=None):
    """My Shift / operator drill-down for one station: one OperatorGroup per part that ran in the
    window (or only `part`). Gated by the display matrix: a station that is not count-trusted
    returns groups with no rows and the message "Counts unverified, keep tally."."""
    if window is None:
        raise ValueError("operator_groups needs a window")
    station = Station.objects.select_related("line__hub").get(pk=station.pk)
    settings = SiteSettings.load()
    plan = HubPlan(station.line.hub)
    ids = plan.shift_ids(window)
    ids = None if ids is None else set(ids)
    now = window.now()

    acc = defaultdict(lambda: defaultdict(lambda: {"jobs": 0, "qty": 0, "excl": 0, "rq": 0, "rs": 0.0,
                                                   "windows": set()}))
    for pid, aid, oid, d, shid, jobs, qty, excl, rq, rs in queries.operator_shift(
            station.pk, window.date_from, window.date_to, part.pk if part else None):
        if ids is not None and shid not in ids:
            continue
        a = acc[pid][aid]
        a["jobs"] += jobs
        a["qty"] += qty
        a["excl"] += excl
        a["rq"] += rq
        a["rs"] += rs
        if shid is not None:
            a["windows"].add((shid, d))
    part_ids = set(acc) | ({part.pk} if part else set())
    parts = Part.objects.select_related("family").in_bulk(part_ids)
    grades = {g.part_id: g for g in StationPartGrade.objects.filter(station=station, part_id__in=part_ids)}
    aliases = OperatorAlias.objects.select_related("operator").in_bulk(
        {aid for p in acc.values() for aid in p})
    upstream = (Station.objects.filter(line=station.line, sequence__lt=station.sequence)
                .order_by("-sequence").first())
    demand = dict(TaktConfig.objects.filter(line=station.line, production_date=window.date_to)
                  .values_list("family_id", "demand_units"))
    upstream_cache = {}

    def upstream_gap(key):
        if key not in upstream_cache:
            w = plan.stamper.window(*key)
            if w is None:
                upstream_cache[key] = 0.0
            else:
                segs = _segments(w, now)
                done = list(Job.objects.filter(station=upstream, completed_at__gte=w.start,
                                               completed_at__lt=min(w.end, now))
                            .values_list("completed_at", flat=True))
                upstream_cache[key] = _upstream_gap_seconds(segs, done, settings.upstream_gap_minutes * 60)
        return upstream_cache[key]

    groups = []
    for pid in sorted(part_ids, key=lambda p: parts[p].hub_part_code):
        p = parts[pid]
        g = grades.get(pid)
        c = matrix_cell(g.grade if g else None, station.count_trusted, g.days_of_history if g else None,
                        station.station_type)
        takt = plan.takt_seconds(window.date_to, demand.get(p.family_id))
        group = OperatorGroup(
            station=station, part=p, grade=c.grade, days_of_history=g.days_of_history if g else None, cell=c,
            message=c.message, rows=[], jobs=None, quantity=None, takt_seconds=takt,
            takt_rate_per_hour=3600 / takt if takt else None, median_rate_per_hour=None,
        )
        groups.append(group)
        if not c.show_operator_rows:
            continue
        if c.show_rates:
            group.median_rate_per_hour = queries.median_operator_rate(
                station.pk, pid, window.date_to - timedelta(days=RATE_MEDIAN_DAYS - 1), window.date_to)
        group.jobs = sum(a["jobs"] for a in acc[pid].values())
        group.quantity = sum(a["qty"] for a in acc[pid].values())
        for aid, a in sorted(acc[pid].items()):
            alias = aliases[aid]
            op = alias.operator
            unmapped = op is None
            rate = rate_vs = None
            if c.show_rates and not unmapped and a["rs"] > 0:
                rate = a["rq"] / a["rs"] * 3600
                if group.median_rate_per_hour:
                    rate_vs = rate / group.median_rate_per_hour - 1
            gap = None
            if c.show_transaction_gap and a["windows"]:
                gap = _transaction_gap_seconds(alias, a["windows"], plan, now) / 60
            ugap = None
            if c.show_upstream_gap and upstream is not None and a["windows"]:
                ugap = sum(upstream_gap(k) for k in a["windows"]) / 60
            tenure = under = None
            if c.show_tenure and op is not None and op.hire_date:
                tenure = (window.date_to - op.hire_date).days
                under = tenure < settings.tenure_flag_days
            group.rows.append(OperatorRow(
                alias=alias, operator=op, unmapped=unmapped, hub_operator_id=alias.hub_operator_id,
                jobs=a["jobs"], quantity=a["qty"], excluded_jobs=a["excl"], rate_per_hour=rate,
                rate_vs_median=rate_vs, rate_text=c.rate_text, transaction_gap_minutes=gap,
                upstream_gap_minutes=ugap, tenure_days=tenure, tenure_under_90=under, can_dispute=c.show_dispute,
            ))
    return groups


def _transaction_gap_seconds(alias, windows, plan, now):
    """Shift time minus breaks minus time covered by the person's jobs (any station). Open jobs
    cover time up to `now`."""
    spans = [w for w in (plan.stamper.window(shid, d) for shid, d in windows) if w is not None]
    if not spans:
        return 0.0
    lo, hi = min(w.start for w in spans), max(w.end for w in spans)
    jobs = Job.objects.filter(started_at__lt=hi).exclude(completed_at__lte=lo)
    jobs = jobs.filter(alias__operator=alias.operator) if alias.operator_id else jobs.filter(alias=alias)
    intervals = [(s, e or now) for s, e in jobs.values_list("started_at", "completed_at") if (e or now) > s]
    total = 0.0
    for w in spans:
        segs = _segments(w, now)
        total += sum((b - a).total_seconds() for a, b in segs) - _covered_seconds(segs, intervals)
    return total


def _reasons(row, station):
    out = [Reason("rate", row.rate_vs_median_display)]
    if row.tenure_under_90:
        out.append(Reason("tenure", "tenure < 90 days"))
    if row.upstream_gap_minutes:
        out.append(Reason("upstream_gap", f"upstream output gap {round(row.upstream_gap_minutes)} min"))
    if row.transaction_gap_minutes is not None and row.transaction_gap_minutes >= TXN_GAP_REASON_MINUTES:
        out.append(Reason("transaction_gap", f"{round(row.transaction_gap_minutes)} min with no transaction"))
    return out


def worth_a_conversation(scope, window):
    """Design review D5: up to 3 names per GREEN, count-trusted station-part group, each with its
    context reasons. Never plant-wide: scope must name lines or stations. Never on the print sheet.
    Returns [ConversationGroup]."""
    if not scope.lines and not scope.stations:
        raise ValueError("worth_a_conversation is never plant-wide: give lines (an area) or stations")
    out = []
    for st in scope.station_list():
        for grp in operator_groups(st, scope.part, window):
            if not grp.cell.show_comparisons:
                continue
            cands = [r for r in grp.rows if not r.unmapped and r.rate_vs_median is not None
                     and r.rate_vs_median <= -WORTH_A_CONVERSATION_MIN_SHORTFALL]
            cands.sort(key=lambda r: (r.rate_vs_median, r.alias.pk))
            items = [ConversationItem(r, st, grp.part, _reasons(r, st)) for r in cands[:WORTH_A_CONVERSATION_LIMIT]]
            if items:
                out.append(ConversationGroup(st, grp.part, items))
    return out


def job_list(scope, window):
    """A person's jobs in the window, for the inline dispute list (design review D14) and the
    excluded-jobs view (D23). scope needs an alias or operator, or explicit stations.
    Open jobs are listed (is_open) but never counted. Returns [JobItem] by start time."""
    if scope.alias is None and scope.operator is None and not scope.stations:
        raise ValueError("job_list needs scope.alias, scope.operator or scope.stations")
    qs = (Job.objects.select_related("alias__operator", "station", "part", "excluded_by")
          .filter(production_date__range=(window.date_from, window.date_to)))
    if window.shift_name:
        qs = qs.filter(shift__name=window.shift_name)
    if scope.alias is not None:
        qs = qs.filter(alias=scope.alias)
    if scope.operator is not None:
        qs = qs.filter(alias__operator=scope.operator)
    if scope.stations:
        qs = qs.filter(station__in=scope.stations)
    elif scope.lines:
        qs = qs.filter(station__line__in=scope.lines)
    if scope.part is not None:
        qs = qs.filter(part=scope.part)
    out = []
    for j in qs.order_by("started_at", "id"):
        done = j.completed_at is not None
        out.append(JobItem(job=j, is_open=not done, counted=done and not j.excluded_from_counts,
                           in_rates=done and not j.excluded and not j.excluded_from_counts,
                           excluded=j.excluded or j.excluded_from_counts, exclusion_reason=j.exclusion_reason,
                           excluded_by=j.excluded_by, excluded_at=j.excluded_at))
    return out


def shift_comparison(lines, production_date, *, as_of=None):
    """Area's 3-shift table (design review D7): line pace and yield for each scheduled shift that
    starts on `production_date`. Returns [ShiftComparisonRow]."""
    out = []
    for line in Line.objects.filter(pk__in=[ln.pk for ln in lines]).select_related("hub"):
        data = _LineData(line, production_date, production_date)
        cells = []
        for w in data.plan.windows_on(production_date):
            win = Window.for_shift(w.shift, production_date, as_of)
            totals = data.by_station(win)
            cells.append(ShiftCell(w.shift.name, win, data.line_pace(win, totals), data.line_yield(win, totals)))
        out.append(ShiftComparisonRow(line, production_date, cells))
    return out


def missing_demand(lines=None, date_from=None, date_to=None, *, include_zero=True, now=None):
    """Family-days with no demand on scheduled days (eng review D7), for the admin warning and
    Line Health. reason "missing": no TaktConfig row (red admin warning); "zero": demand 0.
    Default range: 7 days back to 7 days ahead of today in each hub's plant timezone.
    Returns [DemandGap] by line, date, family."""
    now = now or djtz.now()
    qs = Line.objects.select_related("hub").order_by("name")
    if lines is not None:
        qs = qs.filter(pk__in=[ln.pk for ln in lines])
    out = []
    for line in qs:
        today = local_date(line.hub, now)
        lo = date_from or today - timedelta(days=7)
        hi = date_to or today + timedelta(days=7)
        data = _LineData(line, lo, hi)
        gaps = data.demand_gaps(Window(lo, hi, None, now))
        out += [g for g in gaps if include_zero or g.reason == "missing"]
    return out


__all__ = [
    "line_health", "station_rows", "operator_groups", "worth_a_conversation", "job_list", "shift_comparison",
    "missing_demand", "Window", "Scope", "Pace", "Yield", "LineYield", "TopScrap", "Bottleneck", "DemandGap",
    "TrendPoint", "LineHealthRow", "StationRow", "PartRow", "BatchStats", "OperatorRow", "OperatorGroup",
    "Reason", "ConversationItem", "ConversationGroup", "JobItem", "ShiftCell", "ShiftComparisonRow", "DASH",
]
