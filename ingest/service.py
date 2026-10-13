"""Ingest: MES transactions -> Takt Jobs and ScrapEvents (design Component 1, eng review D2-D4, D9).

One run per hub, all inside one database transaction: if the MES becomes unreachable
mid-run (MesUnavailable), or anything else goes wrong, nothing from that run is
committed; only a failed IngestRun row is written afterwards. A transaction-level
advisory lock per hub makes an overlapping run (e.g. a slow nightly re-read still going
when the 5-minute cron fires) skip instead of racing.

Naive MES times are localized with Hub.plant_timezone via zoneinfo and stored UTC
(eng review D2). Which instant stamps a Job's shift is decided in one place:
`stamp_instant()`.
"""

import logging
import time
from collections import Counter
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.db import connection, transaction
from django.utils import timezone as djtz

from core.models import IngestRun, Job, OperatorAlias, Part, ScrapCode, ScrapEvent, Station
from core.shifts import ShiftStamper

from .adapters import get_adapter

log = logging.getLogger("takt.ingest")

INCREMENTAL_LOOKBACK = timedelta(hours=48)
NIGHTLY_LOOKBACK = timedelta(days=7)

# pg_try_advisory_xact_lock(LOCK_NAMESPACE, hub.pk): one ingest run per hub at a time.
LOCK_NAMESPACE = 0x7A4B7  # "takt ingest"

_FK = ("alias", "station", "part", "shift")
# Fields that come from the MES row itself: a difference here is a new or changed MES transaction.
JOB_MES_FIELDS = ["alias", "station", "part", "started_at", "completed_at", "quantity"]
# Fields Takt derives. A re-stamp after a calendar change is written but is not new MES data.
JOB_STAMP_FIELDS = ["shift", "production_date"]
JOB_SYNC_FIELDS = JOB_MES_FIELDS + JOB_STAMP_FIELDS
# Never in this list: excluded, excluded_from_counts, exclusion_reason, excluded_by, excluded_at.
# Takt keeps a confirmed exclusion even if the MES record changes, until a supervisor clears it.
JOB_UPDATE_FIELDS = JOB_SYNC_FIELDS + ["last_ingested_at"]


def _attr(f):
    return f"{f}_id" if f in _FK else f


class IngestFailed(Exception):
    def __init__(self, run, cause):
        super().__init__(str(cause))
        self.run = run


def stamp_instant(started_at, completed_at):
    """THE stamping rule for Jobs (eng review D2), kept in one place so it can be flipped here.

    user decision 2026-10-12: a job counts toward the shift it started in.

    A Job's (shift, production_date) comes from its START instant (completed_at only if
    started_at were ever null). So a late close changes the earlier, working shift's totals,
    jobs batch-closed at shift end still count for the shift that started them, and an open
    job is stamped by its start and never moves when it closes.
    Used by ingest and by restamp_shifts (whose --from filters on started_at to match).
    """
    return started_at if started_at is not None else completed_at


def localize(naive, tz, fold=0):
    """A MES timestamp -> aware UTC. Naive values are wall-clock plant time (eng review D2)."""
    if naive is None:
        return None
    if naive.tzinfo is not None:
        return naive.astimezone(timezone.utc)
    return naive.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)


def localize_pair(start_naive, complete_naive, tz):
    """Localize a job's start and complete.

    In the repeated fall-back hour (2026-11-01 01:00-02:00 America/Chicago) a naive time is
    ambiguous; zoneinfo takes the first occurrence (fold=0). If that would make the job end
    before it started, the completion must be the second occurrence (fold=1).

    Residual error, accepted: when the naive order still looks plausible but the real
    instants straddle the repeat (e.g. started 00:50 CDT, completed 01:20 CST, read as
    01:20 CDT), the duration and the completion instant are off by up to 1 h. Counts are
    unaffected: the row lands exactly once (upsert by MES ID) on the same night shift.
    Naive local time cannot carry the difference; only a MES storing UTC or an offset can.
    """
    start = localize(start_naive, tz)
    complete = localize(complete_naive, tz)
    if complete is not None and complete < start and complete_naive.tzinfo is None:
        second = localize(complete_naive, tz, fold=1)
        if second >= start:
            complete = second
    return start, complete


