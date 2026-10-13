"""Freshness template tags for every lane: {% load takt_freshness %}.

    {% hub_freshness hub as f %}        ops.freshness.HubFreshness (f.stale, f.data_as_of_display, ...)
    {% stale_tag hub %}                 <span class="stale-tag">stale</span> when the hub is stale, else ""
    {% stale_class hub %}               "is-stale" when the hub is stale, else "" (mutes values; pair with stale_tag)
    {% print_header hub %}              print sheet header lines (D20, eng re-review D6)
    {% freshness_notice hub refresh_url="/shift/" refresh_target="#shift-table" %}
                                        live My Shift poller + polite notice region (eng re-review D5)
    {% freshness_notice_reset hub refresh_url="/shift/" refresh_target="#shift-table" %}
                                        put in the fragment Refresh returns: clears the notice and
                                        restarts polling out of band (hx-swap-oob), forms untouched

`hub` may be a core.models.Hub, a hub code string, or a HubFreshness. Tags reuse the request's
`freshness` snapshot (ops.context_processors.freshness) when present, so a page computes it once.
"""

from urllib.parse import urlencode

from django import template
from django.urls import reverse
from django.utils.html import format_html

from core.models import Hub
from ops import freshness as fr

register = template.Library()


def _resolve(context, hub):
    if isinstance(hub, fr.HubFreshness):
        return hub
    if hub is None or hub == "":
        return None
    snap = context.get("freshness") if context is not None else None
    if snap is not None:
        found = snap.get(hub)
        if found is not None:
            return found
    if not isinstance(hub, Hub):
        hub = Hub.objects.filter(code=hub).first()
        if hub is None:
            return None
    return fr.hub_freshness(hub)


@register.simple_tag(takes_context=True)
def hub_freshness(context, hub):
    return _resolve(context, hub)


@register.simple_tag(takes_context=True)
def stale_tag(context, hub):
    f = _resolve(context, hub)
    if f is None or not f.stale:
        return ""
    return format_html('<span class="stale-tag" title="{} data stopped at {}">stale</span>',
                       f.name, f.stopped_at_display or "an unknown time")


@register.simple_tag(takes_context=True)
def stale_class(context, hub):
    f = _resolve(context, hub)
    return "is-stale" if f is not None and f.stale else ""


@register.inclusion_tag("ops/partials/print_header.html", takes_context=True)
def print_header(context, hub):
    f = _resolve(context, hub)
    header = fr.print_header(f.hub, now=f.now) if f is not None else None
    return {"header": header}


def _poller_context(context, hub, refresh_url, refresh_target):
    f = _resolve(context, hub)
    if f is None:
        return {"poll_url": None}
    params = {"hub": f.code, "since": f.last_new_transaction_at.isoformat() if f.last_new_transaction_at else ""}
    if refresh_url:
        params["refresh_url"] = refresh_url
    if refresh_target:
        params["refresh_target"] = refresh_target
    return {"poll_url": f"{reverse('freshness_notice')}?{urlencode(params)}", "hub_code": f.code}


@register.inclusion_tag("ops/partials/freshness_notice.html", takes_context=True)
def freshness_notice(context, hub, refresh_url="", refresh_target=""):
    return _poller_context(context, hub, refresh_url, refresh_target)


@register.inclusion_tag("ops/partials/freshness_notice_reset.html", takes_context=True)
def freshness_notice_reset(context, hub, refresh_url="", refresh_target=""):
    """Include in the fragment Refresh swaps in: an hx-swap-oob poller with the new `since`."""
    return _poller_context(context, hub, refresh_url, refresh_target)
