"""Create pilot demo accounts for every role. DEV/DEMO ONLY.

    manage.py seed_demo_users [--reset-passwords]

Works with whatever Areas and Shifts exist:
- builder: Django superuser (sees named operators even before Gate 1)
- brian: ops manager (all areas)
- sup-<area>: one supervisor per area
- lead-<area>-<shift>: a lead for every shift that runs on the area's lines
  (so the 1st and the 3rd 23:00-07:00 shift each get one when they exist)
- admin: configuration only (the "Takt admin" group: config models in the Django
  admin, including operators and aliases for mapping; no jobs, scrap or disputes)

Password: $TAKT_DEMO_PASSWORD, or a printed dev-only default. Existing users keep
their password unless --reset-passwords is given.
"""

import os

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand
from django.utils.text import slugify

from accounts.models import Profile
from core.models import Area, Shift

DEV_PASSWORD = "takt-dev-only"
ADMIN_GROUP = "Takt admin"

# Configuration models the admin role manages, plus station-level grades and ingest
# runs (view only). Deliberately excludes Job and ScrapEvent (per-operator and
# transaction data), disputes and user accounts: the admin role never sees operator
# metrics (design Component 7). Operators and aliases are included for mapping only.
ADMIN_MODELS = {
    "core": ["hub", "area", "line", "station", "counttrustchange", "partfamily", "part", "routing",
             "operator", "operatoralias", "shift", "shiftbreak", "nonworkingday", "taktconfig",
             "scrapcode", "stationpartgrade", "ingestrun", "sitesettings"],
}
ADMIN_VIEW_ONLY = {"counttrustchange", "stationpartgrade", "ingestrun"}


class Command(BaseCommand):
    help = "Create pilot demo accounts for every role (dev/demo only)."

    def add_arguments(self, parser):
        parser.add_argument("--reset-passwords", action="store_true",
                            help="Also reset the password of users that already exist.")

    def handle(self, *args, reset_passwords=False, **opts):
        password = os.environ.get("TAKT_DEMO_PASSWORD")
        if not password:
            password = DEV_PASSWORD
            self.stderr.write(self.style.WARNING(
                f"WARNING: TAKT_DEMO_PASSWORD is not set; using the dev-only password '{DEV_PASSWORD}'. "
                "These are DEV/DEMO accounts. Never run this on a production VM."
            ))
        self.password, self.reset = password, reset_passwords
        self.rows = []

        self._user("builder", None, superuser=True)
        self._user("brian", Profile.OPS_MANAGER)
        admin = self._user("admin", Profile.ADMIN, staff=True)
        admin.groups.add(self._admin_group())

        areas = list(Area.objects.order_by("name"))
        if not areas:
            self.stderr.write(self.style.WARNING("No Areas exist yet: no supervisors or leads created."))
        for area in areas:
            slug = slugify(area.name) or f"area{area.pk}"
            self._user(f"sup-{slug}", Profile.SUPERVISOR, areas=[area])
            shifts = Shift.objects.filter(hub__lines__area=area).distinct().order_by("hub", "start_time")
            if not shifts:
                self.stderr.write(self.style.WARNING(f"Area {area.name}: no shifts on its lines' hubs; no leads."))
            for shift in shifts:
                self._user(f"lead-{slug}-{slugify(shift.name) or shift.pk}", Profile.LEAD, areas=[area], shift=shift)

        width = max(len(r[0]) for r in self.rows)
        self.stdout.write("Demo accounts:")
        for username, role, scope, created in self.rows:
            self.stdout.write(f"  {username:<{width}}  {role:<11}  {scope}{'' if created else '  (existed)'}")
        if password == DEV_PASSWORD:
            self.stdout.write(f"Password for new accounts: {DEV_PASSWORD} (DEV ONLY)")

    def _user(self, username, role, areas=(), shift=None, superuser=False, staff=False):
        User = get_user_model()
        user, created = User.objects.get_or_create(username=username)
        user.is_superuser = superuser
        user.is_staff = superuser or staff
        if created or self.reset:
            user.set_password(self.password)
        user.save()
        if role:
            profile, _ = Profile.objects.update_or_create(user=user, defaults={"role": role, "shift": shift})
            profile.areas.set(areas)
        else:
            Profile.objects.filter(user=user).delete()
        scope = ", ".join(a.name for a in areas) or ("all areas" if role in (None, Profile.OPS_MANAGER) else "-")
        if shift:
            scope += f" / {shift.name} shift ({shift.start_time:%H:%M}-{shift.end_time:%H:%M})"
        self.rows.append((username, role or "builder", scope, created))
        return user

    def _admin_group(self):
        group, _ = Group.objects.get_or_create(name=ADMIN_GROUP)
        perms = []
        for app, models in ADMIN_MODELS.items():
            for model in models:
                actions = ["view"] if model in ADMIN_VIEW_ONLY else ["view", "add", "change"]
                perms += Permission.objects.filter(
                    content_type__app_label=app, codename__in=[f"{a}_{model}" for a in actions]
                )
        group.permissions.set(perms)
        return group
