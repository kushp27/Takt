import re
from datetime import timezone as dt_timezone
from urllib.parse import urlsplit

from django.contrib.auth.views import redirect_to_login
from django.db import DatabaseError
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from core.models import Hub, SiteSettings

from . import freshness


@never_cache
@require_GET
def healthz(request):
    """For IT's monitor (eng review D5). No auth, no SMTP.

    200 when every hub is fresh (or no hub is configured); 503 when any hub is stale or the
    database is unreachable. The body always carries an overall status and per-hub freshness.
    """
    checked_at = freshness.current_time()
    body = {"status": "error", "database": "unreachable", "checked_at": checked_at.isoformat(),
            "stale_after_minutes": None, "hubs": []}
    if not freshness.database_reachable():
        return JsonResponse(body, status=503)
    try:
        threshold = SiteSettings.load().stale_after_minutes
        snap = freshness.snapshot(checked_at, threshold)
    except DatabaseError:
        return JsonResponse(body, status=503)
    body.update(
        status="stale" if snap.any_stale else "ok",
        database="ok",
        stale_after_minutes=threshold,
        hubs=[h.as_json() for h in snap.hubs],
    )
    return JsonResponse(body, status=503 if snap.any_stale else 200)


_SAFE_URL = re.compile(r"^/(?!/)[^\s\"'<>\\]*$")
_SAFE_TARGET = re.compile(r"^[#.][A-Za-z][A-Za-z0-9_-]*$")


@never_cache
@require_GET
def freshness_notice(request):
    """Live My Shift poll (eng re-review D5).

    GET /freshness/notice?hub=<code>&since=<ISO data time the page rendered>
        [&refresh_url=</path>][&refresh_target=<#id or .class>]

    200 + a tiny fragment "New data from 2:45 PM · Refresh" when the hub has a new transaction
    after `since` (an empty `since` means the page rendered with no data). Otherwise 204, which
    htmx ignores. No operator data. The poller is rendered by {% freshness_notice %}.

    Login required. An htmx poll from an expired session gets 204 (not a redirect), so the login
    page is never swapped into the polite live region; the next navigation shows the login page.
    """
    if not request.user.is_authenticated:
        if request.headers.get("HX-Request"):
            return HttpResponse(status=204)
        return redirect_to_login(request.get_full_path())
    hub = Hub.objects.filter(code=request.GET.get("hub", "")).first()
    if hub is None:
        return HttpResponseBadRequest("unknown hub")
    raw_since = request.GET.get("since", "").strip().replace(" ", "+")  # an unencoded '+' arrives as a space
    since = None
    if raw_since:
        try:
            since = parse_datetime(raw_since)
        except ValueError:
            since = None
        if since is None:
            return HttpResponseBadRequest("bad since")
        if timezone.is_naive(since):
            since = timezone.make_aware(since, dt_timezone.utc)
    latest = hub.last_new_transaction_at
    if latest is None or (since is not None and latest <= since):
        return HttpResponse(status=204)
    refresh_url = request.GET.get("refresh_url", "")
    if not refresh_url and request.headers.get("HX-Current-URL"):
        # No explicit target: reload the page the poll came from (path + query only).
        current = urlsplit(request.headers["HX-Current-URL"])
        refresh_url = current.path + (f"?{current.query}" if current.query else "")
    if not _SAFE_URL.match(refresh_url):
        refresh_url = ""
    refresh_target = request.GET.get("refresh_target", "")
    if not _SAFE_TARGET.match(refresh_target):
        refresh_target = ""
    return render(request, "ops/partials/freshness_notice_fragment.html", {
        "notice_hub": hub,
        "new_data_at": freshness.display_time(latest, tz=hub.plant_timezone),
        "refresh_url": refresh_url,
        "refresh_target": refresh_target,
    })


def server_error(request, *args, **kwargs):
    """handler500 (D13): errors raised outside a view (e.g. in middleware) while the database is down
    still get the plain 503 page; anything else gets Django's default 500."""
    from django.views.defaults import server_error as default_server_error

    from .middleware import database_unavailable_response

    if not freshness.database_reachable():
        return database_unavailable_response()
    return default_server_error(request, *args, **kwargs)
