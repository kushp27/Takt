"""ops.freshness: the 30-minute stale rule (eng review D5) and the print header (eng re-review D6)."""

from datetime import date, time, timedelta

from core.models import SiteSettings
from ops import freshness
from tests import factories as f


def _hub(code="cores", name=None, **kw):
    h = f.hub(code, tz="America/Chicago")
    if name:
        h.name = name
    for k, v in kw.items():
        setattr(h, k, v)
    h.save()
    return h


def _first_shift_hub(**kw):
    h = _hub(**kw)
    f.shift(h, "1st", time(7), time(15))
    return h


def test_quiet_2959_is_fresh_and_3000_is_stale():
    h = _first_shift_hub()
    last_tx = f.local(h, 2026, 10, 12, 8, 0)
    h.last_new_transaction_at = last_tx
    h.last_successful_ingest_at = last_tx
    h.save()
    fresh = freshness.hub_freshness(h, last_tx + timedelta(minutes=29, seconds=59))
    stale = freshness.hub_freshness(h, last_tx + timedelta(minutes=30))
    assert fresh.in_scheduled_shift and not fresh.stale and fresh.stale_since is None
    assert fresh.minutes_quiet == round(29 + 59 / 60, 2)
    assert stale.stale and stale.stale_since == last_tx + timedelta(minutes=30)
    assert stale.minutes_quiet == 30


def test_quiet_time_starts_at_shift_start_so_a_new_shift_is_not_instantly_stale():
    h = _first_shift_hub()
    yesterday = f.local(h, 2026, 10, 11, 14, 50)
    h.last_new_transaction_at = yesterday
    h.last_successful_ingest_at = f.local(h, 2026, 10, 12, 6, 55)
    h.save()
    shift_start = f.local(h, 2026, 10, 12, 7, 0)
    assert not freshness.hub_freshness(h, shift_start).stale
    assert not freshness.hub_freshness(h, shift_start + timedelta(minutes=29, seconds=59)).stale
    at_30 = freshness.hub_freshness(h, shift_start + timedelta(minutes=30))
    assert at_30.stale and at_30.stale_since == shift_start + timedelta(minutes=30)


def test_outside_scheduled_shift_is_never_stale():
    h = _first_shift_hub()
    h.last_new_transaction_at = f.local(h, 2026, 10, 1, 8, 0)
    h.last_successful_ingest_at = f.local(h, 2026, 10, 1, 8, 0)
    h.save()
    r = freshness.hub_freshness(h, f.local(h, 2026, 10, 12, 16, 0))
    assert not r.in_scheduled_shift and not r.stale and r.minutes_quiet is None


def test_non_working_day_is_outside_a_scheduled_shift():
    from core.models import NonWorkingDay

    h = _first_shift_hub()
    NonWorkingDay.objects.create(hub=h, date=f.TODAY)
    r = freshness.hub_freshness(h, f.local(h, 2026, 10, 12, 10, 0))
    assert not r.in_scheduled_shift and not r.stale


def test_third_shift_across_midnight_counts_as_scheduled():
    h = _hub()
    f.shift(h, "3rd", time(23), time(7))
    h.last_successful_ingest_at = f.local(h, 2026, 10, 13, 0, 55)
    h.last_new_transaction_at = f.local(h, 2026, 10, 13, 0, 20)
    h.save()
    r = freshness.hub_freshness(h, f.local(h, 2026, 10, 13, 1, 0))
    assert r.in_scheduled_shift and r.window.shift.name == "3rd"
    assert r.stale and r.stale_since == f.local(h, 2026, 10, 13, 0, 50)
    assert not freshness.hub_freshness(h, f.local(h, 2026, 10, 13, 0, 49)).stale


def test_never_ingested_hub_is_stale_only_at_block_start_plus_threshold():
    h = _first_shift_hub()
    assert not freshness.hub_freshness(h, f.local(h, 2026, 10, 12, 7, 29)).stale
    at_30 = freshness.hub_freshness(h, f.local(h, 2026, 10, 12, 7, 30))
    assert at_30.stale and at_30.stale_since == f.local(h, 2026, 10, 12, 7, 30)
    assert at_30.stopped_at is None
    assert not freshness.hub_freshness(h, f.local(h, 2026, 10, 12, 20, 0)).stale


def test_shift_change_does_not_reset_the_quiet_clock():
    h = _hub()
    f.shift(h, "1st", time(7), time(15))
    f.shift(h, "2nd", time(15), time(23))
    h.last_new_transaction_at = h.last_successful_ingest_at = f.local(h, 2026, 10, 12, 14, 20)
    h.save()
    r = freshness.hub_freshness(h, f.local(h, 2026, 10, 12, 15, 5))
    assert r.window.shift.name == "2nd"
    assert r.stale and r.stale_since == f.local(h, 2026, 10, 12, 14, 50)
    assert r.minutes_quiet == 45


def test_third_shift_into_first_shift_is_one_contiguous_block():
    h = _hub()
    f.shift(h, "3rd", time(23), time(7))
    f.shift(h, "1st", time(7), time(15))
    h.last_new_transaction_at = h.last_successful_ingest_at = f.local(h, 2026, 10, 13, 6, 45)
    h.save()
    r = freshness.hub_freshness(h, f.local(h, 2026, 10, 13, 7, 15))
    assert r.window.shift.name == "1st" and r.stale  # 30 quiet minutes across 07:00
    # Never-transacted hub: the block started at 23:00 the night before.
    h.last_new_transaction_at = None
    h.save()
    r = freshness.hub_freshness(h, f.local(h, 2026, 10, 13, 7, 0))
    assert r.quiet_from == f.local(h, 2026, 10, 12, 23, 0)