class _HubContext:
    """Per-run caches: stations, parts, aliases, scrap codes and the shift stamper."""

    def __init__(self, hub, run):
        self.hub = hub
        self.run = run
        self.tz = ZoneInfo(hub.plant_timezone)
        self.stamper = ShiftStamper(hub)
        by_code = {}
        for s in Station.objects.filter(line__hub=hub):
            by_code.setdefault(s.hub_station_code, []).append(s)
        self.stations = {c: v[0] for c, v in by_code.items() if len(v) == 1}
        self.ambiguous_stations = {c for c, v in by_code.items() if len(v) > 1}
        self.parts = {p.hub_part_code: p for p in Part.objects.filter(hub=hub)}
        self.aliases = {a.hub_operator_id: a for a in OperatorAlias.objects.filter(hub=hub)}
        self.scrap_codes = {c.code: c for c in ScrapCode.objects.filter(hub=hub)}
        self.skipped = Counter()  # rows not stored: unknown or ambiguous station or part
        self.notes = Counter()  # rows stored but worth an admin's look (a new scrap code)
        self.changed = 0  # rows written: new, MES-changed, or re-stamped
        self.mes_changed = 0  # rows new or with changed MES fields: drives hub freshness
        self.seen_job_ids = set()

    def _skip(self, kind, code):
        self.skipped[f"{kind} {code!r}"] += 1

    def _resolve(self, station_code, part_code):
        station = self.stations.get(station_code)
        part = self.parts.get(part_code)
        if station is None:
            why = "ambiguous station" if station_code in self.ambiguous_stations else "unknown station"
            self._skip(why, station_code)
            return None, None
        if part is None:
            self._skip("unknown part", part_code)
            return None, None
        return station, part

    def _aliases_for(self, operator_ids):
        new = [i for i in set(operator_ids) if i not in self.aliases]
        if new:
            # Unknown hub operator IDs become unmapped aliases (operator=None): they count in
            # totals now, land in the admin mapping queue, and mapping later backfills them
            # automatically because Jobs reference the alias.
            OperatorAlias.objects.bulk_create(
                [OperatorAlias(hub=self.hub, hub_operator_id=i, operator=None) for i in new], ignore_conflicts=True
            )
            for a in OperatorAlias.objects.filter(hub=self.hub, hub_operator_id__in=new):
                self.aliases[a.hub_operator_id] = a
            log.info("ingest %s: %d new unmapped operator alias(es): %s", self.hub.code, len(new), ", ".join(sorted(new)))

    def upsert_jobs(self, rows):
        resolved = []
        for r in rows:
            self.seen_job_ids.add(r.mes_id)
            station, part = self._resolve(r.station_code, r.part_code)
            if station is not None:
                resolved.append((r, station, part))
        # Only rows Takt keeps create aliases: a skipped row never adds an unmapped operator.
        self._aliases_for(r.operator_id for r, _, _ in resolved)
        candidates = {}
        for r, station, part in resolved:
            started, completed = localize_pair(r.started_at, r.completed_at, self.tz)
            shift, pdate = self.stamper.stamp(stamp_instant(started, completed))
            candidates[r.mes_id] = Job(
                hub=self.hub, mes_id=r.mes_id, alias=self.aliases[r.operator_id], station=station, part=part,
                started_at=started, completed_at=completed, quantity=r.quantity, shift=shift, production_date=pdate,
            )
        if not candidates:
            return
        existing = {
            j["mes_id"]: j
            for j in Job.objects.filter(hub=self.hub, mes_id__in=list(candidates)).values(
                "mes_id", *[_attr(f) for f in JOB_SYNC_FIELDS]
            )
        }
        to_write = []
        for mes_id, job in candidates.items():
            old = existing.get(mes_id)
            mes_diff = old is None or any(old[_attr(f)] != getattr(job, _attr(f)) for f in JOB_MES_FIELDS)
            stamp_diff = old is not None and any(old[_attr(f)] != getattr(job, _attr(f)) for f in JOB_STAMP_FIELDS)
            if mes_diff or stamp_diff:
                to_write.append(job)
                self.mes_changed += int(mes_diff)
        if to_write:
            now = djtz.now()
            for j in to_write:
                j.last_ingested_at = now
            Job.objects.bulk_create(
                to_write, update_conflicts=True, unique_fields=["hub", "mes_id"], update_fields=JOB_UPDATE_FIELDS
            )
            self.changed += len(to_write)

    def upsert_scrap(self, rows):
        candidates = {}
        for r in rows:
            station, part = self._resolve(r.station_code, r.part_code)
            if station is None:
                continue
            code = self.scrap_codes.get(r.scrap_code)
            if code is None:
                # Unknown scrap codes are created (label only, no cause hint) rather than dropped,
                # so scrap under a new code still counts against yield instead of vanishing.
                # It is noted on the IngestRun and shows up in the admin to be described.
                code, _ = ScrapCode.objects.get_or_create(hub=self.hub, code=r.scrap_code)
                self.scrap_codes[r.scrap_code] = code
                log.warning("ingest %s: new scrap code %r created without a cause hint", self.hub.code, r.scrap_code)
            if not code.description and code.cause_station_hint_id is None:
                self.notes[f"new scrap code {r.scrap_code!r} (no description or cause hint yet)"] += 1
            at = localize(r.occurred_at, self.tz)
            shift, pdate = self.stamper.stamp(at)
            candidates[r.mes_id] = ScrapEvent(
                hub=self.hub, mes_id=r.mes_id, occurred_at=at, shift=shift, production_date=pdate, part=part,
                station_found=station, scrap_code=code, quantity=r.quantity,
            )
        if not candidates:
            return
        mes_fields = ["occurred_at", "part_id", "station_found_id", "scrap_code_id", "quantity"]
        fields = mes_fields + ["shift_id", "production_date"]
        existing = {
            e["mes_id"]: e
            for e in ScrapEvent.objects.filter(hub=self.hub, mes_id__in=list(candidates)).values("mes_id", *fields)
        }
        to_write = []
        for mid, ev in candidates.items():
            old = existing.get(mid)
            mes_diff = old is None or any(old[f] != getattr(ev, f) for f in mes_fields)
            if mes_diff or any(old[f] != getattr(ev, f) for f in fields):
                to_write.append(ev)
                self.mes_changed += int(mes_diff)
        if to_write:
            ScrapEvent.objects.bulk_create(
                to_write, update_conflicts=True, unique_fields=["hub", "mes_id"],
                update_fields=["occurred_at", "shift", "production_date", "part", "station_found", "scrap_code",
                               "quantity"],
            )
            self.changed += len(to_write)


