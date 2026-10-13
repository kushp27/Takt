"""The synthetic MES: Postgres schema `fake_mes` (eng review D8 substitute).

We have no access to the real Cores Hub or Dies Hub (Open Question 1), so the demo and
the ingest tests read from a synthetic MES with the same awkward traits a real one has:
naive local timestamps (timestamp WITHOUT time zone), a last-modified column, open
jobs, late closes, batch-closes at shift end, overlapping jobs, kiln batches, unmapped
operator badges and unknown codes. Table names start with `synthetic_` so nobody
mistakes them for real MES tables; none of these names come from the real MES.

The MES is reached over its OWN connection (never Django's), exactly as a real hub
would be: so ingest's "commit nothing on MesUnavailable" and the 30 s statement timeout
are exercised through a real SQL path. Generation is deterministic for a given seed:
each shift window is generated from its own RNG, so `fixture_tick` can regenerate a
window and get identical rows.
"""

import hashlib
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg
from django.db import connections

from core import shifts as shiftcal
from core.models import Hub

from . import fixture_spec as spec

SCHEMA = "fake_mes"
JOB_TABLE = f"{SCHEMA}.synthetic_job_txn"
SCRAP_TABLE = f"{SCHEMA}.synthetic_scrap_txn"
STATE_TABLE = f"{SCHEMA}.synthetic_generator_state"

DDL = f"""
DROP SCHEMA IF EXISTS {SCHEMA} CASCADE;
CREATE SCHEMA {SCHEMA};
CREATE TABLE {JOB_TABLE} (
    txn_id        bigint PRIMARY KEY,
    hub_code      text NOT NULL,
    station_code  text NOT NULL,
    part_code     text NOT NULL,
    operator_code text NOT NULL,
    start_ts      timestamp without time zone NOT NULL,
    complete_ts   timestamp without time zone NULL,
    qty           integer NOT NULL,
    last_modified timestamp without time zone NOT NULL
);
CREATE INDEX synthetic_job_txn_lm ON {JOB_TABLE} (hub_code, last_modified, txn_id);
CREATE TABLE {SCRAP_TABLE} (
    txn_id             bigint PRIMARY KEY,
    hub_code           text NOT NULL,
    found_station_code text NOT NULL,
    part_code          text NOT NULL,
    scrap_code         text NOT NULL,
    qty                integer NOT NULL,
    event_ts           timestamp without time zone NOT NULL,
    last_modified      timestamp without time zone NOT NULL
);
CREATE INDEX synthetic_scrap_txn_lm ON {SCRAP_TABLE} (hub_code, last_modified, txn_id);
CREATE TABLE {STATE_TABLE} (
    hub_code          text PRIMARY KEY,
    seed              integer NOT NULL,
    load_end          timestamptz NOT NULL,
    generated_through timestamptz NOT NULL
);
"""

JOB_COLS = ("txn_id", "hub_code", "station_code", "part_code", "operator_code", "start_ts", "complete_ts",
            "qty", "last_modified")
SCRAP_COLS = ("txn_id", "hub_code", "found_station_code", "part_code", "scrap_code", "qty", "event_ts",
              "last_modified")


def connection_params(**overrides):
    """psycopg params for the MES connection: the Takt database server (and, under pytest,
    the test database), but a separate connection from Django's."""
    s = connections["default"].settings_dict
    params = {"dbname": s["NAME"], "user": s["USER"], "password": s["PASSWORD"],
              "host": s["HOST"] or None, "port": s["PORT"] or None, "connect_timeout": 10}
    params.update(overrides)
    return {k: v for k, v in params.items() if v is not None}


def connect(autocommit=True, **overrides):
    return psycopg.connect(autocommit=autocommit, **connection_params(**overrides))


def create_schema():
    with connect() as conn:
        conn.execute(DDL)


def drop_schema():
    with connect() as conn:
        conn.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")


def schema_exists():
    with connect() as conn:
        return conn.execute("SELECT 1 FROM pg_namespace WHERE nspname = %s", [SCHEMA]).fetchone() is not None


