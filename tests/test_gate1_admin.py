"""Gate 1 switch: only the builder (superuser) flips it; who and when are stamped."""

from io import StringIO

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import Client

from core.models import SiteSettings


@pytest.fixture(autouse=True)
def _seeded(monkeypatch):
    monkeypatch.setenv("TAKT_DEMO_PASSWORD", "x-demo")
    call_command("seed_demo_users", stdout=StringIO(), stderr=StringIO())


def _form(s, **over):
    fields = ["grade_green_max_share", "grade_red_min_share", "duration_short_ratio", "duration_long_ratio",
              "end_of_shift_minutes", "clustered_timestamp_others", "grade_window_days",
              "count_dispute_revoke_share", "count_dispute_revoke_days", "stale_after_minutes",
              "upstream_gap_minutes", "tenure_flag_days"]
    data = {f: getattr(s, f) for f in fields}
    data.update(over)
    return data


def _client(username):
    c = Client()
    c.force_login(get_user_model().objects.get(username=username))
    return c


def _url(s):
    return f"/admin/core/sitesettings/{s.pk}/change/"


def test_admin_group_cannot_flip_gate1_but_can_tune_thresholds():
    s = SiteSettings.load()
    c = _client("admin")
    assert 'name="gate1_approved"' not in c.get(_url(s)).content.decode()  # read-only
    resp = c.post(_url(s), _form(s, gate1_approved="on", gate1_approved_by="admin", grade_window_days=21))
    assert resp.status_code == 302
    s.refresh_from_db()
    assert s.gate1_approved is False and s.gate1_approved_by == "" and s.gate1_approved_at is None
    assert s.grade_window_days == 21


def test_builder_flips_gate1_and_it_is_stamped():
    s = SiteSettings.load()
    c = _client("builder")
    page = c.get(_url(s)).content.decode()
    assert 'name="gate1_approved"' in page and 'name="gate1_approved_by"' not in page
    assert c.post(_url(s), _form(s, gate1_approved="on", gate1_approved_by="someone else")).status_code == 302
    s.refresh_from_db()
    assert s.gate1_approved and s.gate1_approved_by == "builder" and s.gate1_approved_at is not None
    stamped_at = s.gate1_approved_at
    # Saving other fields without touching the flag keeps the stamp.
    c.post(_url(s), _form(s, gate1_approved="on", grade_window_days=27))
    s.refresh_from_db()
    assert s.gate1_approved_at == stamped_at and s.grade_window_days == 27
