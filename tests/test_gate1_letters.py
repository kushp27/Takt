"""E-T2: Gate 1 page tokens and HMAC letters (eng re-review D3, design review D12)."""

import re
from datetime import timedelta

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


@pytest.fixture
def w():
    set_gate1(False)
    return build_world()


def logged_in(w, role):
    c = Client()
    c.force_login(w.users[role])
    return c


def page_and_partial(w, role, token="from-page"):
    c = logged_in(w, role)
    page = c.get(f"/_sweep-demo/area/{w.area_in.pk}/")
    sent = TOKEN_RE.search(page.content.decode()).group(1) if token == "from-page" else token
    partial = c.get(f"/_sweep-demo/station/{w.st_a1.pk}/", {policy.TOKEN_PARAM: sent} if sent else {},
                    headers={"HX-Request": "true"})
    return page, partial


def labels(ctx, w, keys):
    return {k: ctx.display(w.ops[k].alias).label for k in keys}


@pytest.mark.parametrize("role", ["ops_manager", "sup_in", "lead_own"])
def test_parent_and_partial_letters_agree_on_a_subset(w, role):
    page, partial = page_and_partial(w, role)
    pctx, sctx = page.context["takt_page"], partial.context["takt_page"]
    assert sctx.reused and sctx.nonce == pctx.nonce
    station_keys = w.in_scope(station="st_a1")  # zeb, pem, hub: a subset of the page's operators
    assert labels(sctx, w, station_keys) == labels(pctx, w, station_keys)
    # And in the HTML: every letter in the partial is the same string on the page.
    assert set(LETTER_RE.findall(partial.content.decode())) <= set(LETTER_RE.findall(page.content.decode()))


def test_two_page_loads_get_different_tokens(w):
    c = logged_in(w, "ops_manager")
    a = c.get(f"/_sweep-demo/area/{w.area_in.pk}/").context["takt_page"]
    b = c.get(f"/_sweep-demo/area/{w.area_in.pk}/").context["takt_page"]
    assert a.token != b.token and a.nonce != b.nonce


def test_full_page_load_ignores_a_sent_token(w):
    c = logged_in(w, "ops_manager")
    first = c.get(f"/_sweep-demo/area/{w.area_in.pk}/").context["takt_page"]
    again = c.get(f"/_sweep-demo/area/{w.area_in.pk}/", {policy.TOKEN_PARAM: first.token}).context["takt_page"]
    assert not again.reused and again.nonce != first.nonce


def _ctx(w, nonce, role="ops_manager"):
    return PageContext(w.users[role], OperatorScope(area_ids=[w.area_in.pk]), nonce, timezone.now(), "t")


def test_letters_are_deterministic_per_token_and_reshuffle_across_tokens(w):
    for i in range(4):  # a bigger crew so orderings have room to differ
        op = f.operator(f"Extra Crewmember {i}")
        f.job(w.st_a2, w.part, f.alias(op, w.hub), timezone.now() - timedelta(hours=1),
              shift=w.shift_1st, production_date=w.today)
    assert _ctx(w, "fixed-1").letters == _ctx(w, "fixed-1").letters
    orderings = {tuple(sorted(_ctx(w, f"fixed-{i}").letters.items())) for i in range(20)}
    assert len(orderings) > 1
    # Letters are a permutation of A.. over exactly the scope's operators.
    letters = _ctx(w, "fixed-1").letters
    assert sorted(letters.values()) == [chr(65 + i) for i in range(len(letters))]


def test_letter_sequence_past_z():
    assert [policy._letters(i) for i in (0, 25, 26, 27, 51, 52)] == ["A", "Z", "AA", "AB", "AZ", "BA"]


@pytest.mark.parametrize("bad", ["", "garbage", "eyJuIjoiZm9yZ2VkIn0:forged:sig"])
def test_forged_or_missing_token_mints_fresh_and_reveals_no_names(w, bad):
    page, partial = page_and_partial(w, "ops_manager", token=bad)
    sctx = partial.context["takt_page"]
    assert not sctx.reused and sctx.nonce != page.context["takt_page"].nonce
    body = partial.content.decode()
    assert not any(n in body for n in w.names.values())
    assert LETTER_RE.search(body)


def test_tampered_scope_in_token_is_rejected(w):
    user = w.users["sup_in"]
    good = mint_token(user, OperatorScope(area_ids=[w.area_in.pk]))
    payload = signing.loads(good, salt=policy.TOKEN_SALT)
    payload["s"] = OperatorScope(area_ids=[w.area_out.pk]).key()
    forged = signing.dumps(payload, salt="wrong-salt", compress=True)
    assert read_token(forged, user) is None


def test_another_users_token_is_not_reused(w):
    token = mint_token(w.users["sup_in"], OperatorScope(area_ids=[w.area_in.pk]))
    assert read_token(token, w.users["sup_in"]) is not None
    assert read_token(token, w.users["ops_manager"]) is None
    assert read_token(token, AnonymousUser()) is None


def test_expired_token_is_not_reused(w, monkeypatch):
    token = mint_token(w.users["sup_in"], OperatorScope(area_ids=[w.area_in.pk]))
    monkeypatch.setattr(policy, "TOKEN_MAX_AGE", timedelta(seconds=-1))
    assert read_token(token, w.users["sup_in"]) is None


def test_token_never_maps_to_names(w):
    page, _ = page_and_partial(w, "ops_manager")
    token = page.context["takt_page"].token
    payload = signing.loads(token, salt=policy.TOKEN_SALT)
    assert set(payload) == {"n", "u", "s", "t"}
    blob = token + repr(payload)
    for needle in [*w.names.values(), *w.hub_ids.values()]:
        assert needle not in blob


def test_reused_token_scope_is_still_restricted_to_the_user(w):
    """A token minted for an all-areas scope still letters only what the user may see."""
    req = rf.get("/")
    req.user = w.users["lead_own"]
    ctx = page_context(req, OperatorScope())
    assert set(ctx.letters) == {f"o{w.ops['zeb'].operator.pk}", f"o{w.ops['ott'].operator.pk}",
                                f"a{w.ops['hub'].alias.pk}"}


def test_jobs_ingested_after_page_load_do_not_reshuffle_partial_letters(w):
    c = logged_in(w, "ops_manager")
    page = c.get(f"/_sweep-demo/area/{w.area_in.pk}/")
    token = TOKEN_RE.search(page.content.decode()).group(1)
    newcomer = f.alias(f.operator("Newcomer Xanthippe"), w.hub, "NX-999")
    f.job(w.st_a1, w.part, newcomer, timezone.now(), shift=w.shift_1st, production_date=w.today)
    partial = c.get(f"/_sweep-demo/station/{w.st_a1.pk}/", {policy.TOKEN_PARAM: token}, headers={"HX-Request": "true"})
    keys = w.in_scope(station="st_a1")
    assert labels(partial.context["takt_page"], w, keys) == labels(page.context["takt_page"], w, keys)
    assert partial.context["takt_page"].display(newcomer).mode == policy.HIDDEN