def _copy(conn, table, cols, rows):
    with conn.cursor() as cur, cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)


def insert_jobs(rows):
    """Insert raw MES job rows (tuples in JOB_COLS order, naive local timestamps). For tests."""
    with connect() as conn:
        _copy(conn, JOB_TABLE, JOB_COLS, rows)


def insert_scrap(rows):
    with connect() as conn:
        _copy(conn, SCRAP_TABLE, SCRAP_COLS, rows)


def execute(sql, params=None):
    """Run one statement against the synthetic MES (tests use it to close jobs, etc.)."""
    with connect() as conn:
        cur = conn.execute(sql, params)
        return cur.fetchall() if cur.description else None


# ---------------------------------------------------------------- generation


@dataclass
class SynJob:
    txn_id: int
    hub_code: str
    station_code: str
    part_code: str
    operator_code: str
    start: datetime  # aware, real instant
    complete: datetime  # aware, real instant the MES records the completion (may be late)
    qty: int


@dataclass
class SynScrap:
    txn_id: int
    hub_code: str
    station_code: str
    part_code: str
    scrap_code: str
    qty: int
    at: datetime


def txn_id(*parts):
    """Deterministic positive 56-bit id from a key."""
    return int(hashlib.blake2b("|".join(map(str, parts)).encode(), digest_size=7).hexdigest(), 16)


def naive_local(instant, tz):
    """What a naive-local MES stores: wall-clock time, no zone (the fall-back hour repeats)."""
    return instant.astimezone(tz).replace(tzinfo=None, fold=0)


def _segments(window):
    """Working segments of a window: [start, end) minus breaks."""
    segs, t = [], window.start
    for b_start, b_end in sorted(window.breaks):
        if b_start > t:
            segs.append((t, min(b_start, window.end)))
        t = max(t, b_end)
    if t < window.end:
        segs.append((t, window.end))
    return segs


def _pick(rng, weights):
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


