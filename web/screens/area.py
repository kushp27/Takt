"""Area (design Component 6, design review D7), the supervisor's home.

Top to bottom: the open-disputes strip (count, overdue count, oldest age, "Review"; only when disputes
are open), the Line Health rows for the area's lines, and the 3-shift comparison (pace and yield per
line). The header carries the open-dispute count badge (eng review D6). Opens on the last completed
shift with the current shift one click away (D21).
"""

from django.shortcuts import get_object_or_404, render
from django.urls import reverse

from accounts import scope as role_scope
from accounts.decorators import area_scope_required, operator_bearing, render_out_of_scope
from accounts.policy import OperatorScope, page_context
from core.models import Area
from disputes import service as disputes
from metrics import api
from web import windows

from .common import area_hub, area_lines, is_decider, is_lead, on_shift_hub


@operator_bearing
@area_scope_required("area_id")
def area(request, area_id):
    area = get_object_or_404(Area, pk=area_id)
    lines = area_lines(area)
    hub = area_hub(area, lines)
    try:
        window = windows.area_window(request, area, hub)
    except windows.OutOfScopeShift:
        return render_out_of_scope(request, reason="shift")
    ctx = page_context(request, OperatorScope(
        area_ids=[area.pk], shift_ids=[window.shift.pk] if window.shift else None,
        date_from=window.date_from, date_to=window.date_to))
    shift_lines = on_shift_hub(lines, window.shift)
    rows = api.line_health(shift_lines, window.date_from, window.date_to, shift=window.shift) if shift_lines else []

    lead = is_lead(request.user)
    names, comparison = _comparison(shift_lines if lead else lines, window.date_to,
                                    only=window.shift.name if lead and window.shift else None)

    summary = disputes.open_summary([area], windows.now()) if is_decider(request.user) else None
    current = None
    if hub is not None:
        w = windows.current_window(hub)
        if w is not None and (not lead or w.shift.pk == window.shift.pk):
            current = (w.production_date, w.shift)
    action = reverse("area", kwargs={"area_id": area.pk})
    picker = windows.picker(request, window, action, area=area, current=current, allow_all_shifts=not lead)
    areas = list(role_scope.visible_areas(request.user).order_by("name"))
    return render(request, "web/area.html", {
        "takt_page": ctx, "area": area, "rows": rows, "window": window, "picker": picker,
        "comparison_names": names, "comparison": comparison, "comparison_date": window.date_to,
        "summary": summary, "queue_url": reverse("dispute_queue", kwargs={"area_id": area.pk}),
        "other_areas": [a for a in areas if a.pk != area.pk], "nav_active": "area",
    })


def _comparison(lines, production_date, only=None):
    """3-shift table (D7): (shift names in start order, [(line, [ShiftCell or None per name])])."""
    if not lines:
        return [], []
    rows = api.shift_comparison(lines, production_date)
    names = []
    for row in rows:
        for c in row.shifts:
            if c.shift_name not in names and (only is None or c.shift_name == only):
                names.append(c.shift_name)
    out = []
    for row in rows:
        by_name = {c.shift_name: c for c in row.shifts}
        out.append((row.line, [by_name.get(n) for n in names]))
    return names, out
