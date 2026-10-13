"""Ingest (T1-T3) against the synthetic MES in Postgres schema fake_mes, over its own connection.

Eng review D8 substitute: we have no anonymized extract of the real hubs yet (Open
Question 1), so these run against naive-local synthetic rows through the real SQL path.
"""

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from django.core.management import CommandError, call_command

from core import models as m
from ingest import fake_mes
from ingest.adapters import MesUnavailable
from ingest.adapters.base import BATCH_SIZE
from ingest.adapters.fixture import FixtureAdapter
from ingest.service import IngestFailed, localize_pair, run_ingest
from tests.mes_fixtures import mes, mes_row, naive, small_plant  # noqa: F401

CHI = ZoneInfo("America/Chicago")
UTC = timezone.utc


def _now(y, mo, d, hh=0, mm=0):
    return datetime(y, mo, d, hh, mm, tzinfo=CHI)


@pytest.mark.mes_engine
@pytest.mark.skip(reason="eng review D8: needs an anonymized extract in the hub's own engine; blocked on Open Question 1")
def test_hub_adapters_against_anonymized_extract_in_hub_engine():
    """Placeholder for the real D8 test: Cores Hub / Dies Hub adapters against an anonymized
    extract loaded into a throwaway container of each hub's own database engine."""


def test_naive_local_times_stored_utc_and_third_shift_is_one_production_day(mes):
    p = small_plant()
    code, part = p["s1"].hub_station_code, p["part"].hub_part_code
    mes.insert_jobs([
        mes_row(1, code, part, "OP1", naive(2026, 10, 13, 23, 10), naive(2026, 10, 13, 23, 40)),
        mes_row(2, code, part, "OP1", naive(2026, 10, 14, 6, 20), naive(2026, 10, 14, 6, 50)),
    ])
    run_ingest(p["hub"], now=_now(2026, 10, 14, 8))
    j1, j2 = m.Job.objects.order_by("started_at")
    # 23:10 CDT = 04:10 UTC next day
    assert j1.started_at == datetime(2026, 10, 14, 4, 10, tzinfo=UTC)
    assert (j1.shift, j1.production_date) == (p["third"], date(2026, 10, 13))
    assert (j2.shift, j2.production_date) == (p["third"], date(2026, 10, 13))


def test_dst_fall_back_ambiguous_hour_lands_once_each(mes):
    """2026-11-01 01:00-02:00 repeats. Two jobs in the repeated hour, one per occurrence,
    store the same naive times; both land exactly once, on the 3rd shift of Oct 31."""
    p = small_plant()
    code, part = p["s1"].hub_station_code, p["part"].hub_part_code
    mes.insert_jobs([
        mes_row(10, code, part, "OP1", naive(2026, 11, 1, 1, 10), naive(2026, 11, 1, 1, 40)),
        mes_row(11, code, part, "OP2", naive(2026, 11, 1, 1, 10), naive(2026, 11, 1, 1, 40)),
        # starts in the first 1:50 (CDT), completes in the second 1:20 (CST): naive end < start
        mes_row(12, code, part, "OP3", naive(2026, 11, 1, 1, 50), naive(2026, 11, 1, 1, 20)),
    ])
    run_ingest(p["hub"], now=_now(2026, 11, 1, 9))
    jobs = list(m.Job.objects.order_by("mes_id"))
    assert len(jobs) == 3
    assert {(j.shift_id, j.production_date) for j in jobs} == {(p["third"].pk, date(2026, 10, 31))}
    assert all(j.completed_at > j.started_at for j in jobs)
    assert jobs[2].completed_at - jobs[2].started_at == timedelta(minutes=30)


def test_localize_pair_spring_forward_gap_does_not_crash():
    start, complete = localize_pair(datetime(2026, 3, 8, 1, 50), datetime(2026, 3, 8, 3, 10), CHI)
    assert complete - start == timedelta(minutes=20)


