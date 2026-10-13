"""My Shift (design Component 6, D5, D6, D14, D23; eng re-review D4, D5), the lead's home, and the print
sheet that replaces the sticky note (design D20, eng re-review D6).

My Shift, top to bottom:
- the live notice ("New data from 2:45 PM · Refresh", eng re-review D5) in its own container, outside the
  table and any form; Refresh swaps only #shift-table and never a region holding an open dispute form;
- "My disputes": the user's disputes in this area and shift (open, or raised or decided in the last 7 days),
  grouped by submission, each Open / Confirmed / Rejected with the supervisor's note (D14);
- #shift-table: "worth a conversation" (up to 3 names per green station-part group, D5), then the table
  grouped by station and part, rows A-Z (D6), with "This looks wrong" (the job list and dispute form, D14)
  and "N jobs excluded" (D23) per row as the matrix allows.

Opens on the lead's current shift, live (D21). The D10 states: start of shift with zero jobs closed, and
no shift scheduled today.
"""

from datetime import timedelta
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from accounts import scope as role_scope
from accounts.decorators import area_scope_required, operator_bearing, render_out_of_scope
from accounts.policy import OperatorScope, page_context
from core.models import Area, Job, OperatorAlias, Shift
from disputes import service as disputes
from disputes.models import Dispute
from metrics import api
from metrics.api import Scope
from ops import freshness
from web import windows

from .common import announce, area_hub, area_lines, is_lead, station_blocks

MY_SHIFT_VIEW = "my_shift"
# DEVIATION: the doc does not say how long "My disputes" keeps a dispute. It shows the user's disputes in this
# area and shift that are open, or were raised or decided in the last 7 days.
MY_DISPUTES_DAYS = 7
REFRESH_TARGET = "#shift-table"


# --------------------------------------------------------------------------- shift choice


def default_shift(user, area):
    """The shift My Shift opens on when none is named: a lead's own; otherwise the shift running now
    in the area, else the last completed one, else the first the user may view."""
    if is_lead(user):
        return user.profile.shift
    hub = area_hub(area)
    allowed = role_scope.visible_shifts(user, area)
    if hub is not None:
        w = windows.current_window(hub)
        if w is not None and allowed.filter(pk=w.shift.pk).exists():
            return w.shift
        found = windows.last_completed_shift(hub)
        if found is not None and allowed.filter(pk=found[1].pk).exists():
            return found[1]
    return allowed.order_by("start_time").first()


def my_shift_url(area, shift, **params):
    q = {"shift_id": shift.pk, **{k: v for k, v in params.items() if v is not None}}
    return f"{reverse('my_shift', kwargs={'area_id': area.pk})}?{urlencode(q)}"


@login_required
def my_shift_home(request):
    """/shift/: the user's own My Shift (a lead: their area and shift)."""
    assignment = role_scope.lead_assignment(request.user)
    if assignment is not None:
        area, shift = assignment
        return redirect(my_shift_url(area, shift))
    if is_lead(request.user) or not role_scope.can_see_operator_metrics(request.user):
        return redirect("home")
    area = role_scope.own_area(request.user)
    shift = default_shift(request.user, area) if area is not None else None
    if area is None or shift is None:
        return redirect("home")
    return redirect(my_shift_url(area, shift))


def _shift_or_none(request):
    raw = request.GET.get("shift_id") or request.POST.get("shift_id") or ""
    return Shift.objects.select_related("hub").filter(pk=int(raw)).first() if raw.isdigit() else None


# --------------------------------------------------------------------------- data


def _my_disputes(user, area, shift):
    """[[Dispute]] grouped by submission, newest submission first."""
    since = windows.now() - timedelta(days=MY_DISPUTES_DAYS)
    rows = (Dispute.objects.filter(raised_by=user, job__station__line__area=area, job__shift=shift)
            .filter(Q(status=Dispute.OPEN) | Q(raised_at__gte=since) | Q(decided_at__gte=since))
            .select_related("job__station", "job__part", "job__alias__operator", "decided_by")
            .order_by("-raised_at", "pk"))
    subs = {}
    for d in rows:
        subs.setdefault(d.submission_id, []).append(d)
    return list(subs.values())


def _page_scope(area, shift, window, mine=()):
    """The page's operator scope: this area and shift, the window's dates, widened to cover the jobs
    in "My disputes" so their operators letter on the same page token."""
    dates = [window.date_from, window.date_to] + [d.job.production_date for sub in mine for d in sub
                                                  if d.job.production_date]
    return OperatorScope(area_ids=[area.pk], shift_ids=[shift.pk], date_from=min(dates), date_to=max(dates))


