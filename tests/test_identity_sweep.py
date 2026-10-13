"""Identity sweep (eng re-review D2): every operator-bearing URL x every role x Gate 1 off/on.

What it asserts, against fixture operators with distinctive names (tests/identity_world.py):
- names appear only when the policy allows: builder always; ops manager, supervisor and
  lead only after Gate 1 and only for operators in their scope;
- before Gate 1 those roles see letters ("Operator C", "Unmapped operator D") instead,
  one distinct letter per visible operator, dense on full pages (A, B, C...), in
  ascending order within every table or list (so rows never follow name order), plus
  the D12 notice on full pages;
- in letter mode no model str() form leaks ("Operator #12", "cores:ZQ-101");
- operators outside the user's scope are never named, never lettered;
- an unmapped alias's hub ID shows only in named mode ("Unmapped operator (HUBQX-77)");
- out of scope -> 403 with the D13 copy (page, or inline fragment for htmx); admin -> 403
  everywhere (never operator metrics); anonymous -> redirect to login;
- no view passes a raw operator name into the template context (any mode, any role);
- partials are swept three ways: with the parent page's token, with no token, and with
  a forged token. None may leak; in letter mode the last two must answer HX-Refresh.

The URL list can't rot:
- test_every_operator_bearing_url_has_a_sweep_case walks every resolver pattern and fails
  if a view marked @operator_bearing has no SweepCase (or a SweepCase names a URL that
  isn't marked);
- test_unmarked_urls_render_no_operator_identity requests every UNMARKED pattern as every
  role, Gate 1 off and on, plain and htmx, with and without common query params
  (area_id, shift_id, station_id, line_id, date + DISCOVERY_KWARGS), and fails if a
  fixture name, hub ID or model str() form appears: that view renders operators and must
  be marked and registered.

HOW LANE D ADDS A URL TO THE SWEEP
1. Decorate the view with @operator_bearing (accounts.decorators). For a class-based
   view wrap the callable in urls.py: path(..., operator_bearing(MyView.as_view()), name=...).
   Add the scope decorator too (area_scope_required("area_id", shift_param="shift_id")).
2. In the view, build ctx = page_context(request, OperatorScope(..., date_from=d, date_to=d))
   (partials add parents=("<page view name>",)) and pass it to the template as `takt_page`;
   render operators only with {% operator %} and friends, in ctx.sorted()/{% operators_sorted %}
   order (never queryset order: Operator.Meta.ordering is by name).
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
   - method="post" + data=... for POST partials (e.g. the dispute job list);
   - lead_may_refuse=True only for a shift-less URL that deliberately 403s leads.
   If the screen needs rows the World doesn't have, extend build_world() (keep names unusual).
4. Run the suite. Nothing else to edit: the matrix of roles and Gate 1 states is automatic.
"""

import re
from dataclasses import dataclass

import pytest
from django.test import Client
from django.urls import URLResolver, get_resolver, reverse

from accounts.decorators import OPERATOR_BEARING_ATTR
from accounts.policy import GATE1_NOTICE, TOKEN_PARAM, _letters
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
BLOCK_RE = re.compile(r"<(table|ul|ol)\b.*?</\1>", re.DOTALL | re.IGNORECASE)

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


def expected_status(world, case, role):
    """'login' | '403' | '200' | '200-or-403' from the oracle in tests/identity_world.py."""
    if role == "anonymous":
        return "login"
    if role == "admin":
        return "403"
    if not World.role_area_ok(role, case.area) or (case.shift and not World.role_shift_ok(role, case.shift)):
        return "403"
    if case.lead_may_refuse and case.shift is None and role.startswith("lead"):
        return "200-or-403"
    return "200"


def letter_key(letter):
    return (len(letter), letter)