class Generator:
    """Deterministic synthetic transactions for one hub."""

    def __init__(self, hub, seed, load_end):
        self.hub = hub
        self.hs = spec.hub_spec(hub.code)
        self.seed = seed
        self.load_end = load_end
        self.tz = ZoneInfo(hub.plant_timezone)
        self.load_end_date = load_end.astimezone(self.tz).date()
        self.crews = {}
        for r in spec.roster(self.hs):
            self.crews.setdefault((r.station_code, r.shift_name), []).append(r)
        self.temps = spec.unmapped_ids(self.hs)
        self._spike_days = None

    # -- day-level randomness
    def _day_rng(self, d, what):
        return random.Random(f"{self.seed}|{self.hub.code}|{d.isoformat()}|{what}")

    def day_pace(self, d):
        return math.exp(self._day_rng(d, "pace").gauss(0, 0.04))

    def day_yield_factor(self, d):
        return math.exp(self._day_rng(d, "yield").gauss(0, 0.25))

    def spike_days(self, stamper):
        """The last two working days up to the data end: Cores voids spike (a "yield problem")."""
        if self._spike_days is None:
            days, d = [], self.load_end_date
            while len(days) < 2 and d > self.load_end_date - timedelta(days=10):
                if stamper.windows_on(d):
                    days.append(d)
                d -= timedelta(days=1)
            self._spike_days = set(days)
        return self._spike_days

    def family_weights(self, station_code):
        w = {}
        for fam, stations in self.hs.routing.items():
            if station_code in stations:
                w[fam] = self.hs.demand[fam] * stations[station_code]
        return w

    def part_weights(self, fam, d):
        new_code, new_fam, days_before = spec.NEW_PART
        if fam == new_fam and d >= self.load_end_date - timedelta(days=days_before):
            return spec.NEW_PART_WEIGHTS
        return self.hs.families[fam]

    def _part(self, rng, station_code, d):
        fam = _pick(rng, self.family_weights(station_code))
        return _pick(rng, self.part_weights(fam, d))

    # -- per window
    def window_jobs(self, window):
        d = window.production_date
        out = []
        pace_day = self.day_pace(d)
        for st in self.hs.stations:
            rng = random.Random(f"{self.seed}|{self.hub.code}|{d.isoformat()}|{window.shift.name}|{st.code}")
            crew = list(self.crews.get((st.code, window.shift.name), []))
            op_ids = [c.hub_operator_id for c in crew]
            skills = {c.hub_operator_id: c.skill for c in crew}
            if crew and rng.random() < 0.08:  # a temp badge covers for one regular: unmapped alias
                temp = rng.choice(self.temps)
                op_ids[rng.randrange(len(op_ids))] = temp
                skills[temp] = 0.9
            if st.batch:
                out += self._kiln(rng, st, window, op_ids[0], pace_day)
            else:
                for i, op in enumerate(op_ids):
                    out += self._flow(rng, st, window, op, skills[op], len(op_ids), pace_day, i)
        rng = random.Random(f"{self.seed}|{self.hub.code}|{d.isoformat()}|{window.shift.name}|extras")
        if self.hs.unknown_station_code and rng.random() < 0.3:
            for k in range(2):
                t = window.start + timedelta(minutes=rng.uniform(30, 400))
                out.append(SynJob(txn_id(self.hub.code, d, window.shift.name, "rwk", k), self.hub.code,
                                  self.hs.unknown_station_code, self._part(rng, self.hs.stations[0].code, d),
                                  op_ids[0] if op_ids else "X", t, t + timedelta(minutes=12), 5))
        if self.hs.unknown_part_code and rng.random() < 0.1:
            t = window.start + timedelta(minutes=rng.uniform(30, 400))
            out.append(SynJob(txn_id(self.hub.code, d, window.shift.name, "unknown-part"), self.hub.code,
                              self.hs.stations[0].code, self.hs.unknown_part_code,
                              self.crews[(self.hs.stations[0].code, window.shift.name)][0].hub_operator_id,
                              t, t + timedelta(minutes=20), 6))
        return out

    def _flow(self, rng, st, window, op, skill, crew_n, pace_day, op_index):
        d = window.production_date
        segs = _segments(window)
        planned = sum((b - a).total_seconds() for a, b in segs)
        per_op = st.target_per_shift * st.pace * pace_day / crew_n
        n_jobs = max(1.0, per_op / st.qty)
        cycle = planned * 0.95 / n_jobs
        close_at = window.end - timedelta(minutes=rng.uniform(2, 20))  # this operator's batch-close time
        jobs = []
        t = segs[0][0] + timedelta(seconds=rng.uniform(60, 480))
        seg_i, n = 0, 0
        stop_at = window.end - timedelta(minutes=10)
        while seg_i < len(segs):
            seg_start, seg_end = segs[seg_i]
            t = max(t, seg_start)
            dur = cycle / skill * rng.lognormvariate(0, 0.15)
            r = rng.random()
            if r < 0.008:
                dur *= 4.5  # a long outlier (D share)
            elif r < 0.015:
                dur *= 0.06  # a suspiciously short one (D share)
            end = t + timedelta(seconds=dur)
            if end > seg_end:
                last = seg_i == len(segs) - 1
                if not last and rng.random() < 0.05:
                    # left open over the break: the break lands inside the job
                    end = end + (segs[seg_i + 1][0] - seg_end)
                    seg_i += 1
                elif last:
                    break
                else:
                    seg_i += 1
                    continue
            if end > stop_at:
                break
            n += 1
            complete = end
            next_t = end + timedelta(seconds=rng.uniform(20, 120))
            if st.behaviour == "eos_close" and rng.random() < st.behaviour_share and end < close_at:
                complete = close_at  # forgotten, batch-closed at shift end (C share; overlaps too)
            elif st.behaviour == "eos_close" and rng.random() < 0.02:
                complete = end + timedelta(hours=rng.uniform(16, 30))  # closed late, next day
            if st.behaviour == "overlap" and rng.random() < st.behaviour_share:
                next_t = t + timedelta(seconds=dur * rng.uniform(0.3, 0.7))  # runs two jobs at once
            jobs.append(SynJob(txn_id(self.hub.code, d, window.shift.name, st.code, op_index, n), self.hub.code,
                               st.code, self._part(rng, st.code, d), op, t, complete, st.qty))
            t = next_t
        return jobs

    def _kiln(self, rng, st, window, op, pace_day):
        d = window.production_date
        jobs = []
        t = window.start + timedelta(minutes=rng.uniform(10, 20))
        cycle = timedelta(minutes=140)
        b = 0
        per_batch = st.target_per_shift * st.pace * pace_day / 3
        while t + cycle <= window.end + timedelta(minutes=30):
            b += 1
            c = t + cycle + timedelta(minutes=rng.uniform(-8, 8))
            n = rng.randint(3, 5)
            for k in range(n):
                qty = max(1, round(per_batch / n * rng.uniform(0.85, 1.15)))
                # every job in a load shares identical start and complete timestamps
                jobs.append(SynJob(txn_id(self.hub.code, d, window.shift.name, st.code, b, k), self.hub.code,
                                   st.code, self._part(rng, st.code, d), op, t, c, qty))
            t = c + timedelta(minutes=rng.uniform(8, 15))
            if b >= 3:
                break
        return jobs

    def window_scrap(self, window, stamper):
        d = window.production_date
        rng = random.Random(f"{self.seed}|{self.hub.code}|{d.isoformat()}|{window.shift.name}|scrap")
        yf = self.day_yield_factor(d)
        stations = {s.code: s for s in self.hs.stations}
        out = []
        for code, _desc, found, _hint, share in self.hs.scrap:
            st = stations[found]
            factor = yf
            if self.hub.code == "cores" and code == "SYN-VOID" and d in self.spike_days(stamper):
                factor *= 4.0
            total = round(st.target_per_shift * share * factor * rng.uniform(0.8, 1.2))
            k = 0
            span = (window.end - window.start).total_seconds()
            while total > 0:
                q = min(total, rng.randint(1, 4))
                total -= q
                k += 1
                at = window.start + timedelta(seconds=rng.uniform(0, span))
                out.append(SynScrap(txn_id(self.hub.code, d, window.shift.name, "scrap", code, k), self.hub.code,
                                    found, self._part(rng, found, d), code, q, at))
        return out

    def specials(self, window, jobs):
        """The long-open job: opened on day 0, closed on day 9 (> 7-day look-back, eng review D3)."""
        if self.hub.code != "cores" or window.shift.name != "1st":
            return jobs
        target = self.load_end_date - timedelta(days=10)
        if window.production_date != target:
            return jobs
        for j in jobs:
            if j.station_code == self.hs.stations[0].code:
                j.complete = j.start + timedelta(days=9, hours=1)
                break
        return jobs

    def window_all(self, window, stamper):
        return self.specials(window, self.window_jobs(window)), self.window_scrap(window, stamper)


