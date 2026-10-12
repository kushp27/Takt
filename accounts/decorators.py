"""View decorators for role scope (design Component 7, design review D13).

Out of scope always renders the D13 page ("This area isn't in your assignment")
with HTTP 403 and a link back to the user's own area. Anonymous users are sent to
the login page first.

    @operator_bearing                     # login + operator-metric role + sweep registration
    @area_scope_required("area_id")       # D13 403 unless the user can view that area
    def my_view(request, area_id): ...

For class-based views wrap the callable in urls.py: operator_bearing(MyView.as_view()).
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from core.models import Area, Shift

from . import scope
from .models import Profile

OUT_OF_SCOPE_TEMPLATE = "web/out_of_scope.html"

# The attribute the identity sweep (tests/test_identity_sweep.py) looks for on every
# resolved URL callback. functools.wraps copies it through stacked decorators.
OPERATOR_BEARING_ATTR = "takt_operator_bearing"


def render_out_of_scope(request, reason="area"):
    """The D13 page with status 403. reason: 'area' | 'shift' | 'role'."""
    is_admin = scope.role_of(request.user) == Profile.ADMIN
    return render(
        request,
        OUT_OF_SCOPE_TEMPLATE,
        {"reason": reason, "is_admin_role": is_admin, "own_area_url": scope.own_area_url(request.user)},
        status=403,
    )


def role_required(*roles):
    """Allow only the given Profile roles. The builder (superuser) is always allowed.

    role_required(Profile.SUPERVISOR, Profile.OPS_MANAGER)
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(request, *args, **kwargs):
            if scope.role_of(request.user) not in (scope.BUILDER, *roles):
                return render_out_of_scope(request, reason="role")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def operator_bearing(view):
    """Mark a view as rendering operator identity, and guard it.

    - anonymous: redirect to login;
    - admin and profile-less users: D13 page, 403 (admins never see operator metrics);
    - registers the view for the identity sweep, which fails if a marked URL has no
      sweep case (or an unmarked URL renders an operator name).
    """

    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not scope.can_see_operator_metrics(request.user):
            return render_out_of_scope(request, reason="role")
        return view(request, *args, **kwargs)

    setattr(wrapped, OPERATOR_BEARING_ATTR, True)
    return wrapped


def _lookup(request, kwargs, name):
    if name is None:
        return None
    value = kwargs.get(name)
    if value is None:
        value = request.GET.get(name) or request.POST.get(name)
    return value


def area_scope_required(area_param="area_id", shift_param=None):
    """403 with the D13 page unless the user can view the area (and the shift, if given).

    Looks the parameter up in the URL kwargs, then GET, then POST. A missing area is
    out of scope (conservative). A missing shift is allowed for supervisors and up,
    but a lead must name their own shift whenever shift_param is set.
    """

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(request, *args, **kwargs):
            area_id = _lookup(request, kwargs, area_param)
            if not str(area_id or "").isdigit():
                return render_out_of_scope(request, reason="area")
            area = get_object_or_404(Area, pk=int(area_id))
            if not scope.can_view_area(request.user, area):
                return render_out_of_scope(request, reason="area")
            if shift_param:
                shift_id = _lookup(request, kwargs, shift_param)
                if shift_id in (None, ""):
                    if scope.role_of(request.user) == Profile.LEAD:
                        return render_out_of_scope(request, reason="shift")
                elif not str(shift_id).isdigit():
                    return render_out_of_scope(request, reason="shift")
                else:
                    shift = get_object_or_404(Shift, pk=int(shift_id))
                    if not scope.can_view_shift(request.user, area, shift):
                        return render_out_of_scope(request, reason="shift")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator
