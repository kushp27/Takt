"""Identity sweep (eng re-review D2): every operator-bearing URL x every role x Gate 1 off/on.

What it asserts, against fixture operators with distinctive names (tests/identity_world.py):
- names appear only when the policy allows: builder always; ops manager, supervisor and
  lead only after Gate 1 and only for operators in their scope;
- before Gate 1 those roles see letters ("Operator C", "Unmapped operator D") instead,
  one distinct letter per visible operator, plus the D12 notice on full pages;
- operators outside the user's scope are never named, never lettered;
- an unmapped alias's hub ID shows only in named mode ("Unmapped operator (HUBQX-77)");
- out of scope -> 403 with the D13 page; admin -> 403 everywhere (never operator metrics);
  anonymous -> redirect to login;
- no view passes a raw operator name into the template context (any mode, any role);
- partials are swept three ways: with the parent page's token, with no token, and with
  a forged token. None may leak.

The URL list can't rot:
- test_every_operator_bearing_url_has_a_sweep_case walks every resolver pattern and fails
  if a view marked @operator_bearing has no SweepCase (or a SweepCase names a URL that
  isn't marked);
- test_unmarked_urls_render_no_operator_identity requests every UNMARKED pattern as the
  builder with Gate 1 on (where names are most visible) and fails if a fixture name or
  hub ID appears: that view renders operators and must be marked and registered.

HOW LANE D ADDS A URL TO THE SWEEP
1. Decorate the view with @operator_bearing (accounts.decorators). For a class-based
   view wrap the callable in urls.py: path(..., operator_bearing(MyView.as_view()), name=...).
   Add the scope decorator too (area_scope_required("area_id", shift_param="shift_id")).
2. In the view, build ctx = page_context(request, OperatorScope(...)) and pass it to the
   template as `takt_page`; render operators only with {% operator %} and friends.
3. Add a SweepCase to SWEEP_CASES in tests/sweep_registry.py:
       SweepCase("web:my_shift",
                 kwargs=lambda w: {"area_id": w.area_in.pk, "shift_id": w.shift_1st.pk},
                 query=lambda w: {"date": w.today.isoformat()},
                 shift="shift_1st")
   - kwargs/query build the request from the World (tests/identity_world.py: w.area_in,
     w.area_out, w.line_in, w.st_a1, w.st_a2, w.st_b1, w.shift_1st, w.shift_3rd, w.part,
     w.ops["zeb"].alias / .operator, w.today, w.dispute);
   - area / shift say which World area/shift the URL is bound to (drives the 403 oracle);
   - expected lists the operator keys a builder sees if not "everyone in area+shift"
     (e.g. a drill-down for one station: lambda w: w.in_scope(station="st_a1"));
   - partial=True for htmx partials; parent="<case id>" makes the sweep reuse that page's
     token (taken from its {% page_hx_vals %} attribute), and also tries no/forged tokens;
   - method="post" + data=... for POST partials (e.g. the dispute job list).
   If the screen needs rows the World doesn't have, extend build_world() (keep names unusual).
4. Run the suite. Nothing else to edit: the matrix of roles and Gate 1 states is automatic.
"""

import re
from dataclasses import dataclass

import pytest
from django.test import Client
from django.urls import URLResolver, get_resolver, reverse

from accounts.decorators import OPERATOR_BEARING_ATTR
from accounts.policy import GATE1_NOTICE, TOKEN_PARAM
from tests.identity_world import ROLES, World, build_world, set_gate1
from tests.sweep_registry import (
    DISCOVERY_KWARGS,
    DISCOVERY_SKIP_NAMES,
    DISCOVERY_SKIP_NAMESPACES,
    SWEEP_CASES,
)

pytestmark = pytest.mark.urls("tests.sweep_urls")