def job_row(j, tz, as_of):
    """The MES row for job j as it looks at `as_of`, or None if it hasn't started yet."""
    if j.start > as_of:
        return None
    if j.complete <= as_of:
        complete, lm = naive_local(j.complete, tz), naive_local(j.complete + timedelta(seconds=5), tz)
    else:
        complete, lm = None, naive_local(j.start + timedelta(seconds=5), tz)
    return (j.txn_id, j.hub_code, j.station_code, j.part_code, j.operator_code, naive_local(j.start, tz),
            complete, j.qty, lm)


def scrap_row(s, tz, as_of):
    if s.at > as_of:
        return None
    return (s.txn_id, s.hub_code, s.station_code, s.part_code, s.scrap_code, s.qty, naive_local(s.at, tz),
            naive_local(s.at + timedelta(seconds=5), tz))


def _dates(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def generate_rows(hub, seed, load_end, dates, as_of):
    """(job rows, scrap rows) for windows starting on `dates` and before `as_of`."""
    gen = Generator(hub, seed, load_end)
    stamper = shiftcal.ShiftStamper(hub)
    tz = gen.tz
    jobs, scrap = [], []
    for d in dates:
        for w in stamper.windows_on(d):
            if w.start >= as_of:
                continue
            js, ss = gen.window_all(w, stamper)
            jobs += [r for r in (job_row(j, tz, as_of) for j in js) if r]
            scrap += [r for r in (scrap_row(s, tz, as_of) for s in ss) if r]
    return jobs, scrap


def load(days=42, seed=7, end=None, hubs=None):
    """Create or replace fake_mes with `days` of history ending at `end` (aware), plus the
    2026-03-07/08 spring-forward night. Returns {hub_code: (jobs, scrap)}."""
    end = end or datetime.now(timezone.utc)
    hubs = list(hubs or Hub.objects.filter(code__in=[h.code for h in spec.HUBS]))
    if not hubs:
        raise RuntimeError("No demo hubs: run `manage.py seed_demo` first.")
    create_schema()
    counts = {}
    with connect() as conn:
        for hub in hubs:
            tz = ZoneInfo(hub.plant_timezone)
            end_date = end.astimezone(tz).date()
            dates = set(_dates(end_date - timedelta(days=days), end_date))
            sf_start, sf_end = spec.SPRING_FORWARD_SLICE
            if end_date > sf_end:
                dates |= set(_dates(sf_start, sf_end))
            jobs, scrap = generate_rows(hub, seed, end, sorted(dates), end)
            _copy(conn, JOB_TABLE, JOB_COLS, jobs)
            _copy(conn, SCRAP_TABLE, SCRAP_COLS, scrap)
            conn.execute(f"INSERT INTO {STATE_TABLE} VALUES (%s, %s, %s, %s)", [hub.code, seed, end, end])
            counts[hub.code] = (len(jobs), len(scrap))
    return counts


UPSERT_JOB = f"""
INSERT INTO {JOB_TABLE} ({', '.join(JOB_COLS)}) VALUES ({', '.join(['%s'] * len(JOB_COLS))})
ON CONFLICT (txn_id) DO UPDATE SET complete_ts = EXCLUDED.complete_ts, last_modified = EXCLUDED.last_modified
WHERE {JOB_TABLE}.complete_ts IS DISTINCT FROM EXCLUDED.complete_ts
"""
UPSERT_SCRAP = f"""
INSERT INTO {SCRAP_TABLE} ({', '.join(SCRAP_COLS)}) VALUES ({', '.join(['%s'] * len(SCRAP_COLS))})
ON CONFLICT (txn_id) DO NOTHING
"""


def tick(now=None):
    """Append synthetic transactions from each hub's last generated time up to `now`, and
    close jobs that have since completed. Returns {hub_code: (job rows written, scrap rows)}."""
    now = now or datetime.now(timezone.utc)
    out = {}
    with connect() as conn:
        state = conn.execute(f"SELECT hub_code, seed, load_end, generated_through FROM {STATE_TABLE}").fetchall()
        for hub_code, seed, load_end, through in state:
            hub = Hub.objects.filter(code=hub_code).first()
            if hub is None or now <= through:
                continue
            tz = ZoneInfo(hub.plant_timezone)
            oldest_open = conn.execute(
                f"SELECT min(start_ts) FROM {JOB_TABLE} WHERE hub_code = %s AND complete_ts IS NULL", [hub_code]
            ).fetchone()[0]
            start_date = through.astimezone(tz).date() - timedelta(days=1)
            if oldest_open is not None:
                start_date = min(start_date, oldest_open.date() - timedelta(days=1))
            jobs, scrap = generate_rows(hub, seed, load_end, list(_dates(start_date, now.astimezone(tz).date())), now)
            with conn.cursor() as cur:
                cur.executemany(UPSERT_JOB, jobs)
                j = cur.rowcount
                cur.executemany(UPSERT_SCRAP, scrap)
                s = cur.rowcount
            conn.execute(f"UPDATE {STATE_TABLE} SET generated_through = %s WHERE hub_code = %s", [now, hub_code])
            out[hub_code] = (j, s)
    return out


__all__ = ["create_schema", "drop_schema", "insert_jobs", "insert_scrap", "load", "tick", "connect",
           "connection_params", "naive_local", "txn_id", "JOB_TABLE", "SCRAP_TABLE", "STATE_TABLE", "execute"]
