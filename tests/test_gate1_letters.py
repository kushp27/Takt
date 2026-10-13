"""E-T2: Gate 1 page tokens and HMAC letters (eng re-review D3, design review D12)."""

import json
import re
from datetime import datetime, time, timedelta

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core import signing
from django.test import Client, RequestFactory
from django.utils import timezone

from accounts import policy
from accounts.policy import OperatorScope, PageContext, mint_token, page_context, read_token
from tests import factories as f
from tests.identity_world import build_world, set_gate1

pytestmark = pytest.mark.urls("tests.sweep_urls")
rf = RequestFactory()
LETTER_RE = re.compile(r"(?:Unmapped operator|Operator) [A-Z]+\b")
TOKEN_RE = re.compile(r"hx-vals=\"\{&quot;" + policy.TOKEN_PARAM + r"&quot;: &quot;([^&]+)&quot;\}\"")
HX = {"HX-Request": "true"}


@pytest.fixture
def w():
    set_gate1(False)
    return build_world()


def logged_in(w, role):
    c = Client()
    c.force_login(w.users[role])
    return c


def load_page(c, w, day=None):
    return c.get(f"/_sweep-demo/area/{w.area_in.pk}/", {"date": (day or w.today).isoformat()})


def token_of(resp):
    return TOKEN_RE.search(resp.content.decode()).group(1)


def load_partial(c, w, token, station=None, day=None):
    params = {"date": (day or w.today).isoformat()}
    if token:
        params[policy.TOKEN_PARAM] = token
    return c.get(f"/_sweep-demo/station/{(station or w.st_a1).pk}/", params, headers=HX)


def labels(ctx, w, keys):
    return {k: ctx.display(w.ops[k].alias).label for k in keys}


def scope(w, **kw):
    kw.setdefault("area_ids", [w.area_in.pk])
    return OperatorScope(date_from=w.today, date_to=w.today, **kw)


# --------------------------------------------------------------------------- agreement


@pytest.mark.parametrize("role", ["ops_manager", "sup_in", "lead_own"])
def test_parent_and_partial_letters_agree_on_a_subset(w, role):
    c = logged_in(w, role)
    page = load_page(c, w)
    partial = load_partial(c, w, token_of(page))
    pctx, sctx = page.context["takt_page"], partial.context["takt_page"]
    assert sctx.reused and sctx.nonce == pctx.nonce
    station_keys = w.in_scope(station="st_a1")  # zeb, pem, hub: a subset of the page's operators
    assert labels(sctx, w, station_keys) == labels(pctx, w, station_keys)
    assert set(LETTER_RE.findall(partial.content.decode())) <= set(LETTER_RE.findall(page.content.decode()))


def test_page_letters_are_dense(w):
    body = load_page(logged_in(w, "ops_manager"), w).content.decode()
    letters = sorted({m.split()[-1] for m in LETTER_RE.findall(body)})
    assert letters == ["A", "B", "C", "D"]  # zeb, ott, pem, hub


def test_two_page_loads_get_different_tokens(w):
    c = logged_in(w, "ops_manager")
    a, b = load_page(c, w).context["takt_page"], load_page(c, w).context["takt_page"]
    assert a.token != b.token and a.nonce != b.nonce


def test_full_page_load_ignores_a_sent_token(w):
    c = logged_in(w, "ops_manager")
    first = load_page(c, w).context["takt_page"]
    again = c.get(f"/_sweep-demo/area/{w.area_in.pk}/",
                  {"date": w.today.isoformat(), policy.TOKEN_PARAM: first.token}).context["takt_page"]
    assert not again.reused and again.nonce != first.nonce


def _ctx(w, nonce, role="ops_manager"):
    return PageContext(w.users[role], scope(w), nonce, timezone.now(), "t")


def test_letters_are_deterministic_per_token_and_reshuffle_across_tokens(w):
    for i in range(4):  # a bigger crew so orderings have room to differ
        op = f.operator(f"Extra Crewmember {i}")
        f.job(w.st_a2, w.part, f.alias(op, w.hub), timezone.now() - timedelta(hours=1),
              shift=w.shift_1st, production_date=w.today)
    assert _ctx(w, "fixed-1").letters == _ctx(w, "fixed-1").letters
    orderings = {tuple(sorted(_ctx(w, f"fixed-{i}").letters.items())) for i in range(20)}
    assert len(orderings) > 1
    letters = _ctx(w, "fixed-1").letters
    assert sorted(letters.values()) == [chr(65 + i) for i in range(len(letters))]


def test_letter_sequence_past_z():
    assert [policy._letters(i) for i in (0, 25, 26, 27, 51, 52)] == ["A", "Z", "AA", "AB", "AZ", "BA"]


# --------------------------------------------------------------------------- rejected tokens


@pytest.mark.parametrize("bad", ["", "garbage", "eyJuIjoiZm9yZ2VkIn0:forged:sig"])
def test_bad_token_in_letter_mode_answers_hx_refresh(w, bad):
    resp = load_partial(logged_in(w, "ops_manager"), w, bad)
    assert resp["HX-Refresh"] == "true"
    body = resp.content.decode()
    assert not LETTER_RE.search(body) and not any(n in body for n in w.names.values())


def test_bad_token_in_named_mode_renders_fresh(w):
    set_gate1(True)
    resp = load_partial(logged_in(w, "ops_manager"), w, "garbage")
    assert resp.status_code == 200 and "HX-Refresh" not in resp
    assert "Zebulon Quixote" in resp.content.decode()
    assert not resp.context["takt_page"].reused