D13_TEXT = "This area isn't in your assignment"
LETTER_RE = re.compile(r"\b(Unmapped operator|Operator) ([A-Z]{1,3})\b")
TOKEN_RE = re.compile(r"hx-vals=\"\{&quot;" + TOKEN_PARAM + r"&quot;: &quot;([^&]+)&quot;\}\"")
FORGED_TOKEN = "eyJuIjoiZm9yZ2VkIn0:forged:signature"

CASES_BY_ID = {c.id: c for c in SWEEP_CASES}


@pytest.fixture
def world():
    return build_world()


def client_for(world, role):
    c = Client()
    user = world.users[role]
    if user is not None:
        c.force_login(user)
    return c


def do_request(client, world, case, token=None):
    url = reverse(case.url_name, kwargs=case.kwargs(world))
    params = dict(case.query(world))
    if token:
        params[TOKEN_PARAM] = token
    headers = {"HX-Request": "true"} if case.partial else {}
    if case.method == "post":
        return client.post(url, {**case.data(world), **params}, headers=headers)
    return client.get(url, params, headers=headers)


def _strings(value, depth=0):
    if isinstance(value, str):
        yield value
    elif depth < 4 and isinstance(value, dict):
        for v in value.values():
            yield from _strings(v, depth + 1)
    elif depth < 4 and isinstance(value, (list, tuple, set, frozenset)):
        for v in value:
            yield from _strings(v, depth + 1)


def context_strings(resp):
    """Every raw string a view (or context processor) put into the template context."""
    contexts = resp.context
    if contexts is None:
        return
    if not isinstance(contexts, list):
        contexts = [contexts]
    for ctx in contexts:
        flat = ctx.flatten() if hasattr(ctx, "flatten") else dict(ctx)
        for key, value in flat.items():
            if key in ("csrf_token",):
                continue
            yield from _strings(value)


def check(world, case, role, gate1, resp, how=""):
    """All policy violations in one response, as readable strings."""
    tag = f"[{case.id} {how} role={role} gate1={'on' if gate1 else 'off'}]"
    errs = []
    body = resp.content.decode(errors="replace") if not resp.streaming else ""
    names = world.names

    for s in context_strings(resp):
        for k, n in names.items():
            if n in s:
                errs.append(f"{tag} raw operator name {n!r} passed into the template context")

    # ---- status oracle
    if role == "anonymous":
        if resp.status_code != 302 or "/login/" not in resp.get("Location", ""):
            errs.append(f"{tag} anonymous: expected redirect to login, got {resp.status_code}")
    elif role == "admin":
        if resp.status_code != 403:
            errs.append(f"{tag} admin must get 403 on operator-bearing URLs, got {resp.status_code}")
    elif not World.role_area_ok(role, case.area) or (case.shift and not World.role_shift_ok(role, case.shift)):
        if resp.status_code != 403:
            errs.append(f"{tag} out of scope: expected 403, got {resp.status_code}")
        elif not case.partial and D13_TEXT not in body:
            errs.append(f"{tag} out of scope: 403 without the D13 page")
    elif case.shift is None and role.startswith("lead") and resp.status_code == 403:
        pass  # a shift-less page may refuse leads outright; that reveals nothing
    elif resp.status_code != 200:
        errs.append(f"{tag} in scope: expected 200, got {resp.status_code}")

    # ---- identity oracle
    mode = World.mode(role, gate1)
    ok_200 = resp.status_code == 200
    visible = {k for k in case.expected_keys(world) if world.may_see(role, k)} if ok_200 else set()
    if mode == "hidden":
        visible = set()

    for k, n in names.items():
        shown = n in body
        if shown and not (mode == "named" and k in visible):
            errs.append(f"{tag} name {n!r} rendered but policy says {mode}/{'in' if k in visible else 'out of'} scope")
        if not shown and mode == "named" and k in visible:
            errs.append(f"{tag} name {n!r} missing: builder/approved roles must see in-scope names")

    for k, hub_id in world.hub_ids.items():
        unmapped = world.ops[k].name is None
        if hub_id in body and not (mode == "named" and unmapped and k in visible):
            errs.append(f"{tag} hub ID {hub_id!r} rendered in {mode} mode")
        if mode == "named" and unmapped and k in visible and f"Unmapped operator ({hub_id})" not in body:
            errs.append(f"{tag} unmapped alias should render as 'Unmapped operator ({hub_id})'")

    letters = LETTER_RE.findall(body)
    if mode == "letter":
        distinct = {letter for _, letter in letters}
        if len(distinct) != len(visible):
            errs.append(f"{tag} expected {len(visible)} distinct letters, saw {sorted(distinct)}")
        n_unmapped = sum(1 for k in visible if world.ops[k].name is None)
        seen_unmapped = {letter for label, letter in letters if label == "Unmapped operator"}
        if len(seen_unmapped) != n_unmapped:
            errs.append(f"{tag} expected {n_unmapped} 'Unmapped operator <letter>' labels, saw {sorted(seen_unmapped)}")
        if visible and not case.partial and GATE1_NOTICE not in body:
            errs.append(f"{tag} D12 Gate 1 notice missing above anonymized rows")
    elif letters:
        errs.append(f"{tag} letters {letters} rendered in {mode} mode")
    return errs