def check(world, case, role, gate1, resp, how=""):
    """All policy violations in one response, as readable strings."""
    tag = f"[{case.id} {how} role={role} gate1={'on' if gate1 else 'off'}]"
    errs = []
    body = resp.content.decode(errors="replace") if not resp.streaming else ""
    names = world.names
    mode = World.mode(role, gate1)

    for s in context_strings(resp):
        for k, n in names.items():
            if n in s:
                errs.append(f"{tag} raw operator name {n!r} passed into the template context")

    # ---- status oracle
    expect = expected_status(world, case, role)
    refresh = resp.get("HX-Refresh") == "true"
    # A partial without a usable page token must not mint fresh letters (D3 failure mode).
    expect_refresh = (case.partial and mode == "letter" and how in ("no-token", "forged-token")
                      and expect in ("200", "200-or-403"))
    if expect_refresh:
        if not refresh:
            errs.append(f"{tag} letter mode without a usable page token must answer HX-Refresh, "
                        f"got {resp.status_code}")
        if LETTER_RE.search(body) or any(n in body for n in names.values()):
            errs.append(f"{tag} HX-Refresh response rendered operators")
        return errs
    if refresh:
        errs.append(f"{tag} unexpected HX-Refresh")
    if expect == "login":
        if resp.status_code != 302 or "/login/" not in resp.get("Location", ""):
            errs.append(f"{tag} anonymous: expected redirect to login, got {resp.status_code}")
    elif expect == "403":
        if resp.status_code != 403:
            errs.append(f"{tag} expected 403 (out of scope or admin), got {resp.status_code}")
        elif D13_TEXT not in body:
            errs.append(f"{tag} 403 without the D13 copy")
    elif expect == "200-or-403" and resp.status_code == 403:
        pass  # this case declares that it refuses leads outright; that reveals nothing
    elif resp.status_code != 200:
        errs.append(f"{tag} in scope: expected 200, got {resp.status_code}")

    # ---- identity oracle
    visible = {k for k in case.expected_keys(world) if world.may_see(role, k)} if resp.status_code == 200 else set()
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

    if mode != "named":
        # Model __str__ forms: Operator ("Operator #12") and OperatorAlias ("cores:ZQ-101").
        for pattern in (r"Operator #\d+", rf"\b{re.escape(world.hub.code)}:[\w-]+"):
            if re.search(pattern, body):
                errs.append(f"{tag} raw model str() {re.search(pattern, body).group(0)!r} rendered in {mode} mode")

    letters = LETTER_RE.findall(body)
    if mode == "letter":
        distinct = {letter for _, letter in letters}
        if len(distinct) != len(visible):
            errs.append(f"{tag} expected {len(visible)} distinct letters, saw {sorted(distinct)}")
        if not case.partial and case.expected is None and distinct:
            dense = {_letters(i) for i in range(len(distinct))}
            if distinct != dense:
                errs.append(f"{tag} letters not dense: {sorted(distinct, key=letter_key)}")
        n_unmapped = sum(1 for k in visible if world.ops[k].name is None)
        seen_unmapped = {letter for label, letter in letters if label == "Unmapped operator"}
        if len(seen_unmapped) != n_unmapped:
            errs.append(f"{tag} expected {n_unmapped} 'Unmapped operator <letter>' labels, saw {sorted(seen_unmapped)}")
        # Rows must not follow name order (e.g. Operator.Meta.ordering = ["name"]): within each
        # table or list, letters appear in ascending order.
        # A grouped table (My Shift: by station and part) sorts each <tbody> on its own.
        for block in _order_blocks(body):
            seq = [letter for _, letter in LETTER_RE.findall(block)]
            if [letter_key(x) for x in seq] != sorted(letter_key(x) for x in seq):
                errs.append(f"{tag} letters out of order in a table/list: {seq} (name order leaking?)")
        if visible and not case.partial and GATE1_NOTICE not in body:
            errs.append(f"{tag} D12 Gate 1 notice missing above anonymized rows")
    elif letters:
        errs.append(f"{tag} letters {letters} rendered in {mode} mode")
    return errs