def test_token_from_another_view_is_rejected(w):
    c = logged_in(w, "ops_manager")
    print_page = c.get(f"/_sweep-demo/print/{w.area_in.pk}/{w.shift_1st.pk}/", {"date": w.today.isoformat()})
    resp = load_partial(c, w, token_of(print_page))
    assert resp["HX-Refresh"] == "true"


def test_partial_scope_outside_token_scope_is_rejected(w):
    c = logged_in(w, "ops_manager")
    token = token_of(load_page(c, w))
    assert load_partial(c, w, token, day=w.today - timedelta(days=1))["HX-Refresh"] == "true"
    assert load_partial(c, w, token, station=w.st_b1)["HX-Refresh"] == "true"
    assert "HX-Refresh" not in load_partial(c, w, token)


def test_scope_contains():
    d = datetime(2026, 10, 12).date()
    page = OperatorScope(area_ids=[1], date_from=d, date_to=d)
    assert page.contains(OperatorScope(area_ids=[1], shift_ids=[3], date_from=d, date_to=d))
    assert not page.contains(OperatorScope(area_ids=[1, 2], date_from=d, date_to=d))
    assert not page.contains(OperatorScope(area_ids=None, date_from=d, date_to=d))
    assert not page.contains(OperatorScope(area_ids=[1], date_from=d, date_to=d + timedelta(days=1)))
    shift_page = OperatorScope(area_ids=[1], shift_ids=[3], date_from=d, date_to=d)
    assert not shift_page.contains(OperatorScope(area_ids=[1], date_from=d, date_to=d))


def test_tampered_payload_of_a_genuine_token_is_rejected(w):
    user = w.users["sup_in"]
    good = mint_token(user, scope(w), "sweep_demo_area")
    assert read_token(good, user) is not None
    _payload, rest = good.split(":", 1)
    payload = signing.loads(good, salt=policy.TOKEN_SALT)
    payload["s"] = scope(w, area_ids=[w.area_in.pk, w.area_out.pk]).key()
    forged_payload = signing.b64_encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    assert read_token(f"{forged_payload}:{rest}", user) is None


def test_another_users_token_is_not_reused(w):
    token = mint_token(w.users["sup_in"], scope(w), "sweep_demo_area")
    assert read_token(token, w.users["sup_in"]) is not None
    assert read_token(token, w.users["ops_manager"]) is None
    assert read_token(token, AnonymousUser()) is None


def test_token_expires_after_its_production_day_plus_grace(w):
    tz = timezone.get_current_timezone()
    minted = datetime.combine(w.today, time(23, 30), tzinfo=tz)  # a 3rd-shift page load
    token = mint_token(w.users["sup_in"], scope(w), "sweep_demo_area", minted_at=minted)
    next_day = w.today + timedelta(days=1)
    assert read_token(token, w.users["sup_in"], now=datetime.combine(next_day, time(7, 0), tzinfo=tz))
    assert read_token(token, w.users["sup_in"], now=datetime.combine(next_day, time(8, 1), tzinfo=tz)) is None
    assert read_token(token, w.users["sup_in"], now=minted + timedelta(days=2)) is None


def test_token_never_maps_to_names(w):
    token = load_page(logged_in(w, "ops_manager"), w).context["takt_page"].token
    payload = signing.loads(token, salt=policy.TOKEN_SALT)
    assert set(payload) == {"n", "u", "v", "s", "t", "m"}
    assert all(isinstance(x, int) for x in payload["m"])  # unmapped alias pks, not hub IDs
    blob = token + repr(payload)
    for needle in [*w.names.values(), *w.hub_ids.values()]:
        assert needle not in blob


# --------------------------------------------------------------------------- operator set stability


def test_reused_token_scope_is_still_restricted_to_the_user(w):
    req = rf.get("/")
    req.user = w.users["lead_own"]
    ctx = page_context(req, OperatorScope(date_from=w.today, date_to=w.today))
    assert set(ctx.letters) == {f"o{w.ops['zeb'].operator.pk}", f"o{w.ops['ott'].operator.pk}",
                                f"a{w.ops['hub'].alias.pk}"}


def test_jobs_ingested_after_page_load_do_not_reshuffle_partial_letters(w):
    c = logged_in(w, "ops_manager")
    page = load_page(c, w)
    newcomer = f.alias(f.operator("Newcomer Xanthippe"), w.hub, "NX-999")
    f.job(w.st_a1, w.part, newcomer, timezone.now(), shift=w.shift_1st, production_date=w.today)
    partial = load_partial(c, w, token_of(page))
    keys = w.in_scope(station="st_a1")
    assert labels(partial.context["takt_page"], w, keys) == labels(page.context["takt_page"], w, keys)
    assert partial.context["takt_page"].display(newcomer).mode == policy.HIDDEN


@pytest.mark.parametrize("to", ["new_operator", "existing_operator"])
def test_alias_mapped_after_page_load_keeps_its_letter(w, to):
    c = logged_in(w, "ops_manager")
    page = load_page(c, w)
    keys = w.in_scope(station="st_a1")
    before = labels(page.context["takt_page"], w, keys)
    hub = w.ops["hub"].alias
    hub.operator = f.operator("Freshly Mapped") if to == "new_operator" else w.ops["pem"].operator
    hub.save()
    w.ops["hub"].alias = hub
    partial = load_partial(c, w, token_of(page))
    assert labels(partial.context["takt_page"], w, keys) == before
