"""E-T1: operator_display policy and the {% operator %} template tags (eng re-review D2)."""

import json
import re

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import engines
from django.test import RequestFactory

from accounts import policy
from accounts.policy import HIDDEN, LETTER, NAMED, OperatorScope, operator_display, page_context
from tests.identity_world import build_world, set_gate1

rf = RequestFactory()


@pytest.fixture
def w():
    return build_world()


def ctx_for(w, role, scope=None, htmx_token=None):
    req = rf.get("/", {policy.TOKEN_PARAM: htmx_token} if htmx_token else {},
                 headers={"HX-Request": "true"} if htmx_token else {})
    req.user = w.users[role] or AnonymousUser()
    return page_context(req, scope or OperatorScope(area_ids=[w.area_in.pk]))


def show(w, role, key, ctx=None):
    ctx = ctx or ctx_for(w, role)
    return operator_display(ctx.user, w.ops[key].alias, ctx)


@pytest.mark.parametrize("gate1", [False, True])
def test_builder_always_named(w, gate1):
    set_gate1(gate1)
    d = show(w, "builder", "zeb")
    assert (d.mode, d.label) == (NAMED, "Zebulon Quixote")


@pytest.mark.parametrize("role", ["ops_manager", "sup_in", "lead_own"])
def test_scope_roles_letters_before_gate1_names_after(w, role):
    set_gate1(False)
    d = show(w, role, "zeb")
    assert d.mode == LETTER and re.fullmatch(r"Operator [A-Z]", d.label)
    assert "Zebulon" not in d.label
    set_gate1(True)
    assert show(w, role, "zeb").label == "Zebulon Quixote"


@pytest.mark.parametrize("role", ["admin", "anonymous"])
@pytest.mark.parametrize("gate1", [False, True])
def test_admin_and_anonymous_hidden(w, role, gate1):
    set_gate1(gate1)
    assert show(w, role, "zeb").mode == HIDDEN


@pytest.mark.parametrize("gate1", [False, True])
def test_out_of_scope_hidden_never_lettered(w, gate1):
    set_gate1(gate1)
    # Lead on 1st shift never sees the 3rd-shift operator, even on an all-shifts page.
    d = show(w, "lead_own", "pem")
    assert d.mode == HIDDEN and d.letter is None and "Pemberly" not in d.label
    # Supervisor of North can't see a South operator, even by asking for South in the scope.
    ctx = ctx_for(w, "sup_in", OperatorScope(area_ids=[w.area_in.pk, w.area_out.pk]))
    assert show(w, "sup_in", "mar", ctx).mode == HIDDEN
    # The letters only cover what the user may see.
    assert len(ctx.letters) == 4  # zeb, ott, pem, hub


def test_operator_outside_page_scope_hidden_even_for_builder(w):
    ctx = ctx_for(w, "builder", OperatorScope(area_ids=[w.area_in.pk]))
    assert show(w, "builder", "mar", ctx).mode == HIDDEN


def test_unmapped_alias_labels(w):
    set_gate1(False)
    d = show(w, "ops_manager", "hub")
    assert d.unmapped and re.fullmatch(r"Unmapped operator [A-Z]", d.label)
    assert "HUBQX" not in d.label
    d = show(w, "builder", "hub")
    assert d.label == "Unmapped operator (HUBQX-77)"


def test_operator_alias_and_job_resolve_to_same_identity(w):
    ctx = ctx_for(w, "ops_manager")
    zeb = w.ops["zeb"]
    job = zeb.alias.jobs.first()
    labels = {operator_display(ctx.user, x, ctx).label for x in (zeb.operator, zeb.alias, job)}
    assert len(labels) == 1


def test_no_context_or_other_users_context_is_hidden(w):
    assert operator_display(w.users["builder"], w.ops["zeb"].alias, None).mode == HIDDEN
    ctx = ctx_for(w, "ops_manager")
    assert operator_display(w.users["sup_in"], w.ops["zeb"].alias, ctx).mode == HIDDEN
    assert operator_display(w.users["builder"], object(), ctx_for(w, "builder")).mode == HIDDEN


