"""The trust grade (design Component 4). One code path: the nightly job and the audit both call
compute_grades(), so they always agree.

For each station-and-part over the trailing window (4 weeks), three shares of completed jobs:
- D: durations under 10% or over 300% of the station-part median;
- C: completes in the last 30 minutes of a shift, or an identical completion timestamp shared
  with 3 or more other jobs (at the same station);
- O: jobs that overlap another job by the same operator.

Green: D, C and O each under 10%. Red: any over 25%, or the station is batch. Amber: the rest.
Under 4 weeks of history: "not graded yet" (design review D11), which displays like amber.
Only the trailing window's jobs are graded; history counts from the first job in the last 8 weeks.
Thresholds are SiteSettings (admin).
"""

from dataclasses import dataclass
from datetime import date, timedelta

from django.db import connection, transaction
from django.utils import timezone as djtz

from core.models import Station, StationPartGrade, SiteSettings
from core.shifts import local_date

# History (design review D11, "needs 4 weeks"): days since the station-part's first job, looking
# back at most this many grade windows. The doc defines no gap rule; bounding the look-back keeps
# an old run (the demo's March DST slice) from making a part look 4 weeks old when it has only
# just started again, while any part that has run within the last 8 weeks counts in full.
HISTORY_LOOKBACK_WINDOWS = 2
# Window functions (clustered completions, overlaps) see the graded dates plus this pad, so a job
# on the first or last graded day is compared with its neighbours on adjacent days.
PAD_DAYS = 1

GRADE_SQL = """
WITH base AS (
    SELECT q.*,
           COALESCE('o' || q.operator_id::text, 'a' || q.alias_id::text)
             || CASE WHEN q.station_type = 'batch' THEN ':batch' ELSE ':flow' END AS person
    FROM trust_job_quality q
    WHERE q.production_date BETWEEN %(pad_from)s AND %(pad_to)s
), win AS (
    -- O: overlap with another job by the same person. Batch-station jobs (a kiln load runs for
    -- hours while the loader works elsewhere) are compared only with other batch jobs.
    SELECT b.*,
           (count(*) OVER (PARTITION BY b.station_id, b.completed_at)) - 1 AS same_complete_others,
           (count(*) OVER (PARTITION BY b.station_id, b.started_at, b.completed_at)) - 1
               AS same_start_complete_others,
           COALESCE(b.started_at < max(b.completed_at) OVER w_prev, false)
             OR COALESCE(lead(b.started_at) OVER w_next < b.completed_at, false) AS overlaps
    FROM base b
    WINDOW w_prev AS (PARTITION BY b.person ORDER BY b.started_at, b.job_id
                      ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING),
           w_next AS (PARTITION BY b.person ORDER BY b.started_at, b.job_id)
), w AS (
    SELECT * FROM win
    WHERE station_id = ANY(%(stations)s) AND production_date BETWEEN %(date_from)s AND %(date_to)s
), med AS (
    SELECT station_id, part_id, percentile_cont(0.5) WITHIN GROUP (ORDER BY duration_s) AS median_s
    FROM w GROUP BY station_id, part_id
)
SELECT w.station_id, w.part_id, count(*) AS n, COALESCE(sum(w.quantity), 0) AS volume,
       avg(CASE WHEN w.duration_s < %(short)s * m.median_s OR w.duration_s > %(long)s * m.median_s
                THEN 1 ELSE 0 END)::float8 AS d_share,
       -- C: completed in the last N minutes of the scheduled shift that CONTAINS the completion
       -- (not the stamped start shift), or an identical completion shared with 3+ other jobs.
       avg(CASE WHEN (w.completed_window_end IS NOT NULL
                      AND w.completed_at >= w.completed_window_end - make_interval(mins => %(eos)s))
                  OR w.same_complete_others >= %(cluster)s
                THEN 1 ELSE 0 END)::float8 AS c_share,
       avg(CASE WHEN w.overlaps THEN 1 ELSE 0 END)::float8 AS o_share,
       avg(CASE WHEN w.same_start_complete_others >= 1 THEN 1 ELSE 0 END)::float8 AS batch_share
FROM w JOIN med m ON m.station_id = w.station_id AND m.part_id = w.part_id
GROUP BY w.station_id, w.part_id
"""

FIRST_JOB_SQL = """
SELECT station_id, part_id, min(production_date) FROM metrics_job_fact
WHERE station_id = ANY(%(stations)s) AND production_date BETWEEN %(since)s AND %(date_to)s
GROUP BY station_id, part_id
"""


@dataclass
class GradeResult:
    station: Station
    part_id: int
    grade: str
    d_share: float
    c_share: float
    o_share: float
    job_count: int
    volume: int
    days_of_history: int
    batch_share: float  # share of jobs sharing identical start AND complete timestamps (audit hint)