def test_gap_between_shifts_restarts_the_count():
    h = _hub()
    f.shift(h, "1st", time(7), time(15))
    f.shift(h, "2nd", time(15, 30), time(23))
    h.last_new_transaction_at = h.last_successful_ingest_at = f.local(h, 2026, 10, 12, 14, 0)
    h.save()
    assert freshness.hub_freshness(h, f.local(h, 2026, 10, 12, 14, 59)).stale
    fresh = freshness.hub_freshness(h, f.local(h, 2026, 10, 12, 15, 59))
    assert not fresh.stale and fresh.quiet_from == f.local(h, 2026, 10, 12, 15, 30)
    assert freshness.hub_freshness(h, f.local(h, 2026, 10, 12, 16, 0)).stale


def test_non_working_day_breaks_the_block():
    from core.models import NonWorkingDay

    h = _hub()
    f.shift(h, "3rd", time(23), time(7))
    f.shift(h, "1st", time(7), time(15))
    NonWorkingDay.objects.create(hub=h, date=date(2026, 10, 12))  # no 3rd shift starting the 12th
    h.last_new_transaction_at = h.last_successful_ingest_at = f.local(h, 2026, 10, 12, 14, 0)
    h.save()
    r = freshness.hub_freshness(h, f.local(h, 2026, 10, 13, 7, 10))
    assert r.quiet_from == f.local(h, 2026, 10, 13, 7, 0) and not r.stale


def test_display_strings_use_the_hubs_plant_timezone():
    h = _hub(plant_timezone="America/New_York")
    f.shift(h, "1st", time(7), time(15))
    h.last_successful_ingest_at = f.local(h, 2026, 10, 12, 9, 0)
    h.save()
    r = freshness.hub_freshness(h, f.local(h, 2026, 10, 12, 9, 5))
    assert r.data_as_of_display == "9:00 AM"  # not 8:00 AM Chicago


def test_threshold_comes_from_site_settings():
    s = SiteSettings.load()
    s.stale_after_minutes = 45
    s.save()
    h = _first_shift_hub()
    h.last_new_transaction_at = h.last_successful_ingest_at = f.local(h, 2026, 10, 12, 8, 0)
    h.save()
    snap = freshness.snapshot(f.local(h, 2026, 10, 12, 8, 44))
    assert not snap.any_stale
    assert freshness.snapshot(f.local(h, 2026, 10, 12, 8, 45)).any_stale


def test_snapshot_with_zero_hubs_is_fresh():
    snap = freshness.snapshot()
    assert snap.hubs == () and not snap.any_stale and snap.stale_hubs == []


def test_snapshot_orders_and_indexes_hubs():
    dies = _hub("dies", name="Dies Hub")
    cores = _hub("cores", name="Cores Hub")
    snap = freshness.snapshot(f.local(cores, 2026, 10, 12, 20, 0))
    assert [h.code for h in snap.hubs] == ["cores", "dies"]
    assert snap.get("dies").hub == dies and snap.get(cores).hub == cores


def test_display_time_uses_display_timezone_and_adds_date_for_other_days():
    h = _hub()
    now = f.local(h, 2026, 10, 12, 14, 41)
    assert freshness.display_time(f.local(h, 2026, 10, 12, 6, 40), now) == "6:40 AM"
    assert freshness.display_time(f.local(h, 2026, 10, 12, 14, 38), now) == "2:38 PM"
    assert freshness.display_time(f.local(h, 2026, 10, 12, 0, 5), now) == "12:05 AM"
    assert freshness.display_time(f.local(h, 2026, 10, 11, 22, 0), now) == "Oct 11, 10:00 PM"


def test_print_header_fresh():
    h = _first_shift_hub()
    h.last_successful_ingest_at = f.local(h, 2026, 10, 12, 14, 38)
    h.last_new_transaction_at = f.local(h, 2026, 10, 12, 14, 35)
    h.save()
    ph = freshness.print_header(h, f.local(h, 2026, 10, 12, 14, 41))
    assert ph.data_as_of == "Data as of 2:38 PM"
    assert ph.printed == "Printed 2:41 PM"
    assert not ph.stale and ph.stale_text == ""
    assert ph.lines == ["Data as of 2:38 PM", "Printed 2:41 PM"]


def test_print_header_stale_by_the_same_30_minute_rule():
    h = _first_shift_hub()
    h.last_successful_ingest_at = f.local(h, 2026, 10, 12, 14, 38)  # ingest still running, no new jobs
    h.last_new_transaction_at = f.local(h, 2026, 10, 12, 6, 40)
    h.save()
    ph = freshness.print_header("cores", f.local(h, 2026, 10, 12, 14, 41))
    assert ph.stale and ph.stale_text == "STALE: data stopped at 6:40 AM"
    assert ph.lines[0] == "STALE: data stopped at 6:40 AM"
    assert ph.data_as_of == "Data as of 2:38 PM"


def test_print_header_never_ingested():
    h = _first_shift_hub()
    ph = freshness.print_header(h, f.local(h, 2026, 10, 12, 9, 0))
    assert ph.stale_text == "STALE: no data received yet"
    assert ph.data_as_of == "Data as of —, no data received yet"
