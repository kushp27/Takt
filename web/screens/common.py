"""Helpers shared by the screens (Line Health, Area, My Shift, print sheet, dispute queue)."""

import json

from accounts import scope as role_scope
from accounts.models import Profile
from core.models import Line, SiteSettings, Station
from metrics import api
from metrics.windows import HubPlan

DECIDER_ROLES = (role_scope.BUILDER, Profile.OPS_MANAGER, Profile.SUPERVISOR)


def is_lead(user):
    return role_scope.role_of(user) == Profile.LEAD


def is_decider(user):
    """Roles that decide disputes: builder, ops manager, supervisor (Component 8)."""
    return role_scope.role_of(user) in DECIDER_ROLES


def area_lines(area):
    return list(Line.objects.filter(area=area).select_related("hub", "area").order_by("name"))


def area_hub(area, lines=None):
    """The hub an area's live notice and print header follow: its first line's hub (by name)."""
    lines = area_lines(area) if lines is None else lines
    return lines[0].hub if lines else None


def on_shift_hub(lines, shift):
    """A shift belongs to one hub: with a shift chosen, only that hub's lines (never another hub's
    same-named shift). All lines when no shift is chosen."""
    return [ln for ln in lines if shift is None or ln.hub_id == shift.hub_id]


def unique_hubs(lines):
    seen = {}
    for ln in lines:
        seen.setdefault(ln.hub.pk, ln.hub)
    return list(seen.values())


def announce(response, text, **events):
    """Ask takt.js to announce `text` once in the polite live region after the swap (design D18).
    events: more HX-Trigger events, e.g. navcount=3 -> {"takt:navcount": 3}."""
    payload = {"takt:announce": text}
    payload.update({f"takt:{k}": v for k, v in events.items()})
    response["HX-Trigger-After-Settle"] = json.dumps(payload)
    return response


def station_blocks(area, window, hub, plan=None):
    """My Shift / print sheet body: [(Station, [OperatorGroup])] for the area's stations on `hub` (the
    shift's hub: a shift belongs to one hub, so another hub's same-named shift never leaks in) that ran in
    the window, in line then sequence order. Grouped by station and part, never ranked across stations."""
    stations = (Station.objects.filter(line__area=area, line__hub=hub).select_related("line__hub")
                .order_by("line__name", "sequence"))
    plan, settings = plan or HubPlan(hub), SiteSettings.load()
    blocks = []
    for st in stations:
        groups = api.operator_groups(st, None, window, plan=plan, settings=settings)
        if groups:
            blocks.append((st, groups))
    return blocks


def group_columns(group, with_disputes=False):
    """Number of columns an operator group's table has (for colspans), per the matrix cell."""
    c = group.cell
    n = 3  # operator, jobs, quantity
    n += 1 if (c.show_rates or c.rate_text) else 0
    n += 1 if c.show_transaction_gap else 0
    n += 1 if c.show_upstream_gap else 0
    n += 1 if c.show_tenure else 0
    n += 1 if (with_disputes and c.show_dispute) else 0
    return n
