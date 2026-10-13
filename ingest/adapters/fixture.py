"""FixtureAdapter: HubAdapter over the synthetic MES in schema `fake_mes`.

It behaves like a real hub adapter should (eng review D9):
- its own read-only connection, never Django's;
- keyset pagination ordered by (last_modified, txn_id), BATCH_SIZE (5,000) rows per batch;
- every batch in its own transaction with SET LOCAL statement_timeout (30 s);
- any connection or query error (including a timeout) raises MesUnavailable.

Timestamps come back naive, exactly as the synthetic MES stores them (timestamp
WITHOUT time zone); ingest localizes them with Hub.plant_timezone.
"""

import time
from zoneinfo import ZoneInfo

import psycopg
from psycopg import sql

from .. import fake_mes
from .base import BATCH_SIZE, STATEMENT_TIMEOUT_SECONDS, HubAdapter, MesJobRow, MesScrapRow, MesUnavailable

JOB_SELECT = f"""
SELECT txn_id, station_code, part_code, operator_code, start_ts, complete_ts, qty, last_modified
FROM {fake_mes.JOB_TABLE}
WHERE hub_code = %s AND last_modified >= %s AND (last_modified, txn_id) > (%s, %s)
ORDER BY last_modified, txn_id
LIMIT %s
"""
JOB_BY_ID = f"""
SELECT txn_id, station_code, part_code, operator_code, start_ts, complete_ts, qty, last_modified
FROM {fake_mes.JOB_TABLE}
WHERE hub_code = %s AND txn_id = ANY(%s)
ORDER BY txn_id
"""
SCRAP_SELECT = f"""
SELECT txn_id, found_station_code, part_code, scrap_code, qty, event_ts, last_modified
FROM {fake_mes.SCRAP_TABLE}
WHERE hub_code = %s AND last_modified >= %s AND (last_modified, txn_id) > (%s, %s)
ORDER BY last_modified, txn_id
LIMIT %s
"""

class FixtureAdapter(HubAdapter):
    has_last_modified = True

    def __init__(self, hub, statement_timeout_seconds=STATEMENT_TIMEOUT_SECONDS, **connect_overrides):
        super().__init__(hub)
        self.statement_timeout_ms = int(statement_timeout_seconds * 1000)
        self.connect_overrides = connect_overrides
        self.tz = ZoneInfo(hub.plant_timezone)
        self.rows_read = 0
        self.query_seconds = 0.0
        self.batches_read = 0
        self._conn = None

    # -- connection
    def _connection(self):
        if self._conn is None or self._conn.closed:
            try:
                self._conn = fake_mes.connect(autocommit=False, **self.connect_overrides)
                self._conn.read_only = True  # Takt never writes to the MES
            except psycopg.Error as e:
                raise MesUnavailable(f"{self.hub.code}: cannot connect to the MES: {e}") from e
        return self._conn

    def close(self):
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def _run(self, query, params=()):
        """One bounded query in its own read-only transaction with SET LOCAL statement_timeout."""
        conn = self._connection()
        t0 = time.monotonic()
        try:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute(sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(self.statement_timeout_ms)))
                cur.execute(query, params)
                rows = cur.fetchall()
        except psycopg.Error as e:
            self.close()
            raise MesUnavailable(f"{self.hub.code}: MES query failed: {e}") from e
        finally:
            self.query_seconds += time.monotonic() - t0
        return rows

    def _naive_since(self, since):
        return fake_mes.naive_local(since, self.tz)

    def _keyset(self, query, since, batch_size, convert):
        since_local = self._naive_since(since)
        key = (since_local, -1)
        while True:
            rows = self._run(query, (self.hub.code, since_local, key[0], key[1], batch_size))
            if not rows:
                return
            self.rows_read += len(rows)
            self.batches_read += 1
            yield [convert(r) for r in rows]
            last = rows[-1]
            key = (last[-1], last[0])
            if len(rows) < batch_size:
                return

    @staticmethod
    def _job(r):
        txn, station, part, op, start, complete, qty, _lm = r
        return MesJobRow(str(txn), station, part, op, start, complete, qty)

    @staticmethod
    def _scrap(r):
        txn, station, part, code, qty, at, _lm = r
        return MesScrapRow(str(txn), at, station, part, code, qty)

    # -- HubAdapter
    def iter_jobs_since(self, since, batch_size=BATCH_SIZE):
        yield from self._keyset(JOB_SELECT, since, batch_size, self._job)

    def fetch_jobs_by_ids(self, mes_ids, batch_size=BATCH_SIZE):
        ids = [int(i) for i in mes_ids]
        for k in range(0, len(ids), batch_size):
            rows = self._run(JOB_BY_ID, (self.hub.code, ids[k:k + batch_size]))
            self.rows_read += len(rows)
            self.batches_read += 1
            if rows:
                yield [self._job(r) for r in rows]

    def iter_scrap_since(self, since, batch_size=BATCH_SIZE):
        yield from self._keyset(SCRAP_SELECT, since, batch_size, self._scrap)
