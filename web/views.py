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
    if role in (Profile.SUPERVISOR, Profile.LEAD):
        return _home_page(request, "Your screen is not built yet.")
    return _home_page(request, "Your account has no Takt role yet. Ask the Takt builder to set one.")