def extract_token(resp):
    m = TOKEN_RE.search(resp.content.decode())
    return m.group(1) if m else None


@pytest.mark.parametrize("gate1", [False, True], ids=["gate1_off", "gate1_on"])
@pytest.mark.parametrize("case", SWEEP_CASES, ids=[c.id for c in SWEEP_CASES])
def test_sweep(world, case, gate1):
    set_gate1(gate1)
    errs = []
    for role in ROLES:
        client = client_for(world, role)
        if not case.partial:
            errs += check(world, case, role, gate1, do_request(client, world, case), "page")
            continue
        token = None
        if case.parent:
            parent_resp = do_request(client, world, CASES_BY_ID[case.parent])
            token = extract_token(parent_resp) if parent_resp.status_code == 200 else None
            if parent_resp.status_code == 200 and token is None:
                errs.append(f"[{case.id}] parent {case.parent} rendered without {{% page_hx_vals %}}")
            if token:
                errs += check(world, case, role, gate1, do_request(client, world, case, token), "parent-token")
        errs += check(world, case, role, gate1, do_request(client, world, case), "no-token")
        errs += check(world, case, role, gate1, do_request(client, world, case, FORGED_TOKEN), "forged-token")
    assert not errs, "\n".join(errs)


# --------------------------------------------------------------------------- discovery


@dataclass
class Route:
    path: str
    name: str | None
    callback: object


def _route_str(pattern):
    raw = str(pattern)
    if hasattr(pattern, "regex") and pattern.__class__.__name__ == "RegexPattern":
        raw = raw.lstrip("^").rstrip("$").replace("\\Z", "")
        raw = re.sub(r"\(\?P<(\w+)>[^)]*\)", r"<\1>", raw).replace("\\", "")
    return raw


def walk(patterns=None, prefix="", ns=""):
    if patterns is None:
        patterns = get_resolver().url_patterns
    for p in patterns:
        if isinstance(p, URLResolver):
            if p.namespace in DISCOVERY_SKIP_NAMESPACES:
                continue
            yield from walk(p.url_patterns, prefix + _route_str(p.pattern), f"{ns}{p.namespace}:" if p.namespace else ns)
        else:
            yield Route(prefix + _route_str(p.pattern), f"{ns}{p.name}" if p.name else None, p.callback)


