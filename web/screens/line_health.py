"""Line Health (design Component 6, design review D3, D4, D22), the ops manager's home, and its
drill-downs: line -> stations -> operators at one station and part.

The Line Health page and the station drill-down show no operator identity, so they are not
@operator_bearing (the identity sweep probes them as unmarked URLs). The Line Health page still
mints a page token ({% page_hx_vals %}) so the operator drill-down, which is operator-bearing,
letters operators consistently within the page (eng re-review D3).
"""

from django.shortcuts import render
from django.urls import reverse

from accounts import scope as role_scope
from accounts.decorators import hx_refresh, operator_bearing, render_out_of_scope, role_required
from accounts.models import Profile
from accounts.policy import OperatorScope, PageTokenRejected, page_context
from core.models import Line, Part, Station
from metrics import api
from web import windows

from .common import is_lead, unique_hubs

METRIC_ROLES = (Profile.OPS_MANAGER, Profile.SUPERVISOR, Profile.LEAD)


def _scope(window, area_ids=None):
    return OperatorScope(area_ids=area_ids, shift_ids=[window.shift.pk] if window.shift else None,
                         date_from=window.date_from, date_to=window.date_to)


@role_required(*METRIC_ROLES)
def line_health(request):
    """One row per line the user can see: status word + sentence first, evidence below (D3)."""
    lines = list(role_scope.visible_lines(request.user).select_related("hub", "area").order_by("name"))
    try:
        window = windows.line_health_window(request, unique_hubs(lines))
    except windows.OutOfScopeShift:
        return render_out_of_scope(request, reason="shift")
    try:
        ctx = page_context(request, _scope(window))
    except PageTokenRejected:
        return hx_refresh()  # the page fetched by htmx (no token to reuse): reload it, mint a new token
    rows = api.line_health(lines, window.date_from, window.date_to, shift=window.shift) if lines else []
    picker = windows.picker(request, window, reverse("line_health"), allow_all_shifts=not is_lead(request.user),
                            show_days=True)
    return render(request, "web/line_health.html", {
        "takt_page": ctx, "rows": rows, "window": window, "picker": picker, "nav_active": "line_health",
    })


@role_required(*METRIC_ROLES)
def line_stations(request, line_id):
    """Drill-down partial: the line's stations in sequence, per-part rows, trust badges (same layout)."""
    line = Line.objects.select_related("hub", "area").filter(pk=line_id).first()
    if line is None or not role_scope.can_view_area(request.user, line.area_id):
        return render_out_of_scope(request)
    try:
        window = windows.line_health_window(request, [line.hub])
    except windows.OutOfScopeShift:
        return render_out_of_scope(request, reason="shift")
    rows = api.station_rows(line, window.metrics)
    return render(request, "web/partials/stations.html", {"line": line, "rows": rows, "window": window})


@operator_bearing
def station_operators(request, station_id):
    """Drill-down partial: operators at one station (and part), same station and part only (D6).

    Letters come from the parent page's token (Line Health or Area). A lead sees only their shift."""
    station = Station.objects.select_related("line__hub", "line__area").filter(pk=station_id).first()
    if station is None or not role_scope.can_view_area(request.user, station.line.area_id):
        return render_out_of_scope(request)
    try:
        window = windows.line_health_window(request, [station.line.hub])
    except windows.OutOfScopeShift:
        return render_out_of_scope(request, reason="shift")
    part = None
    part_id = request.GET.get("part_id", "")
    if part_id:
        if not part_id.isdigit():
            return render_out_of_scope(request)
        part = Part.objects.filter(pk=int(part_id)).first()
    ctx = page_context(request, _scope(window, [station.line.area_id]), parents=("line_health", "area"))
    groups = api.operator_groups(station, part, window.metrics)
    return render(request, "web/partials/operators.html", {
        "takt_page": ctx, "station": station, "part": part, "groups": groups, "window": window,
        "lead_view": is_lead(request.user),
    })
