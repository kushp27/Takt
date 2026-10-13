"""Role scope helpers and decorators (design Component 7, design review D13)."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory

from accounts import scope
from accounts.decorators import area_scope_required, operator_bearing, role_required
from accounts.models import Profile
from tests import factories as f
from tests.identity_world import build_world


@pytest.fixture
def w():
    return build_world()


def U(w, role):
    return w.users[role] or AnonymousUser()


def test_roles(w):
    assert scope.role_of(U(w, "builder")) == scope.BUILDER
    assert scope.role_of(U(w, "ops_manager")) == Profile.OPS_MANAGER
    assert scope.role_of(U(w, "admin")) == Profile.ADMIN
    assert scope.role_of(AnonymousUser()) is None
    assert scope.role_of(f.user("nobody", None)) is None


@pytest.mark.parametrize("role,expected", [
    ("builder", True), ("ops_manager", True), ("sup_in", True), ("lead_own", True),
    ("admin", False), ("anonymous", False),
])
def test_can_see_operator_metrics(w, role, expected):
    assert scope.can_see_operator_metrics(U(w, role)) is expected


def test_profile_less_and_inactive_users_see_nothing(w):
    nobody = f.user("nobody", None)
    assert not scope.can_see_operator_metrics(nobody)
    assert not scope.visible_areas(nobody).exists()
    lead = w.users["lead_own"]
    lead.is_active = False
    assert not scope.can_view_area(lead, w.area_in)


@pytest.mark.parametrize("role,areas", [
    ("builder", {"area_in", "area_out"}), ("ops_manager", {"area_in", "area_out"}),
    ("sup_in", {"area_in"}), ("sup_out", {"area_out"}), ("lead_own", {"area_in"}),
    ("admin", set()), ("anonymous", set()),
])
def test_visible_areas_and_lines(w, role, areas):
    user = U(w, role)
    assert set(scope.visible_areas(user)) == {w[a] for a in areas}
    assert set(scope.visible_lines(user)) == {w[a].lines.get() for a in areas}
    for a in ("area_in", "area_out"):
        assert scope.can_view_area(user, w[a]) is (a in areas)
        assert scope.can_view_area(user, w[a].pk) is (a in areas)


def test_can_view_shift(w):
    for role in ("ops_manager", "sup_in"):
        assert scope.can_view_shift(w.users[role], w.area_in, w.shift_3rd)
    assert not scope.can_view_shift(w.users["sup_out"], w.area_in, w.shift_1st)
    assert scope.can_view_shift(w.users["lead_own"], w.area_in, w.shift_1st)
    assert not scope.can_view_shift(w.users["lead_own"], w.area_in, w.shift_3rd)
    assert scope.can_view_shift(w.users["lead_other"], w.area_in, w.shift_3rd)
    assert not scope.can_view_shift(w.users["lead_own"], w.area_out, w.shift_1st)
    assert not scope.can_view_shift(w.users["admin"], w.area_in, w.shift_1st)


def test_visible_shifts(w):
    assert set(scope.visible_shifts(w.users["sup_in"], w.area_in)) == {w.shift_1st, w.shift_3rd}
    assert set(scope.visible_shifts(w.users["lead_own"])) == {w.shift_1st}
    assert set(scope.visible_shifts(w.users["lead_other"], w.area_in)) == {w.shift_3rd}
    assert not scope.visible_shifts(w.users["sup_out"], w.area_in).exists()
    assert not scope.visible_shifts(w.users["admin"]).exists()


def test_lead_without_shift_sees_no_shift(w):
    lead = f.user("lead-noshift", Profile.LEAD, areas=[w.area_in])
    assert not scope.visible_shifts(lead).exists()
    assert not scope.can_view_shift(lead, w.area_in, w.shift_1st)
    assert scope.lead_assignment(lead) is None


def test_lead_assignment(w):
    assert scope.lead_assignment(w.users["lead_other"]) == (w.area_in, w.shift_3rd)
    assert scope.lead_assignment(w.users["sup_in"]) is None


def test_own_area_url_points_at_the_area_screen(w):
    """Lane D named the area screen 'area' (kwarg area_id): the D13 link goes there."""
    assert scope.own_area_url(w.users["sup_in"]) == f"/area/{w.area_in.pk}/"
    assert scope.own_area_url(w.users["lead_own"]) == f"/area/{w.area_in.pk}/"
    assert scope.own_area_url(w.users["admin"]) == "/admin/"


def test_own_area_url_falls_back_without_an_area_url(w, settings):
    settings.TAKT_AREA_URL_NAME = "no_such_url"
    assert scope.own_area_url(w.users["sup_in"]) == "/"
    assert scope.own_area_url(w.users["admin"]) == "/admin/"


# --------------------------------------------------------------------------- decorators

rf = RequestFactory()


def _req(user, path="/x/", **get):
    r = rf.get(path, get)
    r.user = user or AnonymousUser()
    return r


@area_scope_required("area_id", shift_param="shift_id")
def _shift_view(request, area_id, shift_id=None):
    return HttpResponse("ok")


def test_area_scope_out_of_area_renders_d13_403(w):
    resp = _shift_view(_req(w.users["sup_out"]), area_id=w.area_in.pk)
    assert resp.status_code == 403
    assert b"This area isn't in your assignment" in resp.content
    assert b"Back to your area" in resp.content


def test_area_scope_lead_other_shift_is_403(w):
    assert _shift_view(_req(w.users["lead_own"]), area_id=w.area_in.pk, shift_id=w.shift_1st.pk).status_code == 200
    resp = _shift_view(_req(w.users["lead_own"]), area_id=w.area_in.pk, shift_id=w.shift_3rd.pk)
    assert resp.status_code == 403
    # A lead must name their shift on a shift-bound view.
    assert _shift_view(_req(w.users["lead_own"]), area_id=w.area_in.pk).status_code == 403
    # Supervisors may omit it (all shifts).
    assert _shift_view(_req(w.users["sup_in"]), area_id=w.area_in.pk).status_code == 200


def test_area_scope_reads_get_params_and_rejects_garbage(w):
    assert _shift_view(_req(w.users["sup_in"], area_id=str(w.area_in.pk)), area_id=None).status_code == 200
    assert _shift_view(_req(w.users["sup_in"]), area_id="abc").status_code == 403


def test_area_scope_anonymous_redirects_to_login(w):
    resp = _shift_view(_req(None), area_id=w.area_in.pk)
    assert resp.status_code == 302 and "/login/" in resp["Location"]


@operator_bearing
def _op_view(request):
    return HttpResponse("ok")


def test_operator_bearing_blocks_admin_and_marks_view(w):
    assert getattr(_op_view, "takt_operator_bearing") is True
    assert _op_view(_req(w.users["lead_own"])).status_code == 200
    resp = _op_view(_req(w.users["admin"]))
    assert resp.status_code == 403
    assert b"Operator views aren't part of it" in resp.content
    assert _op_view(_req(None)).status_code == 302


def test_marker_survives_stacked_decorators():
    stacked = area_scope_required("area_id")(operator_bearing(lambda r: HttpResponse()))
    assert getattr(stacked, "takt_operator_bearing", False)


@role_required(Profile.SUPERVISOR)
def _sup_view(request):
    return HttpResponse("ok")


def test_role_required(w):
    assert _sup_view(_req(w.users["sup_in"])).status_code == 200
    assert _sup_view(_req(w.users["builder"])).status_code == 200
    assert _sup_view(_req(w.users["lead_own"])).status_code == 403
    assert _sup_view(_req(w.users["admin"])).status_code == 403


def test_unknown_area_or_shift_is_the_same_d13_403(w):
    resp = _shift_view(_req(w.users["sup_in"]), area_id=999999)
    assert resp.status_code == 403 and b"This area isn't in your assignment" in resp.content
    resp = _shift_view(_req(w.users["sup_in"]), area_id=w.area_in.pk, shift_id=999999)
    assert resp.status_code == 403


def test_htmx_refusal_is_an_inline_d13_fragment(w):
    r = rf.get("/x/", headers={"HX-Request": "true"})
    r.user = w.users["sup_out"]
    resp = _shift_view(r, area_id=w.area_in.pk)
    assert resp.status_code == 403
    body = resp.content.decode()
    assert "This area isn't in your assignment" in body and "Back to your area" in body
    assert "<html" not in body and 'role="alert"' in body


def test_base_template_swaps_403_fragments_and_shows_load_errors(client, w):
    client.force_login(w.users["sup_in"])
    body = client.get("/", follow=True).content.decode()  # / -> the supervisor's Area
    assert "htmx:beforeSwap" in body and "status === 403" in body and 'getResponseHeader("X-Takt-Inline")' in body
    assert "htmx:responseError" in body and "Couldn't load stations." in body and "Retry" in body


def test_inline_403_carries_the_swap_marker_and_full_page_does_not(w):
    r = _req(w.users["sup_out"])
    r.META["HTTP_HX_REQUEST"] = "true"
    assert _shift_view(r, area_id=w.area_in.pk)["X-Takt-Inline"] == "out-of-scope"


def test_rejected_token_rolls_back_writes_made_before_page_context(w):
    from accounts.policy import PageTokenRejected
    from core.models import Area

    @operator_bearing
    def writes_then_rejects(request):
        Area.objects.create(name="written-before-reject")
        raise PageTokenRejected()

    resp = writes_then_rejects(_req(w.users["sup_in"]))
    assert resp["HX-Refresh"] == "true"
    assert not Area.objects.filter(name="written-before-reject").exists()
