"""Ingest: MES transactions -> Takt Jobs and ScrapEvents (design Component 1, eng review D2-D4, D9).

One run per hub, all inside one database transaction: if the MES becomes unreachable
mid-run (MesUnavailable), nothing from that run is committed; only a failed IngestRun
row is written afterwards.

Stamping rule (eng review D2): naive MES times are localized with Hub.plant_timezone via
zoneinfo and stored UTC. A Job is stamped (shift, production_date) by its COMPLETION
instant (the shift that completed the units is the shift that gets the count); a job
still open is stamped by its start and re-stamped when it closes. Scrap is stamped by
when it was found.
"""

import logging
import time
from collections import Counter
from datetime import datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone as djtz

from core.models import IngestRun, Job, OperatorAlias, Part, ScrapCode, ScrapEvent, Station
from core.shifts import ShiftStamper

from .adapters import MesUnavailable, get_adapter

log = logging.getLogger("takt.ingest")

INCREMENTAL_LOOKBACK = timedelta(hours=48)
NIGHTLY_LOOKBACK = timedelta(days=7)

JOB_SYNC_FIELDS = ["alias", "station", "part", "started_at", "completed_at", "quantity", "shift", "production_date"]
# Never in this list: excluded, excluded_from_counts, exclusion_reason, excluded_by, excluded_at.
# Takt keeps a confirmed exclusion even if the MES record changes, until a supervisor clears it.
JOB_UPDATE_FIELDS = JOB_SYNC_FIELDS + ["last_ingested_at"]


class IngestFailed(Exception):
    def __init__(self, run, cause):
        super().__init__(str(cause))
        self.run = run


def localize(naive, tz, fold=0):
    """A MES timestamp -> aware UTC. Naive values are wall-clock plant time (eng review D2)."""
    if naive is None:
        return None
    if naive.tzinfo is not None:
        return naive.astimezone(timezone.utc)
    return naive.replace(tzinfo=tz, fold=fold).astimezone(timezone.utc)


def localize_pair(start_naive, complete_naive, tz):
    """Localize a job's start and complete. In the repeated fall-back hour a naive time is
    ambiguous; zoneinfo takes the first occurrence (fold=0). If that would make the job end
    before it started, the completion must be the second occurrence (fold=1)."""
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
        self.skipped = Counter()
        self.changed = 0
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
        self._aliases_for(r.operator_id for r in rows)
        candidates = {}
        for r in rows:
            self.seen_job_ids.add(r.mes_id)
            station, part = self._resolve(r.station_code, r.part_code)
            if station is None:
                continue
            started, completed = localize_pair(r.started_at, r.completed_at, self.tz)
            shift, pdate = self.stamper.stamp(completed or started)
            candidates[r.mes_id] = Job(
                hub=self.hub, mes_id=r.mes_id, alias=self.aliases[r.operator_id], station=station, part=part,
                started_at=started, completed_at=completed, quantity=r.quantity, shift=shift, production_date=pdate,
            )
        if not candidates:
            return
        existing = {
            j["mes_id"]: j
            for j in Job.objects.filter(hub=self.hub, mes_id__in=list(candidates)).values(
                "mes_id", *[f"{f}_id" if f in ("alias", "station", "part", "shift") else f for f in JOB_SYNC_FIELDS]
            )
        }
        to_write = []
        for mes_id, job in candidates.items():
            old = existing.get(mes_id)
            if old is None or any(
                old[f"{f}_id" if f in ("alias", "station", "part", "shift") else f]
                != getattr(job, f"{f}_id" if f in ("alias", "station", "part", "shift") else f)
                for f in JOB_SYNC_FIELDS
            ):
                to_write.append(job)
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
                # so yield is never overstated; they show up in the admin to be described.
                code, _ = ScrapCode.objects.get_or_create(hub=self.hub, code=r.scrap_code)
                self.scrap_codes[r.scrap_code] = code
                log.warning("ingest %s: new scrap code %r created without a cause hint", self.hub.code, r.scrap_code)
            at = localize(r.occurred_at, self.tz)
            shift, pdate = self.stamper.stamp(at)
            candidates[r.mes_id] = ScrapEvent(
                hub=self.hub, mes_id=r.mes_id, occurred_at=at, shift=shift, production_date=pdate, part=part,
                station_found=station, scrap_code=code, quantity=r.quantity,
            )
        if not candidates:
            return
        fields = ["occurred_at", "shift_id", "production_date", "part_id", "station_found_id", "scrap_code_id", "quantity"]
        existing = {
            e["mes_id"]: e
            for e in ScrapEvent.objects.filter(hub=self.hub, mes_id__in=list(candidates)).values("mes_id", *fields)
        }
        to_write = [
            ev for mid, ev in candidates.items()
            if mid not in existing or any(existing[mid][f] != getattr(ev, f) for f in fields)
        ]
        if to_write:
            ScrapEvent.objects.bulk_create(
                to_write, update_conflicts=True, unique_fields=["hub", "mes_id"],
                update_fields=["occurred_at", "shift", "production_date", "part", "station_found", "scrap_code",
                               "quantity"],
            )
            self.changed += len(to_write)