def guess_kwarg(world, name):
    if name in DISCOVERY_KWARGS:
        return DISCOVERY_KWARGS[name](world)
    guesses = [
        ("area", lambda: world.area_in.pk), ("line", lambda: world.line_in.pk),
        ("station", lambda: world.st_a1.pk), ("shift", lambda: world.shift_1st.pk),
        ("alias", lambda: world.ops["zeb"].alias.pk), ("operator", lambda: world.ops["zeb"].operator.pk),
        ("job", lambda: world.ops["zeb"].alias.jobs.first().pk), ("dispute", lambda: world.dispute.pk),
        ("submission", lambda: world.dispute.submission_id), ("hub", lambda: world.hub.pk),
        ("part", lambda: world.part.pk), ("date", lambda: world.today.isoformat()),
    ]
    for needle, value in guesses:
        if needle in name:
            return value()
    raise LookupError(name)


def fill(world, route):
    def sub(m):
        return str(guess_kwarg(world, m.group(1)))

    return "/" + re.sub(r"<(?:\w+:)?(\w+)>", sub, route.path)


def discover(world):
    """(marked routes, leak messages) for the current ROOT_URLCONF."""
    marked, leaks = [], []
    builder = client_for(world, "builder")
    needles = list(world.names.values()) + list(world.hub_ids.values())
    for route in walk():
        if getattr(route.callback, OPERATOR_BEARING_ATTR, False):
            marked.append(route)
            continue
        if route.name in DISCOVERY_SKIP_NAMES:
            continue
        try:
            path = fill(world, route)
        except LookupError as e:
            leaks.append(f"{route.path}: can't build kwarg {e}; add it to DISCOVERY_KWARGS in tests/sweep_registry.py")
            continue
        for headers in ({}, {"HX-Request": "true"}):
            resp = builder.get(path, headers=headers)
            body = resp.content.decode(errors="replace") if not resp.streaming else ""
            found = [n for n in needles if n in body] + [
                n for s in context_strings(resp) for n in needles if n in s
            ]
            if found:
                leaks.append(f"{path} ({route.name}) renders operator identity {sorted(set(found))} but its view "
                             "isn't @operator_bearing and registered in tests/sweep_registry.py")
                break
    return marked, leaks


def test_every_operator_bearing_url_has_a_sweep_case(world):
    marked, _ = discover(world)
    marked_names = {r.name for r in marked}
    registered = {c.url_name for c in SWEEP_CASES}
    unnamed = [r.path for r in marked if r.name is None]
    assert not unnamed, f"operator-bearing URLs need a name for the sweep: {unnamed}"
    missing = marked_names - registered
    assert not missing, f"@operator_bearing URLs with no SweepCase in tests/sweep_registry.py: {sorted(missing)}"
    stale = registered - marked_names
    assert not stale, f"SweepCases for URLs that don't exist or aren't @operator_bearing: {sorted(stale)}"


def test_unmarked_urls_render_no_operator_identity(world):
    set_gate1(True)  # named mode is where a leak is easiest to see
    _, leaks = discover(world)
    assert not leaks, "\n".join(leaks)


@pytest.mark.urls("tests.sweep_leaky_urls")
def test_harness_catches_leaky_and_unregistered_views(world):
    """Self-test: a name-rendering view without @operator_bearing, and a marked view
    without a SweepCase, must both be reported."""
    set_gate1(True)
    marked, leaks = discover(world)
    assert any("sweep_demo_leaky" in m for m in leaks), leaks
    assert not any("sweep_demo_harmless" in m for m in leaks), leaks
    assert "sweep_demo_marked_no_case" in {r.name for r in marked} - {c.url_name for c in SWEEP_CASES}


def test_harness_detects_a_leak_in_a_registered_view(world):
    """Self-test: check() flags names shown before Gate 1, missing letters, and context leaks."""
    case = CASES_BY_ID["sweep_demo_area"]

    class Fake:
        status_code = 200
        streaming = False
        context = [{"label": "Zebulon Quixote"}]
        content = b"<td>Zebulon Quixote</td>"

        def get(self, *_):
            return ""

    errs = check(world, case, "ops_manager", False, Fake(), "self-test")
    assert any("rendered but policy says letter" in e for e in errs)
    assert any("template context" in e for e in errs)
    assert any("distinct letters" in e for e in errs)
