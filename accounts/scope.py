"""Role scope (design Component 7).

| Role        | Scope                                   |
|-------------|-----------------------------------------|
| builder     | Django superuser; everything             |
| ops_manager | all areas, all shifts                    |
| supervisor  | Profile.areas, all shifts                |
| lead        | Profile.areas, Profile.shift only        |
| admin       | configuration only: no areas, no operator metrics |

Every helper here is conservative: an anonymous user, a user without a Profile,
or a lead without a shift sees nothing.
"""

from django.conf import settings
from django.urls import NoReverseMatch, reverse

from core.models import Area, Line, Shift

from .models import Profile

BUILDER = "builder"  # pseudo-role: Django superuser (user clarification: builder = superuser)
OPERATOR_METRIC_ROLES = (Profile.OPS_MANAGER, Profile.SUPERVISOR, Profile.LEAD)


def _profile(user):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    try:
        return user.profile
    except Profile.DoesNotExist:
        return None


def is_builder(user):
    return bool(getattr(user, "is_authenticated", False) and user.is_active and user.is_superuser)


def role_of(user):
    """'builder', a Profile role, or None (anonymous, inactive or no Profile)."""
    if is_builder(user):
        return BUILDER
    p = _profile(user)
    return p.role if p else None


def can_see_operator_metrics(user):
    """Builder, ops manager, supervisor, lead. Never admin, anonymous or profile-less users."""
    return role_of(user) in (BUILDER, *OPERATOR_METRIC_ROLES)


def visible_areas(user):
    role = role_of(user)
    if role in (BUILDER, Profile.OPS_MANAGER):
        return Area.objects.all()
    if role in (Profile.SUPERVISOR, Profile.LEAD):
        return user.profile.areas.all()
    return Area.objects.none()  # admin, anonymous, no profile


def visible_area_ids(user):
    return set(visible_areas(user).values_list("pk", flat=True))


def visible_lines(user):
    return Line.objects.filter(area__in=visible_areas(user))


def visible_shifts(user, area=None):
    """Shifts the user may view, optionally limited to the shifts of one area's hubs.

    Shifts belong to a hub; an area's shifts are those of the hubs its lines run on.
    A lead sees only Profile.shift (none if unset).
    """
    role = role_of(user)
    if role not in (BUILDER, *OPERATOR_METRIC_ROLES):
        return Shift.objects.none()
    qs = Shift.objects.all()
    if area is not None:
        if not can_view_area(user, area):
            return Shift.objects.none()
        qs = qs.filter(hub__lines__area=area).distinct()
    elif role == Profile.SUPERVISOR:
        qs = qs.filter(hub__lines__area__in=visible_areas(user)).distinct()
    if role == Profile.LEAD:
        shift_id = user.profile.shift_id
        return qs.filter(pk=shift_id) if shift_id else Shift.objects.none()
    return qs


def _pk(obj):
    return getattr(obj, "pk", obj)


def can_view_area(user, area):
    """area: an Area or its pk."""
    if area is None:
        return False
    return _pk(area) in visible_area_ids(user)


def can_view_shift(user, area, shift):
    """True when the user may view `shift` in `area`. Leads: only their own shift."""
    if not can_view_area(user, area) or shift is None:
        return False
    if role_of(user) == Profile.LEAD:
        return user.profile.shift_id is not None and _pk(shift) == user.profile.shift_id
    return True


def lead_assignment(user):
    """(area, shift) for a lead, or None. A lead with several areas gets the first by name."""
    if role_of(user) != Profile.LEAD:
        return None
    area = user.profile.areas.order_by("name").first()
    shift = user.profile.shift
    if area is None or shift is None:
        return None
    return area, shift


def own_area(user):
    """The area a user's 'back to your area' link goes to (D13), or None."""
    role = role_of(user)
    if role in (Profile.SUPERVISOR, Profile.LEAD):
        return user.profile.areas.order_by("name").first()
    if role in (BUILDER, Profile.OPS_MANAGER):
        return Area.objects.order_by("name").first()
    return None


def own_area_url(user):
    """URL for the D13 link back to the user's own area.

    Lane D: name the area screen's URL 'area' with an `area_id` kwarg, or set
    settings.TAKT_AREA_URL_NAME. Admins go to the Django admin; anything that can't
    be reversed falls back to '/'.
    """
    if role_of(user) == Profile.ADMIN:
        return reverse("admin:index")
    area = own_area(user)
    if area is not None:
        try:
            return reverse(getattr(settings, "TAKT_AREA_URL_NAME", "area"), kwargs={"area_id": area.pk})
        except NoReverseMatch:
            pass
    return "/"
