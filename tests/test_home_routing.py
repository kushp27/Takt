"""`/` sends each role to its home, and the nav's own links resolve (design Component 6, D21).

    ops manager -> Line Health      supervisor -> their Area      lead -> My Shift (their area and shift)
    admin -> /admin/                builder -> Line Health
"""

import pytest

from accounts.models import Profile
from tests import factories as f
from tests.screen_world import build, pin, t


@pytest.fixture
def p(monkeypatch):
    p = build()
    pin(monkeypatch, t(p["hub"], 12))
    return p


@pytest.mark.parametrize("role,target", [
    ("ops_manager", "/line/"),
    ("builder", "/line/"),
    ("admin", "/admin/"),
])
def test_home_redirects(p, client, role, target):
    client.force_login(p["users"][role])
    resp = client.get("/")
    assert resp.status_code == 302 and resp["Location"] == target


def test_supervisor_lands_on_their_area(p, client):
    client.force_login(p["users"]["supervisor"])
    assert client.get("/")["Location"] == f"/area/{p['area'].pk}/"


def test_lead_lands_on_my_shift_for_their_area_and_shift(p, client):
    client.force_login(p["users"]["lead"])
    assert client.get("/")["Location"] == "/shift/"
    assert client.get("/shift/")["Location"] == f"/area/{p['area'].pk}/shift/?shift_id={p['first'].pk}"


@pytest.mark.parametrize("role", [Profile.LEAD, Profile.SUPERVISOR, None])
def test_unassigned_users_get_a_plain_page(client, role):
    client.force_login(f.user("someone", role))
    resp = client.get("/")
    assert resp.status_code == 200 and "Ask the Takt builder" in resp.content.decode()


def test_nav_links_and_own_links(p, client):
    client.force_login(p["users"]["supervisor"])
    html = client.get(f"/area/{p['area'].pk}/").content.decode()
    for label, href in [("Line Health", "/line/"), ("Area", "/area/"), ("My Shift", "/shift/"),
                        ("Disputes", "/disputes/")]:
        assert f'<a href="{href}"' in html and f">{label}" in html
    assert '<a href="/area/" aria-current="page">Area' in html
    assert client.get("/area/")["Location"] == f"/area/{p['area'].pk}/"
    assert client.get("/disputes/")["Location"] == f"/area/{p['area'].pk}/disputes/"
    assert client.get("/shift/").status_code == 302
    client.force_login(p["users"]["admin"])
    html = client.get("/login/").content.decode()
    assert "Line Health" not in html


def test_d13_back_to_your_area_uses_the_area_url(p, client):
    client.force_login(p["users"]["lead"])
    resp = client.get(f"/area/{p['area'].pk}/shift/?shift_id={p['second'].pk}")
    assert resp.status_code == 403
    assert f'<a href="/area/{p["area"].pk}/">Back to your area</a>' in resp.content.decode()