def run_ingest(hub, nightly=False, lookback=None, now=None, adapter=None):
    """Ingest one hub. Returns the IngestRun; raises IngestFailed (after recording a failed
    IngestRun) when the MES is unavailable, with nothing from the run committed."""
    now = now or djtz.now()
    lookback = lookback or (NIGHTLY_LOOKBACK if nightly else INCREMENTAL_LOOKBACK)
    since = now - lookback
    run = IngestRun(hub=hub, mode=IngestRun.NIGHTLY if nightly else IngestRun.INCREMENTAL, started_at=djtz.now())
    adapter = adapter or get_adapter(hub)
    t0 = time.monotonic()
    try:
        with transaction.atomic():
            ctx = _HubContext(hub, run)
            open_before = list(
                Job.objects.filter(hub=hub, completed_at__isnull=True).values_list("mes_id", flat=True)
            )

            # 1. Trailing window (48 h; 7 days nightly), by the MES last-modified column.
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
            run.skipped_detail = "; ".join(f"{k}: {v} row(s)" for k, v in sorted(ctx.skipped.items()))
            run.query_seconds = round(getattr(adapter, "query_seconds", time.monotonic() - t0), 3)
            run.save()
            hub.last_successful_ingest_at = finished
            fields = ["last_successful_ingest_at"]
            if ctx.changed:
                hub.last_new_transaction_at = finished
                fields.append("last_new_transaction_at")
            hub.save(update_fields=fields)
    except (MesUnavailable, NotImplementedError) as e:
        run.ok = False
        run.finished_at = djtz.now()
        run.error = f"{type(e).__name__}: {e}"
        run.query_seconds = round(getattr(adapter, "query_seconds", 0.0), 3)
        run.rows_read = getattr(adapter, "rows_read", 0)
        run.pk = None
        run.save()
        log.error("ingest %s FAILED (nothing committed): %s", hub.code, run.error)
        raise IngestFailed(run, e) from e
    finally:
        if hasattr(adapter, "close"):
            adapter.close()
    if run.rows_skipped:
        log.warning("ingest %s: skipped %d row(s) with unknown codes: %s", hub.code, run.rows_skipped,
                    run.skipped_detail)
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
        # Filter on the instant, a day early, so rows whose old stamp was wrong are still caught.
        cutoff = datetime.combine(from_date - timedelta(days=1), dtime(0), tzinfo=ZoneInfo(hub.plant_timezone))
        jobs = jobs.filter(started_at__gte=cutoff)
        scrap = scrap.filter(occurred_at__gte=cutoff)
    changed_jobs = []
    for j in jobs.only("id", "started_at", "completed_at", "shift_id", "production_date").iterator(chunk_size=5000):
        shift, pdate = stamper.stamp(j.completed_at or j.started_at)
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
