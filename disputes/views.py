"""Dispute queue (design review D8, eng review D6, eng re-review D4): the supervisor's in-app queue.

- A table sorted oldest first; overdue submissions (older than 2 shifts) sit at the top with the word
  "Overdue" in red, never colour alone.
- Columns: age in shifts, station and part, job ID and time, operator, raised by, reason.
- Rows are grouped by submission (one <tbody> each). Each row expands inline (htmx) to the job's MES
  values with Confirm and Reject; both require a note, which is shown back to the lead. The supervisor
  decides each job, or every open job in the submission with "Apply to all".
- In-app only: the nav badge and the Area strip count open disputes. No email.

Who: builder, ops manager and the area's supervisors decide. Leads raise disputes on My Shift and see
outcomes in "My disputes"; the queue refuses them (D13).
"""

from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST

from accounts.decorators import area_scope_required, operator_bearing, render_out_of_scope, role_required
from accounts.models import Profile
from accounts.policy import OperatorScope, page_context
from core.models import Area
from web import windows
from web.screens.common import announce, is_decider

from . import service
from .models import Dispute

DECIDERS = (Profile.OPS_MANAGER, Profile.SUPERVISOR)
QUEUE_VIEW = "dispute_queue"


def _scope(area, disputes, fallback):
    dates = [d.job.production_date for d in disputes if d.job.production_date] or [fallback]
    return OperatorScope(area_ids=[area.pk], date_from=min(dates), date_to=max(dates))


def _dispute_in_area(area, dispute_id):
    return (Dispute.objects.select_related("job__station__line__hub", "job__part", "job__alias__operator",
                                           "job__shift", "raised_by", "decided_by")
            .filter(pk=dispute_id, job__station__line__area=area).first())


@operator_bearing
@role_required(*DECIDERS)
@area_scope_required("area_id")
def dispute_queue(request, area_id):
    area = get_object_or_404(Area, pk=area_id)
    now = windows.now()
    open_rows = list(service.open_disputes([area]))
    ctx = page_context(request, _scope(area, open_rows, now.date()))
    return render(request, "disputes/queue.html", {
        "takt_page": ctx, "area": area, "subs": service.queue(open_rows, now), "nav_active": "disputes",
    })


@operator_bearing
@area_scope_required("area_id")
def dispute_detail(request, area_id, dispute_id):
    """Inline expand: the job's MES values and the Confirm / Reject form."""
    area = get_object_or_404(Area, pk=area_id)
    dispute = _dispute_in_area(area, dispute_id)
    if dispute is None:
        return render_out_of_scope(request)
    # The token check comes first so a partial without the queue's token never renders fresh letters
    # (HX-Refresh); then the role check (leads raise disputes, they don't decide them).
    ctx = page_context(request, _scope(area, [dispute], windows.now().date()), parents=(QUEUE_VIEW,))
    if not is_decider(request.user):
        return render_out_of_scope(request, reason="role")
    return _detail(request, ctx, area, dispute)


def _detail(request, ctx, area, dispute, error="", note="", status=200):
    others = (Dispute.objects.filter(submission_id=dispute.submission_id, status=Dispute.OPEN)
              .exclude(pk=dispute.pk).count())
    return render(request, "disputes/partials/detail.html", {
        "takt_page": ctx, "area": area, "d": dispute, "job": dispute.job, "others": others,
        "open_total": others + 1, "error": error, "note": note,
        "affects_default": service.default_affects_counts(dispute),
    }, status=status)


@operator_bearing
@area_scope_required("area_id")
@require_POST
def dispute_decide(request, area_id, dispute_id):
    """Confirm or Reject one job (or all open jobs in its submission). Both require a note."""
    area = get_object_or_404(Area, pk=area_id)
    dispute = _dispute_in_area(area, dispute_id)
    if dispute is None:
        return render_out_of_scope(request)
    now = windows.now()
    # page_context before any write (a rejected token in letter mode answers HX-Refresh, nothing saved).
    ctx = page_context(request, _scope(area, [dispute], now.date()), parents=(QUEUE_VIEW,))
    if not is_decider(request.user):
        return render_out_of_scope(request, reason="role")
    decision = request.POST.get("decision", "")
    note = request.POST.get("note", "")
    try:
        decided = service.decide(dispute, request.user, decision, note,
                                 affects_counts=request.POST.get("affects_counts") == "1",
                                 apply_to_all=request.POST.get("apply_to_all") == "1", now=now)
    except service.DisputeError as e:
        response = _detail(request, ctx, area, _dispute_in_area(area, dispute_id), error=str(e), note=note)
        response["HX-Retarget"] = f"#dispute-{dispute.pk}-detail"
        response["HX-Reswap"] = "innerHTML"
        return response
    rows = list(Dispute.objects.select_related("job__station__line__hub", "job__part", "job__alias__operator",
                                               "raised_by", "decided_by")
                .filter(submission_id=dispute.submission_id, job__station__line__area=area))
    [sub] = service.queue(rows, now)
    word = "confirmed" if decision == service.CONFIRM else "rejected"
    response = render(request, "disputes/partials/decided.html", {
        "takt_page": ctx, "area": area, "sub": sub, "decided_ids": {d.pk for d in decided},
    })
    n = len(decided)
    return announce(response, f"Dispute {word}" if n == 1 else f"{n} disputes {word}",
                    navcount=_nav_count(request.user))


def _nav_count(user):
    from web.context_processors import open_dispute_count

    return open_dispute_count(user)

