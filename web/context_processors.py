"""Top nav for every page (design review screen map, Pass 1):

    Takt · Line Health · Area · My Shift · Disputes [n]   freshness ▸

Disputes [n] is the open-dispute count badge (eng review D6), shown to the roles that decide
disputes (builder, ops manager, supervisor) for the areas they can see. Admins get a link to the
configuration (the Django admin) only. No operator data here: counts and links.
"""

from django.urls import reverse
from django.utils.functional import SimpleLazyObject

from accounts import scope as role_scope
from accounts.models import Profile
from disputes.models import Dispute

DECIDER_ROLES = (role_scope.BUILDER, Profile.OPS_MANAGER, Profile.SUPERVISOR)


def open_dispute_count(user):
    if role_scope.role_of(user) not in DECIDER_ROLES:
        return None
    return Dispute.objects.filter(status=Dispute.OPEN,
                                  job__station__line__area__in=role_scope.visible_areas(user)).count()


def nav(request):
    """Lazy: htmx partials render with this processor too and never touch the nav."""
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return {}
    return {"takt_nav": SimpleLazyObject(lambda: _items(user))}


def _items(user):
    role = role_scope.role_of(user)
    items = []
    if role == Profile.ADMIN:
        items.append({"key": "admin", "label": "Configuration", "url": reverse("admin:index")})
    elif role_scope.can_see_operator_metrics(user):
        items.append({"key": "line_health", "label": "Line Health", "url": reverse("line_health")})
        items.append({"key": "area", "label": "Area", "url": reverse("area_home")})
        if role in DECIDER_ROLES:
            items.append({"key": "disputes", "label": "Disputes", "url": reverse("disputes_home"),
                          "count": open_dispute_count(user)})
    return items
