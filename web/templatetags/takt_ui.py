"""Small display helpers for the screens: {% load takt_ui %}.

    {% trust_badge cell.badge %}          D16 trust badge: words + outline glyph, never fill colour
    {% status_word row %}                 D3 status word with its class (colour only with a word)
    {{ minutes|duration }}                "45 min", "2.4 h"
    {{ user|user_short }}                 "J. Ortiz" for a Django user (supervisors, leads); never operators
    {{ instant|plant_time }}              "Tue 3:10 PM" in the display timezone
    {% sparkline row %}                   D22 14-day sparkline (inline SVG, accessible name = trend_text)
"""

from django import template
from django.utils import timezone

from web import sparkline as spark

register = template.Library()

STATUS_CLASSES = {
    "behind": "status-behind",
    "yield_problem": "status-yield",
    "on_pace": "status-ok",
    # Neutral words: no performance colour (Pace unavailable is the user-approved fifth word).
    "no_demand": "status-neutral",
    "pace_unavailable": "status-neutral",
}


@register.inclusion_tag("partials/trust_badge.html")
def trust_badge(badge, long=False):
    """badge: trust.matrix.Badge. long=True shows the full detail ("Not graded yet (needs 4 weeks;
    9 days so far)") instead of the word; not-graded always shows its detail (design review D11)."""
    show_detail = bool(badge) and (long or badge.key == "not_graded")
    return {"badge": badge, "show_detail": show_detail}


@register.filter
def status_class(status_key):
    return STATUS_CLASSES.get(status_key or "", "status-neutral")


@register.filter
def duration(minutes):
    if minutes is None or minutes == "":
        return ""
    m = float(minutes)
    if m < 60:
        return f"{round(m)} min"
    return f"{m / 60:.1f} h"


@register.filter
def user_short(user):
    """'J. Ortiz' from first and last name, else the username. For Django users only."""
    if user is None:
        return ""
    first = (getattr(user, "first_name", "") or "").strip()
    last = (getattr(user, "last_name", "") or "").strip()
    if first and last:
        return f"{first[0]}. {last}"
    if last or first:
        return last or first
    return user.get_username()


@register.filter
def plant_time(instant):
    """'Tue 3:10 PM' (design review D23) in the display timezone."""
    if instant is None:
        return ""
    local = timezone.localtime(instant)
    return f"{local:%a} {local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


@register.filter
def clock_time(instant):
    """'6:42 AM' in the display timezone."""
    if instant is None:
        return ""
    local = timezone.localtime(instant)
    return f"{local.hour % 12 or 12}:{local.minute:02d} {'AM' if local.hour < 12 else 'PM'}"


@register.inclusion_tag("web/partials/sparkline.html")
def sparkline(row):
    return {"spark": spark.build(row.trend, row.pace_baseline, row.yield_baseline), "label": row.trend_text}