class _LockHeld(Exception):
    pass


def _try_lock(hub):
    """Transaction-level advisory lock for this hub; released when the run's transaction ends."""
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_xact_lock(%s, %s)", [LOCK_NAMESPACE, hub.pk])
        return cur.fetchone()[0]


def run_ingest(hub, nightly=False, lookback=None, now=None, adapter=None):
    """Ingest one hub.

    Returns the IngestRun, or None when another run already holds this hub's lock (skipped,
    logged, nothing recorded). On ANY error (MesUnavailable, an adapter stub, a bug) the run's
    transaction rolls back, a failed IngestRun is recorded, and IngestFailed is raised.

    `lookback` (ingest --days N) makes this a backfill run; it cannot also be nightly.
    """
    if nightly and lookback is not None:
        raise ValueError("A run is either nightly (7 days) or a backfill (--days N), not both.")
    now = now or djtz.now()
    if lookback is not None:
        mode = IngestRun.BACKFILL
    else:
        mode = IngestRun.NIGHTLY if nightly else IngestRun.INCREMENTAL
        lookback = NIGHTLY_LOOKBACK if nightly else INCREMENTAL_LOOKBACK
    since = now - lookback
    run = IngestRun(hub=hub, mode=mode, started_at=djtz.now(), lookback_hours=lookback.total_seconds() / 3600)
    t0 = time.monotonic()
    try:
        with transaction.atomic():
            if not _try_lock(hub):
                raise _LockHeld()
            if adapter is None:
                adapter = get_adapter(hub)
            ctx = _HubContext(hub, run)
            open_before = list(
                Job.objects.filter(hub=hub, completed_at__isnull=True).values_list("mes_id", flat=True)
            )

            # 1. Trailing window (48 h; 7 days nightly; N days backfill), by the MES last-modified column.
            for batch in adapter.iter_jobs_since(since):
                ctx.upsert_jobs(batch)

            # gstack-shortcut(dec-eb6ab8fd-8076-46f6-81b9-103462da2dc8): MES hard-deleted rows keep counting in Takt, upgrade when Open Question 1 shows the MES hard-deletes corrected rows or a confirmed dispute traces to a deleted row (then add the nightly missing-ID sweep, eng review D4 option A)

            # 2. Every Job still open in Takt, by MES ID, regardless of age (eng review D3).
            reread = [m for m in open_before if m not in ctx.seen_job_ids]
            run.open_jobs_reread = len(reread)
            for batch in adapter.fetch_jobs_by_ids(reread):
                ctx.upsert_jobs(batch)

            # 3. Scrap events, same window.
            for batch in adapter.iter_scrap_since(since):
                ctx.upsert_scrap(batch)

            finished = djtz.now()
            run.finished_at = finished
            run.ok = True
            run.rows_read = getattr(adapter, "rows_read", 0)
            run.rows_changed = ctx.changed
            run.rows_skipped = sum(ctx.skipped.values())
            detail = [f"{k}: {v} row(s)" for k, v in sorted(ctx.skipped.items())]
            detail += [f"{k}: {v} row(s)" for k, v in sorted(ctx.notes.items())]
            run.skipped_detail = "; ".join(detail)
            run.query_seconds = round(getattr(adapter, "query_seconds", time.monotonic() - t0), 3)
            run.save()
            hub.last_successful_ingest_at = finished
            fields = ["last_successful_ingest_at"]
            # Freshness moves only for new or MES-changed rows. Takt-side re-stamps don't count, and
            # neither do skipped unknown-code rows: ingest keeps no state for them and re-reads them on
            # every run, so counting them would keep a stalled hub looking fresh for up to 48 h.
            if ctx.mes_changed:
                hub.last_new_transaction_at = finished
                fields.append("last_new_transaction_at")
            hub.save(update_fields=fields)
    except _LockHeld:
        log.info("ingest %s %s skipped: another ingest run holds this hub's lock", hub.code, mode)
        return None
    except Exception as e:  # noqa: BLE001 - every failure is recorded, then re-raised as IngestFailed
        run.ok = False
        run.finished_at = djtz.now()
        run.error = f"{type(e).__name__}: {e}"
        run.query_seconds = round(getattr(adapter, "query_seconds", 0.0), 3)
        run.rows_read = getattr(adapter, "rows_read", 0)
        run.pk = None
        run.save()
        log.error("ingest %s FAILED (nothing committed): %s", hub.code, run.error, exc_info=True)
        raise IngestFailed(run, e) from e
    finally:
        if adapter is not None and hasattr(adapter, "close"):
            adapter.close()
    if run.skipped_detail:
        log.warning("ingest %s: %d row(s) skipped; %s", hub.code, run.rows_skipped, run.skipped_detail)
    log.info(
        "ingest %s %s ok: rows_read=%d rows_changed=%d open_jobs_reread=%d rows_skipped=%d query_seconds=%.3f",
        hub.code, run.mode, run.rows_read, run.rows_changed, run.open_jobs_reread, run.rows_skipped, run.query_seconds,
    )
    return run


