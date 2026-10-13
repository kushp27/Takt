"""Registry of operator-bearing URLs for the identity sweep (tests/test_identity_sweep.py).

Lane D: every view decorated with @operator_bearing needs exactly one SweepCase here
(or several, with distinct ids), keyed by its URL name. See the test module docstring.
"""

from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class SweepCase:
    url_name: str                                   # reverse() name, with namespace if any ("web:my_shift")
    kwargs: Callable = lambda w: {}                 # World -> URL kwargs
    query: Callable = lambda w: {}                  # World -> GET params
    area: str = "area_in"                           # World attribute: the area this URL is scoped to
    shift: str | None = None                        # World attribute when the URL is bound to one shift
    expected: Callable | None = None                # World -> operator keys a builder sees; default: in area+shift
    partial: bool = False                           # htmx partial: sent with HX-Request; no D12 notice required
    lead_may_refuse: bool = False                   # shift-less URL that refuses leads outright (403) by design
    parent: str | None = None                       # id of the page case whose token this partial reuses
    method: str = "get"
    data: Callable = lambda w: {}                   # World -> POST body when method == "post"
    case_id: str | None = None

    @property
    def id(self):
        return self.case_id or self.url_name

    def expected_keys(self, world):
        if self.expected is not None:
            return set(self.expected(world))
        return world.in_scope(area=self.area, shift=self.shift)


SWEEP_CASES = [
    # --- test-only demo views (tests/sweep_demo.py); they prove the harness end to end.
    SweepCase(
        "sweep_demo_area",
        kwargs=lambda w: {"area_id": w.area_in.pk},
        query=lambda w: {"date": w.today.isoformat()},
    ),
    SweepCase(
        "sweep_demo_station",
        kwargs=lambda w: {"station_id": w.st_a1.pk},
        query=lambda w: {"date": w.today.isoformat()},
        expected=lambda w: w.in_scope(station="st_a1"),
        partial=True,
        parent="sweep_demo_area",
    ),
    SweepCase(
        "sweep_demo_print",
        kwargs=lambda w: {"area_id": w.area_in.pk, "shift_id": w.shift_1st.pk},
        query=lambda w: {"date": w.today.isoformat()},
        shift="shift_1st",
    ),
    # --- Lane D: the real screens. Line Health (/line/) and its station drill-down render no operator
    # identity and are not marked: discovery probes them as unmarked URLs.
    # Area (supervisor's home): line-level only, but it mints the token its operator drill-down reuses.
    SweepCase(
        "area",
        kwargs=lambda w: {"area_id": w.area_in.pk},
        query=lambda w: {"date": w.today.isoformat()},
        expected=lambda w: set(),
    ),
    # Operator drill-down at one station (from Line Health or Area). A lead sees only their shift.
    SweepCase(
        "station_operators",
        kwargs=lambda w: {"station_id": w.st_a1.pk},
        query=lambda w: {"date": w.today.isoformat()},
        expected=lambda w: w.in_scope(station="st_a1"),
        partial=True,
        parent="area",
    ),
    # My Shift (lead's home), its Refresh fragment, the job list, the dispute POST, the print sheet.
    SweepCase(
        "my_shift",
        kwargs=lambda w: {"area_id": w.area_in.pk},
        query=lambda w: {"shift_id": w.shift_1st.pk, "date": w.today.isoformat()},
        shift="shift_1st",
    ),
    SweepCase(
        "my_shift_table",
        kwargs=lambda w: {"area_id": w.area_in.pk},
        query=lambda w: {"shift_id": w.shift_1st.pk, "date": w.today.isoformat()},
        shift="shift_1st",
        partial=True,
        parent="my_shift",
    ),
    SweepCase(
        "my_shift_jobs",
        kwargs=lambda w: {"area_id": w.area_in.pk, "alias_id": w.ops["zeb"].alias.pk},
        query=lambda w: {"shift_id": w.shift_1st.pk, "date": w.today.isoformat()},
        expected=lambda w: {"zeb"},
        shift="shift_1st",
        partial=True,
        parent="my_shift",
    ),
    SweepCase(
        "my_shift_dispute",
        kwargs=lambda w: {"area_id": w.area_in.pk},
        method="post",
        data=lambda w: {"shift_id": w.shift_1st.pk, "date": w.today.isoformat(),
                        "alias_id": w.ops["zeb"].alias.pk,
                        "job": [w.ops["zeb"].alias.jobs.order_by("pk").last().pk], "reason": "wrong_quantity"},
        expected=lambda w: {"zeb"},
        shift="shift_1st",
        partial=True,
        parent="my_shift",
    ),
    SweepCase(
        "print_sheet",
        kwargs=lambda w: {"area_id": w.area_in.pk},
        query=lambda w: {"shift_id": w.shift_1st.pk, "date": w.today.isoformat()},
        shift="shift_1st",
    ),
    # Dispute queue (supervisor): leads raise disputes on My Shift but never decide them.
    SweepCase(
        "dispute_queue",
        kwargs=lambda w: {"area_id": w.area_in.pk},
        expected=lambda w: {"zeb"},
        lead_may_refuse=True,
    ),
    SweepCase(
        "dispute_detail",
        kwargs=lambda w: {"area_id": w.area_in.pk, "dispute_id": w.dispute.pk},
        expected=lambda w: {"zeb"},
        lead_may_refuse=True,
        partial=True,
        parent="dispute_queue",
    ),
    SweepCase(
        "dispute_decide",
        kwargs=lambda w: {"area_id": w.area_in.pk, "dispute_id": w.dispute.pk},
        method="post",
        data=lambda w: {"decision": "confirm", "note": "Traveler shows 8."},
        expected=lambda w: {"zeb"},
        lead_may_refuse=True,
        partial=True,
        parent="dispute_queue",
    ),
]

# URL names (or namespaces) that discovery never probes. Keep this short and justified.
DISCOVERY_SKIP_NAMESPACES = {"admin"}  # Django admin: configuration, mapping by design (Component 2)
DISCOVERY_SKIP_NAMES = {"logout"}      # POST-only, renders nothing

# Discovery values by parameter name: name -> World -> value. Used for URL kwargs the
# name-based guess can't fill AND sent as GET params on every probe of an unmarked URL
# (on top of the defaults area_id, shift_id, station_id, line_id, date), so views that
# read their window from the query string are probed with data in it.
DISCOVERY_KWARGS: dict[str, Callable] = {}