def test_fixture_fall_back_range_no_double_counting_or_drops(mes):
    """A generated range covering the 2026-11-01 fall-back: every MES row with known codes
    lands exactly once, and the 3rd shift of Oct 31 (9 real hours) is one production day."""
    call_command("seed_demo", end="2026-11-03T12:00", days=5, verbosity=0)
    call_command("load_fixture_mes", end="2026-11-03T12:00", days=5, verbosity=0)
    hub = m.Hub.objects.get(code="cores")
    run = run_ingest(hub, lookback=timedelta(days=400), now=_now(2026, 11, 3, 12))
    mes_jobs = fake_mes.execute(
        f"SELECT count(*) FROM {fake_mes.JOB_TABLE} WHERE hub_code='cores' "
        "AND station_code <> 'SYN-RWK-9' AND part_code <> 'C-9999'")[0][0]
    assert m.Job.objects.filter(hub=hub).count() == mes_jobs
    assert run.rows_skipped > 0
    third = m.Shift.objects.get(hub=hub, name="3rd")
    night = m.Job.objects.filter(hub=hub, shift=third, production_date=date(2026, 10, 31))
    assert night.exists()
    w_start = datetime(2026, 10, 31, 23, tzinfo=CHI).astimezone(UTC)
    w_end = datetime(2026, 11, 1, 7, tzinfo=CHI).astimezone(UTC)
    assert w_end - w_start == timedelta(hours=9)
    assert all(w_start <= j.completed_at < w_end for j in night.exclude(completed_at=None))
    # nothing completed inside that night is stamped anywhere else
    leaked = m.Job.objects.filter(hub=hub, completed_at__gte=w_start, completed_at__lt=w_end).exclude(
        shift=third, production_date=date(2026, 10, 31))
    assert not leaked.exists()


def test_default_fixture_includes_spring_forward_night(mes):
    call_command("seed_demo", end="2026-10-12T06:00", days=3, verbosity=0)
    call_command("load_fixture_mes", end="2026-10-12T06:00", days=3, verbosity=0)
    hub = m.Hub.objects.get(code="cores")
    run_ingest(hub, lookback=timedelta(days=400), now=_now(2026, 10, 12, 6))
    third = m.Shift.objects.get(hub=hub, name="3rd")
    night = m.Job.objects.filter(hub=hub, shift=third, production_date=date(2026, 3, 7))
    assert night.count() > 20
    assert all(j.completed_at > j.started_at for j in night.exclude(completed_at=None))


def test_job_opened_day0_closed_day9_lands_via_open_job_reread(mes):
    """Eng review D3: a job closed after the 7-day look-back still lands. The MES's
    last-modified is left at day 0 so only the open-job re-read can find the close."""
    p = small_plant()
    code, part = p["s1"].hub_station_code, p["part"].hub_part_code
    start = naive(2026, 10, 1, 8, 0)
    mes.insert_jobs([mes_row(500, code, part, "OP1", start, None, qty=12, last_modified=start)])
    run_ingest(p["hub"], now=_now(2026, 10, 1, 9))
    job = m.Job.objects.get(mes_id="500")
    assert job.completed_at is None
    assert (job.shift, job.production_date) == (p["first"], date(2026, 10, 1))  # open: stamped by start

    mes.execute(f"UPDATE {fake_mes.JOB_TABLE} SET complete_ts = %s WHERE txn_id = 500", [naive(2026, 10, 10, 9, 0)])
    run = run_ingest(p["hub"], now=_now(2026, 10, 10, 10))  # day 9; last_modified is 9 days old
    job.refresh_from_db()
    assert run.open_jobs_reread == 1
    assert job.completed_at == datetime(2026, 10, 10, 9, tzinfo=CHI)
    assert (job.shift, job.production_date) == (p["first"], date(2026, 10, 10))  # counts on the day it closed


