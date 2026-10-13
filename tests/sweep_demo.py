"""Test-only demo views that prove the identity sweep works end to end.

They are wired only by tests/sweep_urls.py (never by takt/urls.py) and show the
pattern Lane D's real screens follow: @operator_bearing + scope decorator,
page_context(), {% operator %}, {% gate1_notice %}, {% page_hx_vals %}.
"""

from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.template import engines
from django.urls import path
from django.utils import timezone

from accounts import scope as role_scope
from accounts.decorators import area_scope_required, operator_bearing, render_out_of_scope
from accounts.policy import OperatorScope, page_context
from core.models import Job, Line, OperatorAlias, Station
from core.shifts import local_date

_PAGE = engines["django"].from_string("""{% load operator %}<!doctype html>
<main {% page_hx_vals %}>
<h1>Demo area</h1>
{% gate1_notice %}
{% operators_sorted aliases "" as rows %}
<table><tbody>{% for a in rows %}<tr><td>{% operator a %}</td></tr>{% endfor %}</tbody></table>
<div hx-get="{{ partial_url }}" hx-trigger="load"></div>
</main>""")

_PARTIAL = engines["django"].from_string("""{% load operator %}
<table><tbody>{% operators_sorted aliases "" as rows %}{% for a in rows %}<tr><td>{% operator a %}</td></tr>{% endfor %}</tbody></table>""")


def _day(request, area_id):
    """?date=YYYY-MM-DD, default today in the area's hub timezone. Bounded scopes need a date."""
    try:
        return date.fromisoformat(request.GET["date"])
    except (KeyError, ValueError):
        line = Line.objects.filter(area_id=area_id).select_related("hub").first()
        return local_date(line.hub, timezone.now()) if line else timezone.localdate()


def _aliases(**job_filter):
    ids = Job.objects.filter(**job_filter).values_list("alias_id", flat=True).distinct()
    return list(OperatorAlias.objects.filter(pk__in=ids).select_related("operator", "hub"))


@operator_bearing
@area_scope_required("area_id")
def demo_area(request, area_id):
    day = _day(request, area_id)
    ctx = page_context(request, OperatorScope(area_ids=[area_id], date_from=day, date_to=day))
    station = Station.objects.filter(line__area_id=area_id).order_by("line", "sequence").first()
    return HttpResponse(_PAGE.render({
        "takt_page": ctx,
        "aliases": _aliases(station__line__area_id=area_id, production_date=day),
        "partial_url": f"/_sweep-demo/station/{station.pk}/?date={day.isoformat()}" if station else "",
    }, request))


@operator_bearing
def demo_station_partial(request, station_id):
    station = Station.objects.select_related("line").filter(pk=station_id).first()
    if station is None or not role_scope.can_view_area(request.user, station.line.area_id):
        return render_out_of_scope(request)
    day = _day(request, station.line.area_id)
    # This partial's own scope must fit inside the parent page's token scope; letters then
    # come from the token, so they match the page. No usable token in letter mode -> HX-Refresh.
    ctx = page_context(request, OperatorScope(area_ids=[station.line.area_id], date_from=day, date_to=day),
                       parents=("sweep_demo_area",))
    return HttpResponse(_PARTIAL.render(
        {"takt_page": ctx, "aliases": _aliases(station=station, production_date=day)}, request))


@operator_bearing
@area_scope_required("area_id", shift_param="shift_id")
def demo_print(request, area_id, shift_id):
    day = _day(request, area_id)
    ctx = page_context(request, OperatorScope(area_ids=[area_id], shift_ids=[shift_id], date_from=day, date_to=day))
    return HttpResponse(_PAGE.render({
        "takt_page": ctx,
        "aliases": _aliases(station__line__area_id=area_id, shift_id=shift_id, production_date=day),
        "partial_url": "",
    }, request))


@login_required
def leaky_unregistered(request):
    """Deliberately wrong: renders names without the policy and without @operator_bearing."""
    names = ", ".join(a.operator.name for a in _aliases() if a.operator)
    return HttpResponse(f"<p>{names}</p>")


@login_required
def marked_by_template_only(request):
    """Unmarked but harmless: no operator identity at all."""
    return HttpResponse("<p>Stations only.</p>")


demo_urlpatterns = [
    path("_sweep-demo/area/<int:area_id>/", demo_area, name="sweep_demo_area"),
    path("_sweep-demo/station/<int:station_id>/", demo_station_partial, name="sweep_demo_station"),
    path("_sweep-demo/print/<int:area_id>/<int:shift_id>/", demo_print, name="sweep_demo_print"),
]

leaky_urlpatterns = [
    path("_sweep-demo/leaky/", leaky_unregistered, name="sweep_demo_leaky"),
    path("_sweep-demo/harmless/", marked_by_template_only, name="sweep_demo_harmless"),
]
