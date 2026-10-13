"""Home routing and the nav's "own" links.

`/` sends each role to its home (design Component 6, D21):
    builder (superuser) -> Line Health        ops manager -> Line Health
    supervisor -> their Area                  lead -> My Shift for their area and shift
    admin -> /admin/ (configuration only; never operator metrics)
A user without an assignment (no Profile, a lead with no area or shift) gets a plain page saying so.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from accounts import scope as role_scope
from accounts.models import Profile


def _home_page(request, message):
    return render(request, "web/home.html", {"message": message})


@login_required
def home(request):
    role = role_scope.role_of(request.user)
    if role in (role_scope.BUILDER, Profile.OPS_MANAGER):
        return redirect("line_health")
    if role == Profile.ADMIN:
        return redirect("admin:index")
    if role == Profile.SUPERVISOR:
        area = role_scope.own_area(request.user)
        if area is not None:
            return redirect("area", area_id=area.pk)
        return _home_page(request, "No area is assigned to you yet. Ask the Takt builder to add one.")
    if role == Profile.LEAD:
        if role_scope.lead_assignment(request.user) is not None:
            return redirect("my_shift_home")
        return _home_page(request, "No area and shift are assigned to you yet. Ask the Takt builder to add them.")
    return _home_page(request, "Your account has no Takt role yet. Ask the Takt builder to set one.")


@login_required
def area_home(request):
    """/area/: the user's own area (the D13 "back to your area" target)."""
    return redirect(role_scope.own_area_url(request.user))


@login_required
def disputes_home(request):
    """/disputes/: the dispute queue for the user's own area."""
    area = role_scope.own_area(request.user)
    if area is None or role_scope.role_of(request.user) not in (role_scope.BUILDER, Profile.OPS_MANAGER,
                                                               Profile.SUPERVISOR):
        return redirect("home")
    return redirect("dispute_queue", area_id=area.pk)
