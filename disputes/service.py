"""Dispute submission and decisions (design Component 8, design review D8/D14/D23, eng re-review D4).

- submit(): a lead ticks one or more jobs and picks a reason; one Dispute row per job, all sharing a
  submission_id and the reason.
- decide(): the supervisor decides each job separately, with "apply to all in this submission".
  Confirm and Reject both require a note, which is shown back to the lead.
  A confirm excludes the job: job.excluded, plus job.excluded_from_counts when the count itself is
  wrong (affects_counts), with excluded_by / excluded_at / exclusion_reason. A confirmed count
  dispute re-checks count trust at once (trust.count_trust.auto_revoke) instead of at the nightly run.
- age_in_shifts() / is_overdue(): disputes older than 2 shifts show red, with the word "Overdue"
  (eng review D6). In-app only, no email.

Callers check scope (area, shift) before calling; this module only enforces the dispute rules.
"""

import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.models import Job
from core.shifts import ShiftStamper, local_date
from trust.count_trust import auto_revoke

from .models import Dispute

# DEVIATION: "older than 2 shifts" is not defined further. Age = the number of scheduled shifts of the job's
# hub that have STARTED since the dispute was raised; overdue when that is more than 2 (raised during 1st
# shift: the 2nd and 3rd start, then overdue when the next 1st starts). Same rule for the strip and queue.
OVERDUE_AFTER_SHIFTS = 2  # eng review D6: "disputes older than 2 shifts show red"
AGE_LOOKBACK_DAYS = 60
CONFIRM, REJECT = "confirm", "reject"
NOTE_REQUIRED = "A note is required to confirm or reject."
# DEVIATION: the doc leaves "the count itself is wrong" to the supervisor. The Confirm form shows it as a
# checkbox, pre-ticked only for "wrong quantity"; the supervisor can change it per decision.
COUNT_REASONS = {Dispute.WRONG_QUANTITY}


class DisputeError(ValueError):
    """A submission or decision that breaks a rule; the message is shown to the user."""


# --------------------------------------------------------------------------- submit


def disputable(job):
    """A job can be disputed when it is completed, not already excluded, and has no open dispute."""
    return (job.completed_at is not None and not job.excluded and not job.excluded_from_counts
            and not job.disputes.filter(status=Dispute.OPEN).exists())


@transaction.atomic
def submit(user, jobs, reason, reason_text=""):
    """Create one Dispute per job, sharing a new submission_id. Returns the rows."""
    jobs = list(jobs)
    reason_text = (reason_text or "").strip()
    if not jobs:
        raise DisputeError("Tick at least one job.")
    if reason not in dict(Dispute.REASON_CHOICES):
        raise DisputeError("Pick a reason.")
    if reason == Dispute.OTHER and not reason_text:
        raise DisputeError('Say what looks wrong when the reason is "Other".')
    locked = list(Job.objects.select_for_update().filter(pk__in=[j.pk for j in jobs]).order_by("pk"))
    for job in locked:
        if not disputable(job):
            raise DisputeError("One of the ticked jobs is already disputed, excluded or still open.")
    sid = uuid.uuid4()
    return [Dispute.objects.create(job=job, submission_id=sid, reason=reason,
                                   reason_text=reason_text if reason == Dispute.OTHER else "", raised_by=user)
            for job in locked]


# --------------------------------------------------------------------------- decide


def exclusion_reason(dispute):
    """The words shown after "Excluded:" (design review D23), e.g. "wrong quantity": always the reason's
    label. Free text (reason_text, the note) stays on the Dispute, seen only by the raiser and the deciders,
    so it never becomes an identity channel on the job everyone with the shift can see."""
    return dispute.get_reason_display().lower()


def default_affects_counts(dispute):
    return dispute.reason in COUNT_REASONS