def test_ingest_never_touches_exclusion_fields(mes):
    p = small_plant()
    code, part = p["s1"].hub_station_code, p["part"].hub_part_code
    mes.insert_jobs([mes_row(7, code, part, "OP1", naive(2026, 10, 5, 8), naive(2026, 10, 5, 8, 30), qty=10)])
    run_ingest(p["hub"], now=_now(2026, 10, 5, 12))
    m.Job.objects.filter(mes_id="7").update(excluded=True, excluded_from_counts=True, exclusion_reason="wrong qty")
    mes.execute(f"UPDATE {fake_mes.JOB_TABLE} SET qty = 9, last_modified = %s WHERE txn_id = 7",
                [naive(2026, 10, 5, 13)])
    run = run_ingest(p["hub"], now=_now(2026, 10, 5, 14))
    job = m.Job.objects.get(mes_id="7")
    assert run.rows_changed == 1 and job.quantity == 9
    assert (job.excluded, job.excluded_from_counts, job.exclusion_reason) == (True, True, "wrong qty")


def test_unknown_codes_counted_unmapped_alias_created_and_mapping_backfills(mes, caplog):
    p = small_plant()
    code, part = p["s1"].hub_station_code, p["part"].hub_part_code
    mes.insert_jobs([
        mes_row(1, code, part, "NEW-77", naive(2026, 10, 5, 8), naive(2026, 10, 5, 8, 30)),
        mes_row(2, "NOPE-1", part, "NEW-77", naive(2026, 10, 5, 9), naive(2026, 10, 5, 9, 30)),
        mes_row(3, code, "NOPE-P", "NEW-77", naive(2026, 10, 5, 9), naive(2026, 10, 5, 9, 30)),
    ])
    run = run_ingest(p["hub"], now=_now(2026, 10, 5, 12))
    assert run.rows_skipped == 2
    assert "unknown station 'NOPE-1': 1" in run.skipped_detail
    assert "unknown part 'NOPE-P': 1" in run.skipped_detail
    alias = m.OperatorAlias.objects.get(hub=p["hub"], hub_operator_id="NEW-77")
    assert alias.operator is None
    op = m.Operator.objects.create(name="Sam Rivera")
    alias.operator = op
    alias.save()
    assert m.Job.objects.get(mes_id="1").alias.operator == op  # history follows the alias


def test_ingest_run_logged_and_hub_freshness_updated(mes):
    p = small_plant()
    code, part = p["s1"].hub_station_code, p["part"].hub_part_code
    mes.insert_jobs([mes_row(1, code, part, "OP1", naive(2026, 10, 5, 8), naive(2026, 10, 5, 8, 30))])
    mes.insert_scrap([(1, "cores", code, part, "SYN-VOID", 2, naive(2026, 10, 5, 8, 40), naive(2026, 10, 5, 8, 40))])
    run = run_ingest(p["hub"], now=_now(2026, 10, 5, 12))
    assert (run.ok, run.rows_read, run.rows_changed, run.open_jobs_reread) == (True, 2, 2, 0)
    assert run.query_seconds >= 0 and run.finished_at
    hub = m.Hub.objects.get(pk=p["hub"].pk)
    assert hub.last_successful_ingest_at and hub.last_new_transaction_at
    ev = m.ScrapEvent.objects.get()
    assert (ev.shift, ev.production_date, ev.quantity) == (p["first"], date(2026, 10, 5), 2)
    assert ev.scrap_code.code == "SYN-VOID"
    second = run_ingest(p["hub"], now=_now(2026, 10, 5, 12, 5))
    assert second.rows_changed == 0


def test_mes_unreachable_mid_run_exits_non_zero_and_commits_nothing(mes, monkeypatch):
    p = small_plant()
    code, part = p["s1"].hub_station_code, p["part"].hub_part_code
    base = datetime(2026, 10, 5, 7, 0)
    rows = [mes_row(i, code, part, "OP1", base + timedelta(seconds=i), base + timedelta(seconds=i + 60))
            for i in range(1, BATCH_SIZE + 501)]
    mes.insert_jobs(rows)
    calls = {"n": 0}
    real_run = FixtureAdapter._run

    def flaky(self, query, params=()):
        calls["n"] += 1
        if calls["n"] == 2:  # first batch of 5,000 already upserted; the MES goes away
            raise MesUnavailable("connection lost")
        return real_run(self, query, params)

    monkeypatch.setattr(FixtureAdapter, "_run", flaky)
    with pytest.raises(CommandError):
        call_command("ingest", hub="cores", verbosity=0)
    assert calls["n"] == 2
    assert m.Job.objects.count() == 0  # the first batch was rolled back too
    assert m.OperatorAlias.objects.count() == 0
    failed = m.IngestRun.objects.get()
    assert not failed.ok and "connection lost" in failed.error
    assert m.Hub.objects.get(pk=p["hub"].pk).last_successful_ingest_at is None


