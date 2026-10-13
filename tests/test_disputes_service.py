"""E-T3: per-job Dispute rows sharing a submission_id; per-job decisions plus "apply to all"
(design Component 8, eng re-review D4). Confirm excludes exactly the decided jobs."""

from datetime import date, timedelta

import pytest

from accounts.models import Profile
from core import models as m
from disputes import service
from disputes.models import Dispute
from metrics import api
from metrics.api import Scope, Window
from tests import factories as f
from tests import golden as g
from tests.mes_fixtures import small_plant

D = date(2026, 9, 15)  # a Tuesday, well in the past


def _t(hub, hh, mm=0, d=D):
    return f.local(hub, d.year, d.month, d.day, hh, mm)


@pytest.fixture
def plant():
    p = small_plant()
    p["area"] = p["line"].area
    p["op"] = f.alias(f.operator("Quillon Dartmoor"), p["hub"])
    p["jobs"] = [g.job(p["s2"], p["part"], p["op"], _t(p["hub"], 8 + i), qty=10) for i in range(3)]
    p["lead"] = f.user("lead", Profile.LEAD, areas=[p["area"]], shift_obj=p["first"])
    p["sup"] = f.user("sup", Profile.SUPERVISOR, areas=[p["area"]])
    p["sup"].first_name, p["sup"].last_name = "Jo", "Ortiz"
    p["sup"].save()
    return p


def test_three_job_submission_two_confirmed_one_rejected(plant):
    jobs = plant["jobs"]
    rows = service.submit(plant["lead"], jobs, Dispute.WRONG_QUANTITY)
    assert len(rows) == 3
    assert len({r.submission_id for r in rows}) == 1
    assert {r.job_id for r in rows} == {j.pk for j in jobs}
    assert all(r.status == Dispute.OPEN and r.reason == Dispute.WRONG_QUANTITY for r in rows)

    now = _t(plant["hub"], 16)
    service.decide(rows[0], plant["sup"], service.CONFIRM, "Traveler says 8, not 10.", now=now)
    service.decide(rows[1], plant["sup"], service.CONFIRM, "Double scan.", affects_counts=False, now=now)
    service.decide(rows[2], plant["sup"], service.REJECT, "Bin count matches the MES.", now=now)

    for r in rows:
        r.refresh_from_db()
    assert [r.status for r in rows] == [Dispute.CONFIRMED, Dispute.CONFIRMED, Dispute.REJECTED]
    assert [r.note for r in rows] == ["Traveler says 8, not 10.", "Double scan.", "Bin count matches the MES."]
    assert all(r.decided_by == plant["sup"] and r.decided_at == now for r in rows)

    j0, j1, j2 = (m.Job.objects.get(pk=j.pk) for j in jobs)
    # confirmed: excluded; out of counts only when the count itself was wrong
    assert (j0.excluded, j0.excluded_from_counts, j0.exclusion_reason) == (True, True, "wrong quantity")
    assert (j0.excluded_by, j0.excluded_at) == (plant["sup"], now)
    assert (j1.excluded, j1.excluded_from_counts) == (True, False)
    # rejected: untouched
    assert (j2.excluded, j2.excluded_from_counts, j2.excluded_by) == (False, False, None)

    items = {i.job.pk: i for i in api.job_list(Scope(alias=plant["op"]), Window.for_shift("1st", D))}
    assert (items[j0.pk].excluded, items[j0.pk].counted) == (True, False)
    assert (items[j1.pk].excluded, items[j1.pk].counted, items[j1.pk].in_rates) == (True, True, False)
    assert (items[j2.pk].excluded, items[j2.pk].counted, items[j2.pk].in_rates) == (False, True, True)


def test_apply_to_all_decides_every_open_job_in_the_submission(plant):
    rows = service.submit(plant["lead"], plant["jobs"], Dispute.NOT_MINE)
    service.decide(rows[2], plant["sup"], service.REJECT, "It was yours.", now=_t(plant["hub"], 15))
    decided = service.decide(rows[0], plant["sup"], service.CONFIRM, "Wrong badge scanned.", apply_to_all=True,
                             now=_t(plant["hub"], 16))
    assert {d.pk for d in decided} == {rows[0].pk, rows[1].pk}  # the rejected one stays rejected
    statuses = dict(Dispute.objects.values_list("pk", "status"))
    assert statuses == {rows[0].pk: Dispute.CONFIRMED, rows[1].pk: Dispute.CONFIRMED, rows[2].pk: Dispute.REJECTED}
    assert m.Job.objects.filter(excluded=True).count() == 2
    # "job not mine" is not a count dispute by default: the station's counts keep the jobs
    assert m.Job.objects.filter(excluded_from_counts=True).count() == 0


def test_apply_to_all_stays_inside_the_area(plant):
    other = small_plant(code="dies")
    other_area = f.area("Elsewhere")
    other["line"].area = other_area
    other["line"].save()
    far = g.job(other["s2"], other["part"], f.alias(None, other["hub"], "X-1"), _t(other["hub"], 9), qty=3)
    rows = service.submit(plant["lead"], plant["jobs"][:1] + [far], Dispute.NOT_MINE)
    decided = service.decide(rows[0], plant["sup"], service.REJECT, "No.", apply_to_all=True,
                             area=plant["area"], now=_t(plant["hub"], 16))
    assert [d.pk for d in decided] == [rows[0].pk]
    rows[1].refresh_from_db()
    assert rows[1].status == Dispute.OPEN