def _body(request, area, shift, window, state):
    """What #shift-table shows (shared by the page, the Refresh fragment and the print sheet)."""
    hub = shift.hub
    no_shift = None
    if window.is_default and not state.scheduled_today:
        last = state.last_date
        no_shift = {
            "last_shift_url": my_shift_url(area, shift, date=last.isoformat()) if last else "",
            "fourteen_day_url": (f"{reverse('line_health')}?"
                                 f"{urlencode({'date': (last or window.date_to).isoformat(), 'days': 14, 'shift_id': shift.pk})}"),
        }
        return {"no_shift": no_shift, "blocks": [], "conversation": [], "start": None}
    blocks = station_blocks(area, window.metrics)
    conversation = api.worth_a_conversation(Scope.for_area(area), window.metrics) if blocks else []
    start = None
    # DEVIATION: "a link to the last shift's sheet" is read as the previous run of the SAME shift (a lead
    # may only view their own shift), not whichever shift ran just before.
    if window.live:
        closed = Job.objects.filter(station__line__area=area, shift=shift, production_date=window.date_to,
                                    completed_at__isnull=False).exists()
        if not closed:
            start = {
                "shift_name": shift.name,
                "started": freshness.display_time(state.started_at, tz=hub.plant_timezone) if state.started_at else "",
                "last_sheet_url": (f"{reverse('print_sheet', kwargs={'area_id': area.pk})}?"
                                   f"{urlencode({'shift_id': shift.pk, 'date': state.last_date.isoformat()})}"
                                   if state.last_date else ""),
            }
    return {"no_shift": None, "blocks": blocks, "conversation": conversation, "start": start}


def _refresh_url(area, window):
    return f"{reverse('my_shift_table', kwargs={'area_id': area.pk})}?{window.query()}"


# --------------------------------------------------------------------------- views


@operator_bearing
@area_scope_required("area_id", shift_param="shift_id")
def my_shift(request, area_id):
    area = get_object_or_404(Area, pk=area_id)
    shift = _shift_or_none(request)
    if shift is None:  # supervisors and up may omit it (leads are refused by the decorator)
        shift = default_shift(request.user, area)
        if shift is None:
            return render(request, "web/home.html", {"message": "No shift is set up for this area yet."})
        return redirect(my_shift_url(area, shift, date=request.GET.get("date") or None))
    window, state = windows.my_shift_window(request, shift)
    mine = _my_disputes(request.user, area, shift)
    ctx = page_context(request, _page_scope(area, shift, window, mine))
    body = _body(request, area, shift, window, state)
    picker = windows.picker(request, window, reverse("my_shift", kwargs={"area_id": area.pk}), area=area,
                            allow_all_shifts=False)
    return render(request, "web/my_shift.html", {
        "takt_page": ctx, "area": area, "shift": shift, "window": window, "hub": shift.hub, "picker": picker,
        "my_disputes": mine, "refresh_url": _refresh_url(area, window), "refresh_target": REFRESH_TARGET,
        "print_url": f"{reverse('print_sheet', kwargs={'area_id': area.pk})}?{window.query()}",
        "nav_active": "my_shift", **body,
    })


@operator_bearing
@area_scope_required("area_id", shift_param="shift_id")
def my_shift_table(request, area_id):
    """The fragment the live notice's Refresh swaps in (#shift-table), plus the out-of-band poller reset
    (eng re-review D5). Letters come from the page's token."""
    area = get_object_or_404(Area, pk=area_id)
    shift = _shift_or_none(request)
    if shift is None:
        return render_out_of_scope(request, reason="shift")
    window, state = windows.my_shift_window(request, shift)
    ctx = page_context(request, _page_scope(area, shift, window), parents=(MY_SHIFT_VIEW,))
    body = _body(request, area, shift, window, state)
    return render(request, "web/partials/shift_table_fragment.html", {
        "takt_page": ctx, "area": area, "shift": shift, "window": window, "hub": shift.hub,
        "refresh_url": _refresh_url(area, window), "refresh_target": REFRESH_TARGET, **body,
    })


def _job_items(area, alias, window):
    """The person's jobs in the window at this area's lines (D14), each with `disputable`/`open_dispute`."""
    items = api.job_list(Scope(lines=tuple(area_lines(area)), alias=alias), window.metrics)
    open_ids = set(Dispute.objects.filter(job__in=[i.job for i in items], status=Dispute.OPEN)
                   .values_list("job_id", flat=True))
    for i in items:
        i.open_dispute = i.job.pk in open_ids
        i.disputable = (not i.is_open) and (not i.excluded) and not i.open_dispute
    return items


def _alias(raw):
    return OperatorAlias.objects.select_related("operator").filter(pk=int(raw)).first() if str(raw).isdigit() else None