def test_sort_key_uses_letters_not_names_in_letter_mode(w):
    set_gate1(False)
    ctx = ctx_for(w, "ops_manager")
    aliases = [w.ops[k].alias for k in ("zeb", "ott", "pem", "hub")]
    labels = [ctx.display(a).label.split()[-1] for a in ctx.sorted(aliases)]
    assert labels == sorted(labels)  # A, B, C, D
    named = ctx_for(w, "builder")
    order = [named.display(a).label for a in named.sorted(aliases)]
    assert order == ["Ottoline Brackwater", "Pemberly Vashti", "Zebulon Quixote", "Unmapped operator (HUBQX-77)"]


def test_sorted_drops_hidden_rows(w):
    ctx = ctx_for(w, "lead_own")
    kept = ctx.sorted([w.ops[k].alias for k in ("zeb", "pem", "mar")])
    assert kept == [w.ops["zeb"].alias]


# --------------------------------------------------------------------------- template tags


def render(w, role, src, **extra):
    req = rf.get("/")
    req.user = w.users[role] or AnonymousUser()
    ctx = page_context(req, OperatorScope(area_ids=[w.area_in.pk]))
    tpl = engines["django"].from_string("{% load operator %}" + src)
    return tpl.render({"takt_page": ctx, **extra}, req), ctx


def test_operator_tag(w):
    set_gate1(False)
    out, ctx = render(w, "ops_manager", "{% operator a %}", a=w.ops["zeb"].alias)
    assert out == ctx.display(w.ops["zeb"].alias).label
    out, _ = render(w, "builder", "{% operator a %}", a=w.ops["zeb"].alias)
    assert out == "Zebulon Quixote"


def test_operator_tag_escapes_names(w):
    op = w.ops["zeb"].operator
    op.name = "<script>x</script>"
    op.save()
    out, _ = render(w, "builder", "{% operator a %}", a=w.ops["zeb"].alias)
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_operator_tag_without_page_context_is_hidden(w):
    req = rf.get("/")
    req.user = w.users["builder"]
    tpl = engines["django"].from_string("{% load operator %}{% operator a %}")
    assert tpl.render({"a": w.ops["zeb"].alias}, req) == policy.HIDDEN_LABEL


def test_operator_display_and_sorted_tags(w):
    set_gate1(False)
    src = ('{% operators_sorted rows "alias" as rows %}{% for r in rows %}{% operator_display r.alias as who %}'
           '{{ who.mode }}:{{ who.unmapped|yesno:"u," }}:{{ who.label }}|{% endfor %}')
    rows = [{"alias": w.ops[k].alias} for k in ("zeb", "hub", "mar")]
    out, _ = render(w, "sup_in", src, rows=rows)
    parts = [p for p in out.split("|") if p]
    assert len(parts) == 2 and all(p.startswith("letter:") for p in parts)
    assert any(p.startswith("letter:u:Unmapped operator ") for p in parts)


def test_gate1_notice_tag(w):
    set_gate1(False)
    out, _ = render(w, "ops_manager", "{% gate1_notice %}")
    assert policy.GATE1_NOTICE in out
    out, _ = render(w, "builder", "{% gate1_notice %}")
    assert policy.GATE1_NOTICE not in out
    set_gate1(True)
    out, _ = render(w, "ops_manager", "{% gate1_notice %}")
    assert policy.GATE1_NOTICE not in out


def test_page_hx_vals_and_token_input(w):
    out, ctx = render(w, "ops_manager", "<div {% page_hx_vals %}></div>{% page_token_input %}")
    m = re.search(r'hx-vals="([^"]+)"', out)
    vals = json.loads(m.group(1).replace("&quot;", '"'))
    assert vals == {policy.TOKEN_PARAM: ctx.token}
    assert f'name="{policy.TOKEN_PARAM}" value="{ctx.token}"' in out