def test_mes_connection_refused_raises_mes_unavailable(mes):
    p = small_plant()
    adapter = FixtureAdapter(p["hub"], port=1, connect_timeout=2)
    with pytest.raises(IngestFailed):
        run_ingest(p["hub"], adapter=adapter, now=_now(2026, 10, 5, 12))
    assert m.IngestRun.objects.get().ok is False


def test_keyset_pagination_in_5000_row_batches(mes):
    p = small_plant()
    code, part = p["s1"].hub_station_code, p["part"].hub_part_code
    base = datetime(2026, 10, 5, 7, 0)
    n = 2 * BATCH_SIZE + 1
    # many rows share a last_modified, so the (last_modified, txn_id) keyset must break ties
    rows = [mes_row(i, code, part, f"OP{i % 7}", base, base + timedelta(minutes=5), last_modified=base + timedelta(
        seconds=i // 100)) for i in range(1, n + 1)]
    mes.insert_jobs(rows)
    adapter = FixtureAdapter(p["hub"])
    sizes = [len(b) for b in adapter.iter_jobs_since(_now(2026, 10, 4))]
    adapter.close()
    assert sizes == [5000, 5000, 1]
    run = run_ingest(p["hub"], now=_now(2026, 10, 5, 12))
    assert run.rows_read == n
    assert m.Job.objects.count() == n


def test_statement_timeout_is_set_locally_and_enforced(mes):
    p = small_plant()
    adapter = FixtureAdapter(p["hub"])
    assert adapter._run("SELECT current_setting('statement_timeout')") == [("30s",)]
    adapter.close()
    slow = FixtureAdapter(p["hub"], statement_timeout_seconds=0.2)
    with pytest.raises(MesUnavailable, match="statement timeout"):
        slow._run("SELECT pg_sleep(2)")
    slow.close()


def test_adapter_connection_is_read_only(mes):
    p = small_plant()
    adapter = FixtureAdapter(p["hub"])
    with pytest.raises(MesUnavailable, match="read-only"):
        adapter._run(f"DELETE FROM {fake_mes.JOB_TABLE} RETURNING 1")
    adapter.close()


def test_restamp_shifts_after_calendar_change(mes):
    p = small_plant()
    code, part = p["s1"].hub_station_code, p["part"].hub_part_code
    mes.insert_jobs([mes_row(1, code, part, "OP1", naive(2026, 10, 5, 14, 20), naive(2026, 10, 5, 14, 40))])
    run_ingest(p["hub"], now=_now(2026, 10, 5, 16))
    assert m.Job.objects.get().shift == p["first"]
    m.Shift.objects.filter(pk=p["first"].pk).update(end_time=time(14))
    m.Shift.objects.filter(pk=p["second"].pk).update(start_time=time(14))
    call_command("restamp_shifts", hub="cores", from_date=date(2026, 10, 5), verbosity=0)
    assert m.Job.objects.get().shift == p["second"]


def test_fixture_tick_is_noop_without_env_and_appends_with_it(mes, monkeypatch):
    end = "2026-10-12T06:00"
    call_command("seed_demo", end=end, days=2, verbosity=0)
    call_command("load_fixture_mes", end=end, days=2, verbosity=0)
    count = lambda: fake_mes.execute(f"SELECT count(*), count(complete_ts) FROM {fake_mes.JOB_TABLE}")[0]
    before = count()
    monkeypatch.delenv("TAKT_FIXTURE_LIVE", raising=False)
    call_command("fixture_tick", verbosity=0)
    assert count() == before
    fake_mes.tick(now=datetime(2026, 10, 12, 9, 0, tzinfo=CHI))
    after = count()
    assert after[0] > before[0] and after[1] > before[1]  # new jobs, and open ones closed
    # ticking again to the same time changes nothing (deterministic regeneration)
    fake_mes.tick(now=datetime(2026, 10, 12, 9, 0, tzinfo=CHI))
    assert count() == after


def test_fixture_tick_requires_live_env(mes, monkeypatch):
    call_command("seed_demo", end="2026-10-12T06:00", days=1, verbosity=0)
    call_command("load_fixture_mes", end="2026-10-12T06:00", days=1, verbosity=0)
    monkeypatch.setenv("TAKT_FIXTURE_LIVE", "true")
    call_command("fixture_tick", verbosity=0)  # up to the real now: more rows
    assert fake_mes.execute(f"SELECT max(start_ts) FROM {fake_mes.JOB_TABLE}")[0][0] > datetime(2026, 10, 12, 6)


def test_fixture_generator_is_deterministic_and_realistic(mes):
    end = "2026-10-12T10:00"  # Monday, mid 1st shift: some jobs are open
    call_command("seed_demo", end=end, days=14, verbosity=0)
    call_command("load_fixture_mes", end=end, days=14, seed=7, verbosity=0)
    digest = "SELECT md5(string_agg(t::text, ',' ORDER BY txn_id)) FROM fake_mes.synthetic_job_txn t"
    first = fake_mes.execute(digest)[0][0]
    call_command("load_fixture_mes", end=end, days=14, seed=7, verbosity=0)
    assert fake_mes.execute(digest)[0][0] == first
    call_command("load_fixture_mes", end=end, days=14, seed=8, verbosity=0)
    assert fake_mes.execute(digest)[0][0] != first
    call_command("load_fixture_mes", end=end, days=14, seed=7, verbosity=0)
    q = fake_mes.execute
    # timestamps really are naive (timestamp WITHOUT time zone)
    assert q("SELECT data_type FROM information_schema.columns WHERE table_schema='fake_mes' "
             "AND table_name='synthetic_job_txn' AND column_name='start_ts'")[0][0] == "timestamp without time zone"
    # kiln loads share identical start and complete timestamps
    assert q("SELECT count(*) FROM (SELECT 1 FROM fake_mes.synthetic_job_txn WHERE station_code='SYN-KILN-1' "
             "GROUP BY start_ts, complete_ts HAVING count(*) >= 3) x")[0][0] > 10
    # a job closed more than 7 days after it opened
    assert q("SELECT count(*) FROM fake_mes.synthetic_job_txn WHERE complete_ts - start_ts > interval '7 days'")[0][0] >= 1
    # jobs closed late (next day), batch-closed at shift end (clusters of identical completes)
    assert q("SELECT count(*) FROM fake_mes.synthetic_job_txn WHERE complete_ts - start_ts > interval '12 hours'")[0][0] > 3
    assert q("SELECT count(*) FROM (SELECT 1 FROM fake_mes.synthetic_job_txn WHERE station_code='SYN-LCH-2' "
             "GROUP BY operator_code, complete_ts HAVING count(*) >= 2) x")[0][0] > 10
    # overlapping jobs by one operator at Machining
    assert q("SELECT count(*) FROM fake_mes.synthetic_job_txn a JOIN fake_mes.synthetic_job_txn b "
             "ON a.operator_code=b.operator_code AND a.txn_id<b.txn_id AND a.station_code='SYN-MCH-2' "
             "AND a.start_ts < b.complete_ts AND b.start_ts < a.complete_ts")[0][0] > 10
    # temp badges not on the roster, and open jobs
    assert q("SELECT count(DISTINCT operator_code) FROM fake_mes.synthetic_job_txn WHERE operator_code LIKE '%T-90%'")[0][0] >= 2
    assert q("SELECT count(*) FROM fake_mes.synthetic_job_txn WHERE complete_ts IS NULL")[0][0] >= 1