def restamp(hub, from_date=None):
    """Re-stamp shift + production_date on Jobs and ScrapEvents after a ShiftCalendar change."""
    stamper = ShiftStamper(hub)
    jobs = Job.objects.filter(hub=hub)
    scrap = ScrapEvent.objects.filter(hub=hub)
    if from_date is not None:
        # Filter on the stamping instant (stamp_instant: the start), a day early, so a night
        # shift that started the evening before --from is still caught.
        cutoff = datetime.combine(from_date - timedelta(days=1), dtime(0), tzinfo=ZoneInfo(hub.plant_timezone))
        jobs = jobs.filter(started_at__gte=cutoff)
        scrap = scrap.filter(occurred_at__gte=cutoff)
    changed_jobs = []
    for j in jobs.only("id", "started_at", "completed_at", "shift_id", "production_date").iterator(chunk_size=5000):
        shift, pdate = stamper.stamp(stamp_instant(j.started_at, j.completed_at))
        sid = shift.pk if shift else None
        if (sid, pdate) != (j.shift_id, j.production_date):
            j.shift_id, j.production_date = sid, pdate
            changed_jobs.append(j)
    Job.objects.bulk_update(changed_jobs, ["shift", "production_date"], batch_size=5000)
    changed_scrap = []
    for e in scrap.only("id", "occurred_at", "shift_id", "production_date").iterator(chunk_size=5000):
        shift, pdate = stamper.stamp(e.occurred_at)
        sid = shift.pk if shift else None
        if (sid, pdate) != (e.shift_id, e.production_date):
            e.shift_id, e.production_date = sid, pdate
            changed_scrap.append(e)
    ScrapEvent.objects.bulk_update(changed_scrap, ["shift", "production_date"], batch_size=5000)
    return len(changed_jobs), len(changed_scrap)