def _job_list_response(request, ctx, area, shift, window, alias, items, error="", status=200, checked=()):
    return render(request, "web/partials/job_list.html", {
        "takt_page": ctx, "area": area, "shift": shift, "window": window, "alias": alias, "items": items,
        "reasons": Dispute.REASON_CHOICES, "error": error, "checked": set(checked),
        "any_disputable": any(i.disputable for i in items),
        "posted_reason": request.POST.get("reason", ""), "posted_text": request.POST.get("reason_text", ""),
    }, status=status)


@operator_bearing
@area_scope_required("area_id", shift_param="shift_id")
def my_shift_jobs(request, area_id, alias_id):
    """"This looks wrong" / "N jobs excluded": the operator's job list for the shift, inline (D14, D23).
    Excluded jobs stay visible, struck through, with the reason, who confirmed it and when."""
    area = get_object_or_404(Area, pk=area_id)
    shift = _shift_or_none(request)
    alias = _alias(alias_id)
    if shift is None or alias is None:
        return render_out_of_scope(request)
    window, _ = windows.my_shift_window(request, shift)
    ctx = page_context(request, _page_scope(area, shift, window), parents=(MY_SHIFT_VIEW,))
    return _job_list_response(request, ctx, area, shift, window, alias, _job_items(area, alias, window))


@operator_bearing
@area_scope_required("area_id", shift_param="shift_id")
@require_POST
def my_shift_dispute(request, area_id):
    """Submit a dispute: the ticked jobs and one reason -> one Dispute row per job (eng re-review D4).

    DEVIATION: the doc has the lead raise disputes; it does not forbid the supervisor, ops manager or builder
    who opens the same My Shift. Anyone who may view this shift may raise one; deciding stays with the
    builder, ops manager and supervisors (the queue), and leads never decide."""
    area = get_object_or_404(Area, pk=area_id)
    shift = _shift_or_none(request)
    alias = _alias(request.POST.get("alias_id", ""))
    if shift is None or alias is None:
        return render_out_of_scope(request)
    window, _ = windows.my_shift_window(request, shift)
    # page_context before any write: a rejected token in letter mode answers HX-Refresh, nothing saved.
    ctx = page_context(request, _page_scope(area, shift, window), parents=(MY_SHIFT_VIEW,))
    items = _job_items(area, alias, window)
    allowed = {i.job.pk: i.job for i in items}
    ticked = [int(x) for x in request.POST.getlist("job") if str(x).isdigit()]
    try:
        if any(pk not in allowed for pk in ticked):
            raise disputes.DisputeError("Tick jobs from this list.")
        rows = disputes.submit(request.user, [allowed[pk] for pk in ticked], request.POST.get("reason", ""),
                               request.POST.get("reason_text", ""))
    except disputes.DisputeError as e:
        return _job_list_response(request, ctx, area, shift, window, alias, items, error=str(e), checked=ticked)
    mine = _my_disputes(request.user, area, shift)
    response = render(request, "web/partials/dispute_submitted.html", {
        "takt_page": ctx, "count": len(rows), "my_disputes": mine, "oob": True,
    })
    return announce(response, "Dispute submitted")


@operator_bearing
@area_scope_required("area_id", shift_param="shift_id")
def print_sheet(request, area_id):
    """The printed shift sheet (design D20): the sticky-note replacement. Operator jobs and quantity only
    where the matrix allows them, station and part totals, a blank Correction column; no rates, medians,
    comparisons or "worth a conversation". Header from {% print_header hub %} only (eng re-review D6)."""
    area = get_object_or_404(Area, pk=area_id)
    shift = _shift_or_none(request)
    if shift is None:
        shift = default_shift(request.user, area)
        if shift is None:
            return render_out_of_scope(request, reason="shift")
        q = {"shift_id": shift.pk, **({"date": request.GET["date"]} if request.GET.get("date") else {})}
        return redirect(f"{reverse('print_sheet', kwargs={'area_id': area.pk})}?{urlencode(q)}")
    window, _ = windows.my_shift_window(request, shift)
    ctx = page_context(request, _page_scope(area, shift, window))
    lines = area_lines(area)
    blocks = []
    for st, groups in station_blocks(area, window.metrics):
        shown = [g for g in groups if g.cell.on_print_sheet]
        totals = (sum(g.jobs or 0 for g in shown), sum(g.quantity or 0 for g in shown)) if shown else None
        blocks.append({"station": st, "groups": groups, "totals": totals})
    return render(request, "web/print_sheet.html", {
        "takt_page": ctx, "area": area, "shift": shift, "window": window, "hub": shift.hub, "lines": lines,
        "blocks": blocks, "back_url": my_shift_url(area, shift, date=window.date_to.isoformat()),
        "nav_active": "my_shift",
    })


__all__ = ["my_shift_home", "my_shift", "my_shift_table", "my_shift_jobs", "my_shift_dispute", "print_sheet",
           "default_shift"]
