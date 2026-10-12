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
    ),
    SweepCase(
        "sweep_demo_station",
        kwargs=lambda w: {"station_id": w.st_a1.pk},
        expected=lambda w: w.in_scope(station="st_a1"),
        partial=True,
        parent="sweep_demo_area",
    ),
    SweepCase(
        "sweep_demo_print",
        kwargs=lambda w: {"area_id": w.area_in.pk, "shift_id": w.shift_1st.pk},
        shift="shift_1st",
    ),
    # --- Lane D: register real screens below, e.g.
    # SweepCase("web:my_shift", kwargs=lambda w: {"area_id": w.area_in.pk, "shift_id": w.shift_1st.pk},
    #           shift="shift_1st"),
    # SweepCase("web:operator_jobs", kwargs=lambda w: {"alias_id": w.ops["zeb"].alias.pk},
    #           expected=lambda w: {"zeb"}, partial=True, parent="web:my_shift", shift="shift_1st"),
]

# URL names (or namespaces) that discovery never probes. Keep this short and justified.
DISCOVERY_SKIP_NAMESPACES = {"admin"}  # Django admin: configuration, mapping by design (Component 2)
DISCOVERY_SKIP_NAMES = {"logout"}      # POST-only, renders nothing

# Extra URL kwargs for discovery when the name-based guess isn't enough: name -> World -> value.
DISCOVERY_KWARGS: dict[str, Callable] = {}