@pytest.mark.parametrize("decision", [service.CONFIRM, service.REJECT])
def test_confirm_and_reject_both_require_a_note(plant, decision):
    [row] = service.submit(plant["lead"], plant["jobs"][:1], Dispute.TIME_WRONG)
    for note in ("", "   "):
        with pytest.raises(service.DisputeError, match="A note is required"):
            service.decide(row, plant["sup"], decision, note)
    row.refresh_from_db()
    assert row.status == Dispute.OPEN and not m.Job.objects.get(pk=row.job_id).excluded


def test_submission_rules(plant):
    with pytest.raises(service.DisputeError):
        service.submit(plant["lead"], [], Dispute.WRONG_QUANTITY)
    with pytest.raises(service.DisputeError):
        service.submit(plant["lead"], plant["jobs"][:1], "made_up")
    with pytest.raises(service.DisputeError, match="Other"):
        service.submit(plant["lead"], plant["jobs"][:1], Dispute.OTHER, "  ")
    [row] = service.submit(plant["lead"], plant["jobs"][:1], Dispute.OTHER, "label torn")
    assert row.reason_text == "label torn"
    with pytest.raises(service.DisputeError, match="already disputed"):
        service.submit(plant["lead"], plant["jobs"][:2], Dispute.WRONG_QUANTITY)
    assert Dispute.objects.count() == 1  # all or nothing
    service.decide(row, plant["sup"], service.CONFIRM, "Torn label, recount 9.", now=_t(plant["hub"], 16))
    assert m.Job.objects.get(pk=row.job_id).exclusion_reason == "label torn"
    with pytest.raises(service.DisputeError, match="already been decided"):
        service.decide(row, plant["sup"], service.REJECT, "again")


def test_confirmed_count_dispute_revokes_count_trust(plant):
    """Component 4a: confirmed count disputes over 5% of a station's jobs (2 weeks) revoke trust at once."""
    hub, s2 = plant["hub"], plant["s2"]
    for i in range(10):  # 13 jobs in all; 1 bad = 7.7% > 5%
        g.job(s2, plant["part"], plant["op"], _t(hub, 11, i * 5, d=D - timedelta(days=1)), qty=5)
    [row] = service.submit(plant["lead"], plant["jobs"][:1], Dispute.WRONG_QUANTITY)
    service.decide(row, plant["sup"], service.CONFIRM, "Recounted.", now=_t(hub, 16))
    s2.refresh_from_db()
    assert not s2.count_trusted
    assert m.CountTrustChange.objects.filter(station=s2, action=m.CountTrustChange.AUTO_REVOKE).exists()


def test_rejected_or_non_count_confirm_never_revokes(plant):
    rows = service.submit(plant["lead"], plant["jobs"], Dispute.TIME_WRONG)
    service.decide(rows[0], plant["sup"], service.CONFIRM, "Clock was off.", now=_t(plant["hub"], 16))
    service.decide(rows[1], plant["sup"], service.REJECT, "Fine.", now=_t(plant["hub"], 16))
    plant["s2"].refresh_from_db()
    assert plant["s2"].count_trusted


def test_age_in_shifts_and_overdue(plant):
    """Overdue once more than 2 shifts have started since the dispute was raised (eng review D6)."""
    hub = plant["hub"]
    raised = _t(hub, 10)  # during 1st shift
    assert service.age_in_shifts(raised, hub, _t(hub, 14)) == 0
    assert service.age_in_shifts(raised, hub, _t(hub, 15, 1)) == 1  # 2nd started
    assert service.age_in_shifts(raised, hub, _t(hub, 23, 30)) == 2  # 3rd started
    assert not service.is_overdue(2)
    nxt = D + timedelta(days=1)
    assert service.age_in_shifts(raised, hub, _t(hub, 7, 5, d=nxt)) == 3  # next 1st started
    assert service.is_overdue(3)


def test_queue_puts_overdue_first_then_oldest(plant):
    hub = plant["hub"]
    rows_old = service.submit(plant["lead"], plant["jobs"][:2], Dispute.WRONG_QUANTITY)
    rows_new = service.submit(plant["lead"], plant["jobs"][2:], Dispute.NOT_MINE)
    Dispute.objects.filter(pk__in=[r.pk for r in rows_old]).update(raised_at=_t(hub, 9))
    Dispute.objects.filter(pk__in=[r.pk for r in rows_new]).update(raised_at=_t(hub, 9, d=D + timedelta(days=1)))
    now = _t(hub, 16, d=D + timedelta(days=1))
    subs = service.queue(service.open_disputes([plant["area"]]), now)
    assert [s.submission_id for s in subs] == [rows_old[0].submission_id, rows_new[0].submission_id]
    assert subs[0].overdue and not subs[1].overdue
    summary = service.open_summary([plant["area"]], now)
    assert (summary.count, summary.overdue, summary.oldest_age) == (3, 2, 4)
