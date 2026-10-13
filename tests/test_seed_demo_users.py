"""manage.py seed_demo_users (pilot demo accounts for every role)."""

from datetime import time
from io import StringIO

import pytest
from django.contrib.auth import authenticate, get_user_model
from django.core.management import call_command
from django.test import Client

from accounts import scope
from accounts.models import Profile
from tests import factories as f


def _seed(monkeypatch, password=None, *args):
    if password is None:
        monkeypatch.delenv("TAKT_DEMO_PASSWORD", raising=False)
    else:
        monkeypatch.setenv("TAKT_DEMO_PASSWORD", password)
    out, err = StringIO(), StringIO()
    call_command("seed_demo_users", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


@pytest.fixture
def plant():
    hub = f.hub()
    north, south = f.area("North"), f.area("South")
    f.line("N1", hub, north)
    f.line("S1", hub, south)
    first = f.shift(hub, "1st", time(7), time(15))
    third = f.shift(hub, "3rd", time(23), time(7))
    return north, south, first, third


def test_creates_every_role(monkeypatch, plant):
    north, south, first, third = plant
    out, err = _seed(monkeypatch)
    assert "dev-only" in err and "TAKT_DEMO_PASSWORD" in err
    User = get_user_model()
    assert User.objects.get(username="builder").is_superuser
    assert scope.role_of(User.objects.get(username="brian")) == Profile.OPS_MANAGER
    admin = User.objects.get(username="admin")
    assert admin.profile.role == Profile.ADMIN and admin.is_staff and not admin.is_superuser
    assert set(User.objects.get(username="sup-north").profile.areas.all()) == {north}
    assert set(User.objects.get(username="sup-south").profile.areas.all()) == {south}
    lead1 = User.objects.get(username="lead-north-1st").profile
    lead3 = User.objects.get(username="lead-north-3rd").profile
    assert (lead1.role, lead1.shift, set(lead1.areas.all())) == (Profile.LEAD, first, {north})
    assert (lead3.shift.start_time, lead3.shift.end_time) == (time(23), time(7))
    assert authenticate(username="lead-north-3rd", password="takt-dev-only") is not None
    assert "lead-north-3rd" in out


def test_env_password_and_idempotent(monkeypatch, plant):
    _seed(monkeypatch, "s3cret-demo")
    assert authenticate(username="brian", password="s3cret-demo")
    _seed(monkeypatch, "other")  # existing users keep their password
    assert authenticate(username="brian", password="s3cret-demo")
    _seed(monkeypatch, "other", "--reset-passwords")
    assert authenticate(username="brian", password="other")
    assert get_user_model().objects.filter(username="brian").count() == 1


def test_works_with_no_areas(monkeypatch):
    _, err = _seed(monkeypatch)
    assert "No Areas" in err
    assert get_user_model().objects.filter(username__in=["builder", "brian", "admin"]).count() == 3


def test_admin_can_map_aliases_but_not_open_jobs_or_disputes(monkeypatch, plant):
    _seed(monkeypatch)
    c = Client()
    c.force_login(get_user_model().objects.get(username="admin"))
    assert c.get("/admin/core/operatoralias/").status_code == 200
    assert c.get("/admin/core/operator/").status_code == 200
    # Registered models outside configuration: refused.
    assert c.get("/admin/accounts/profile/").status_code == 403
    assert c.get("/admin/auth/user/").status_code == 403
    assert c.get(f"/admin/core/ingestrun/add/").status_code == 403  # view-only
    # Operator data: no permission at all, whether or not the model is in the admin.
    admin = get_user_model().objects.get(username="admin")
    for perm in ("view_job", "change_job", "view_scrapevent", "change_scrapevent"):
        assert not admin.has_perm(f"core.{perm}"), perm
    for perm in ("view_dispute", "change_dispute"):
        assert not admin.has_perm(f"disputes.{perm}"), perm
    assert admin.has_perm("core.change_operatoralias") and admin.has_perm("core.change_sitesettings")