TBODY_RE = re.compile(r"<tbody\b.*?</tbody>", re.DOTALL | re.IGNORECASE)


def _order_blocks(body):
    for m in BLOCK_RE.finditer(body):
        block = m.group(0)
        bodies = TBODY_RE.findall(block) if m.group(1).lower() == "table" else []
        yield from (bodies or [block])


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


DEFAULT_QUERY_PARAMS = ("area_id", "shift_id", "station_id", "line_id", "date")


def discovery_query(world):
    """GET params sent on every probe: the common window/scope params plus DISCOVERY_KWARGS."""
    query = {name: guess_kwarg(world, name) for name in DEFAULT_QUERY_PARAMS}
    query.update({name: fn(world) for name, fn in DISCOVERY_KWARGS.items()})
    return query


def identity_found(world, resp):
    """Fixture names, hub IDs and model str() forms in a response body or its context."""
    needles = list(world.names.values()) + list(world.hub_ids.values())
    body = resp.content.decode(errors="replace") if not resp.streaming else ""
    found = [n for n in needles if n in body] + [n for s in context_strings(resp) for n in needles if n in s]
    for pattern in (r"Operator #\d+", rf"\b{re.escape(world.hub.code)}:[\w-]+"):
        found += re.findall(pattern, body)
    return sorted(set(found))


def discover(world):
    """(marked routes, leak messages) for the current ROOT_URLCONF.

    Every unmarked URL is requested as every role (with whatever Gate 1 state the caller
    set), plain and as htmx, with and without the discovery query params. Any operator
    identity in the response means the view renders operators without being registered.
    """
    marked, leaks = [], []
    clients = {role: client_for(world, role) for role in ROLES}
    query = discovery_query(world)
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
        probes = [({}, {}), (query, {}), (query, {"HX-Request": "true"})]
        for role, client in clients.items():
            hit = None
            for params, headers in probes:
                found = identity_found(world, client.get(path, params, headers=headers))
                if found:
                    hit = found
                    break
            if hit:
                leaks.append(f"{path} ({route.name}) renders operator identity {hit} to {role} but its view "
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


@pytest.mark.parametrize("gate1", [False, True], ids=["gate1_off", "gate1_on"])
def test_unmarked_urls_render_no_operator_identity(world, gate1):
    set_gate1(gate1)
    _, leaks = discover(world)
    assert not leaks, "\n".join(leaks)


@pytest.mark.urls("tests.sweep_leaky_urls")
@pytest.mark.parametrize("gate1", [False, True], ids=["gate1_off", "gate1_on"])
def test_harness_catches_leaky_and_unregistered_views(world, gate1):
    """Self-test: a name-rendering view without @operator_bearing, and a marked view
    without a SweepCase, must both be reported."""
    set_gate1(gate1)
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

    Fake.context = []
    Fake.content = b"<table><tr><td>Operator C</td></tr><tr><td>Operator A</td></tr></table><p>Operator #12 cores:ZQ-101</p>"
    errs = check(world, case, "ops_manager", False, Fake(), "self-test")
    assert any("out of order" in e for e in errs)
    assert any("'Operator #12'" in e for e in errs)
    assert any("'cores:ZQ-101'" in e for e in errs)
    assert any("not dense" in e for e in errs)

    # A grouped table sorts each <tbody> on its own: that is not a leak, but out of order within one is.
    Fake.content = (b"<table><tbody><tr><td>Operator B</td></tr><tr><td>Operator C</td></tr></tbody>"
                    b"<tbody><tr><td>Operator A</td></tr><tr><td>Operator D</td></tr></tbody></table>")
    assert not any("out of order" in e for e in check(world, case, "ops_manager", False, Fake(), "self-test"))
    Fake.content = b"<table><tbody><tr><td>Operator D</td></tr><tr><td>Operator A</td></tr></tbody></table>"
    assert any("out of order" in e for e in check(world, case, "ops_manager", False, Fake(), "self-test"))