def grade_from_shares(d, c, o, *, batch=False, days_of_history=None, settings=None):
    """Green/amber/red/not_graded from the three shares. Boundaries (eng test plan): 9.9% green,
    10% amber, 25% amber, 25.1% red. Batch stations are red by definition, whatever their history."""
    s = settings or SiteSettings.load()
    if batch:
        return StationPartGrade.RED
    if days_of_history is not None and days_of_history < s.grade_window_days:
        return StationPartGrade.NOT_GRADED
    if max(d, c, o) > s.grade_red_min_share:
        return StationPartGrade.RED
    if d < s.grade_green_max_share and c < s.grade_green_max_share and o < s.grade_green_max_share:
        return StationPartGrade.GREEN
    return StationPartGrade.AMBER


def days_of_history(first_job_date, as_of_date):
    """Calendar days from the station-part's first job (within the look-back) to as_of, inclusive."""
    if first_job_date is None or first_job_date > as_of_date:
        return 0
    return (as_of_date - first_job_date).days + 1


def _as_of_date(station, as_of):
    if isinstance(as_of, date) and not hasattr(as_of, "hour"):
        return as_of
    return local_date(station.line.hub, as_of or djtz.now())


def compute_grades(stations=None, as_of=None, window_days=None, settings=None):
    """Grade every station-part with completed jobs in the trailing window ending on `as_of`
    (a date, or an instant read in each hub's plant timezone; default now). Returns GradeResults;
    persists nothing (see save_grades)."""
    s = settings or SiteSettings.load()
    window_days = window_days or s.grade_window_days
    stations = list(stations if stations is not None else Station.objects.select_related("line__hub"))
    by_date = {}
    for st in stations:
        by_date.setdefault(_as_of_date(st, as_of), []).append(st)
    out = []
    with connection.cursor() as cur:
        for as_of_d, group in by_date.items():
            ids = [st.pk for st in group]
            st_by_id = {st.pk: st for st in group}
            cur.execute(FIRST_JOB_SQL, {
                "stations": ids, "date_to": as_of_d,
                "since": as_of_d - timedelta(days=HISTORY_LOOKBACK_WINDOWS * window_days - 1),
            })
            first = {(sid, pid): d for sid, pid, d in cur.fetchall()}
            date_from = as_of_d - timedelta(days=window_days - 1)
            cur.execute(GRADE_SQL, {
                "stations": ids, "date_from": date_from, "date_to": as_of_d,
                "pad_from": date_from - timedelta(days=PAD_DAYS), "pad_to": as_of_d + timedelta(days=PAD_DAYS),
                "short": s.duration_short_ratio, "long": s.duration_long_ratio,
                "eos": s.end_of_shift_minutes, "cluster": s.clustered_timestamp_others,
            })
            for sid, pid, n, volume, d_share, c_share, o_share, batch_share in cur.fetchall():
                st = st_by_id[sid]
                days = days_of_history(first.get((sid, pid)), as_of_d)
                grade = grade_from_shares(d_share, c_share, o_share, batch=st.station_type == Station.BATCH,
                                          days_of_history=days, settings=s)
                out.append(GradeResult(st, pid, grade, d_share, c_share, o_share, n, int(volume), days, batch_share))
    return out


@transaction.atomic
def save_grades(results, stations=None, computed_at=None):
    """Upsert StationPartGrade rows. Station-parts of `stations` with no jobs in the window keep
    their row but become not graded with job_count 0, so a stale green never lingers."""
    computed_at = computed_at or djtz.now()
    seen = set()
    for r in results:
        StationPartGrade.objects.update_or_create(
            station=r.station, part_id=r.part_id,
            defaults=dict(grade=r.grade, d_share=r.d_share, c_share=r.c_share, o_share=r.o_share,
                          job_count=r.job_count, days_of_history=r.days_of_history, computed_at=computed_at),
        )
        seen.add((r.station.pk, r.part_id))
    stale = StationPartGrade.objects.all()
    if stations is not None:
        stale = stale.filter(station__in=stations)
    for g in stale:
        if (g.station_id, g.part_id) not in seen:
            g.grade = StationPartGrade.RED if g.station.station_type == Station.BATCH else StationPartGrade.NOT_GRADED
            g.d_share = g.c_share = g.o_share = 0.0
            g.job_count, g.days_of_history, g.computed_at = 0, 0, computed_at
            g.save()


SEVERITY = {StationPartGrade.GREEN: 0, StationPartGrade.NOT_GRADED: 1, StationPartGrade.AMBER: 2,
            StationPartGrade.RED: 3}


def majority_grade(volume_by_grade):
    """The grade covering the majority (largest share) of a station's volume (Component 4).
    Ties go to the more cautious grade. Returns None when there is no volume."""
    items = [(g, v) for g, v in volume_by_grade.items() if v > 0]
    if not items:
        return None
    return max(items, key=lambda gv: (gv[1], SEVERITY.get(gv[0], 0)))[0]


__all__ = ["compute_grades", "save_grades", "grade_from_shares", "days_of_history", "majority_grade",
           "GradeResult", "HISTORY_LOOKBACK_WINDOWS", "PAD_DAYS"]