@transaction.atomic
def decide(dispute, user, decision, note, *, affects_counts=None, apply_to_all=False, now=None, area=None):
    """Confirm or reject `dispute` (and, with apply_to_all, every other OPEN dispute in its submission).

    affects_counts: on confirm, the count itself is wrong, so the job also leaves counts
    (default: the reason is "wrong quantity"). area: limit apply_to_all to this area's jobs (the queue's).
    Returns the decided Dispute rows.
    """
    now = now or timezone.now()
    note = (note or "").strip()
    if decision not in (CONFIRM, REJECT):
        raise DisputeError("Choose Confirm or Reject.")
    if not note:
        raise DisputeError(NOTE_REQUIRED)
    dispute = Dispute.objects.select_for_update().get(pk=dispute.pk)
    if dispute.status != Dispute.OPEN:
        raise DisputeError("This dispute has already been decided.")
    targets = [dispute]
    if apply_to_all:
        others = (Dispute.objects.select_for_update()
                  .filter(submission_id=dispute.submission_id, status=Dispute.OPEN).exclude(pk=dispute.pk))
        if area is not None:
            others = others.filter(job__station__line__area=area)
        targets += list(others.order_by("pk"))
    stations = OrderedDict()
    for d in targets:
        d.status = Dispute.CONFIRMED if decision == CONFIRM else Dispute.REJECTED
        d.decided_by = user
        d.decided_at = now
        d.note = note
        if decision == CONFIRM:
            d.affects_counts = default_affects_counts(d) if affects_counts is None else bool(affects_counts)
            job = Job.objects.select_for_update().select_related("station").get(pk=d.job_id)
            job.excluded = True
            job.excluded_from_counts = job.excluded_from_counts or d.affects_counts
            job.excluded_by = user
            job.excluded_at = now
            job.exclusion_reason = exclusion_reason(d)
            job.save(update_fields=["excluded", "excluded_from_counts", "excluded_by", "excluded_at",
                                    "exclusion_reason"])
            if d.affects_counts:
                stations[job.station.pk] = job.station
        else:
            d.affects_counts = False
        d.save(update_fields=["status", "decided_by", "decided_at", "note", "affects_counts"])
    if stations:
        # Component 4a: confirmed count disputes over 5% of a station's jobs (2 weeks) revoke count
        # trust; check now so the station leaves pace and operator rows at once.
        auto_revoke(stations=list(stations.values()), as_of=now)
    return targets


# --------------------------------------------------------------------------- age and the queue


def age_in_shifts(raised_at, hub, now=None, stamper=None):
    """How many scheduled shifts of `hub` have started since the dispute was raised, up to now."""
    now = now or timezone.now()
    stamper = stamper or ShiftStamper(hub)
    d = local_date(hub, raised_at) - timedelta(days=1)
    end = local_date(hub, now)
    if (end - d).days > AGE_LOOKBACK_DAYS:
        d = end - timedelta(days=AGE_LOOKBACK_DAYS)
    n = 0
    while d <= end:
        n += sum(1 for w in stamper.windows_on(d) if raised_at < w.start <= now)
        d += timedelta(days=1)
    return n


def is_overdue(age):
    return age > OVERDUE_AFTER_SHIFTS


@dataclass
class QueueItem:
    dispute: Dispute
    age: int  # in shifts

    @property
    def overdue(self):
        return is_overdue(self.age)


@dataclass
class QueueSubmission:
    submission_id: uuid.UUID
    items: list = field(default_factory=list)  # [QueueItem], oldest first

    @property
    def raised_at(self):
        return min(i.dispute.raised_at for i in self.items)

    @property
    def overdue(self):
        return any(i.overdue for i in self.items if i.dispute.status == Dispute.OPEN)

    @property
    def age(self):
        return max(i.age for i in self.items)

    @property
    def first(self):
        return self.items[0].dispute

    @property
    def open_count(self):
        return sum(1 for i in self.items if i.dispute.status == Dispute.OPEN)


def open_disputes(areas):
    return (Dispute.objects.filter(status=Dispute.OPEN, job__station__line__area__in=areas)
            .select_related("job__station__line__hub", "job__part", "job__alias__operator", "raised_by"))


def queue(disputes, now=None):
    """Group disputes by submission; overdue submissions first, then oldest first (design review D8)."""
    now = now or timezone.now()
    stampers = {}
    subs = OrderedDict()
    for d in sorted(disputes, key=lambda d: (d.raised_at, d.pk)):
        hub = d.job.station.line.hub
        stamper = stampers.setdefault(hub.pk, ShiftStamper(hub))
        item = QueueItem(d, age_in_shifts(d.raised_at, hub, now, stamper))
        subs.setdefault(d.submission_id, QueueSubmission(d.submission_id)).items.append(item)
    return sorted(subs.values(), key=lambda s: (not s.overdue, s.raised_at))


@dataclass(frozen=True)
class OpenSummary:
    """The Area view's open-disputes strip (design review D7)."""

    count: int
    overdue: int
    oldest_age: int | None  # in shifts


def open_summary(areas, now=None):
    items = [i for s in queue(open_disputes(areas), now) for i in s.items]
    return OpenSummary(len(items), sum(1 for i in items if i.overdue), max((i.age for i in items), default=None))
