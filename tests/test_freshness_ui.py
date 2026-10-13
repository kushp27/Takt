"""Header freshness, stale banner (D9), stale tag helpers, the live notice (E-T4), the print header tag
(E-T5) and the database-unreachable page (D13)."""

import re
from pathlib import Path
from datetime import datetime, time, timedelta
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import pytest
from django.db import OperationalError, connections
from django.template import Context, RequestContext, Template

from accounts.models import Profile
from ops import freshness
from tests import factories as f


@pytest.fixture(autouse=True)
def _plain_static(settings):
    # The test image has no collectstatic manifest; base.html uses {% static %}.
    settings.STORAGES = {**settings.STORAGES,
                         "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}}


@pytest.fixture
def clock(monkeypatch):
    def set_now(instant):
        monkeypatch.setattr("ops.freshness.current_time", lambda: instant)

    return set_now


@pytest.fixture
def lead_client(client):
    client.force_login(f.user("lead1", Profile.LEAD))
    return client


def _two_hubs(now_hm=(10, 0), dies_quiet_minutes=50):
    cores = f.hub("cores")
    dies = f.hub("dies")
    for h in (cores, dies):
        f.shift(h, "1st", time(7), time(15))
    now = f.local(cores, 2026, 10, 12, *now_hm)
    cores.last_successful_ingest_at = f.local(cores, 2026, 10, 12, 9, 57)
    cores.last_new_transaction_at = f.local(cores, 2026, 10, 12, 9, 55)
    cores.save()
    dies.last_new_transaction_at = now - timedelta(minutes=dies_quiet_minutes)
    dies.last_successful_ingest_at = f.local(dies, 2026, 10, 12, 9, 58)
    dies.save()
    return cores, dies, now


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


def test_header_shows_each_hubs_data_time(lead_client, clock):
    cores, dies, now = _two_hubs(dies_quiet_minutes=10)
    clock(now)
    html = lead_client.get("/").content.decode()
    assert "Cores Hub data as of 9:57 AM · Dies Hub as of 9:58 AM" in _text(html)
    assert "stale-banner" not in html


def test_stale_banner_names_the_hub_and_its_last_good_time(lead_client, clock):
    cores, dies, now = _two_hubs(dies_quiet_minutes=200)  # dies last tx 6:40 AM
    clock(now)
    html = lead_client.get("/").content.decode()
    text = _text(html)
    banner = re.search(r'<section class="stale-banner"[^>]*>(.*?)</section>', html, re.S).group(1)
    assert _text(banner) == "Dies Hub data stopped at 6:40 AM; numbers for Dies lines may be out of date"
    assert "Cores Hub data stopped" not in text
    # header tags the stale hub with a word, not colour alone
    assert re.search(r'freshness-hub is-stale">Dies Hub as of .*?<span class="stale-tag">stale</span>', html)


def test_banner_and_header_hidden_for_anonymous_login_page(client, clock):
    cores, dies, now = _two_hubs()
    clock(now)
    html = client.get("/login/").content.decode()
    assert "stale-banner" not in html and "data as of" not in html


def test_stale_tag_and_class_helpers(clock):
    cores, dies, now = _two_hubs()
    clock(now)
    snap = freshness.snapshot()
    t = Template('{% load takt_freshness %}<tr class="{% stale_class h %}"><td>{% stale_tag h %}</td></tr>')
    stale = t.render(Context({"freshness": snap, "h": dies}))
    fresh = t.render(Context({"freshness": snap, "h": "cores"}))
    assert 'class="is-stale"' in stale and ">stale</span>" in stale
    assert 'class=""' in fresh and "stale-tag" not in fresh
    # Works without the context processor too (computes on demand).
    alone = Template("{% load takt_freshness %}{% hub_freshness 'dies' as x %}{{ x.stale }}").render(Context({}))
    assert alone == "True"


def test_print_header_tag_fresh_and_stale(clock):
    cores, dies, now = _two_hubs(dies_quiet_minutes=200)
    clock(now)
    t = Template("{% load takt_freshness %}{% print_header h %}")
    fresh = _text(t.render(Context({"h": cores})))
    stale = _text(t.render(Context({"h": dies})))
    assert "Data as of 9:57 AM · Printed 10:00 AM" in fresh and "STALE" not in fresh
    assert stale.startswith("STALE: data stopped at 6:40 AM")
    assert "Data as of 9:58 AM" in stale


# E-T4: live My Shift notice.

def _poller(hub, **kw):
    args = " ".join(f'{k}="{v}"' for k, v in kw.items())
    html = Template("{% load takt_freshness %}{% freshness_notice h " + args + " %}").render(Context({"h": hub}))
    return html, re.search(r'hx-get="([^"]+)"', html).group(1).replace("&amp;", "&")


def test_notice_container_polls_every_5_minutes_in_a_polite_region():
    cores, dies, now = _two_hubs()
    html, url = _poller(cores, refresh_url="/shift/", refresh_target="#shift-table")
    assert 'role="status"' in html and 'aria-live="polite"' in html
    assert 'hx-trigger="every 300s"' in html and 'hx-swap="outerHTML"' in html
    q = parse_qs(urlsplit(url).query)
    assert urlsplit(url).path == "/freshness/notice"
    assert q["hub"] == ["cores"] and q["refresh_url"] == ["/shift/"] and q["refresh_target"] == ["#shift-table"]
    assert q["since"] == [cores.last_new_transaction_at.isoformat()]


def test_notice_is_204_without_newer_data(lead_client):
    cores, dies, now = _two_hubs()
    _, url = _poller(cores)
    assert lead_client.get(url).status_code == 204


def test_notice_shows_new_data_with_refresh_after_ingest(lead_client, clock):
    cores, dies, now = _two_hubs()
    clock(f.local(cores, 2026, 10, 12, 15, 0))  # same local day as the data, not the real clock
    _, url = _poller(cores, refresh_url="/shift/", refresh_target="#shift-table")
    cores.last_new_transaction_at = f.local(cores, 2026, 10, 12, 14, 45)
    cores.save()
    resp = lead_client.get(url)
    assert resp.status_code == 200
    html = resp.content.decode()
    assert _text(html) == "New data from 2:45 PM · Refresh"
    assert 'href="/shift/"' in html and 'hx-target="#shift-table"' in html and "data-takt-refresh" in html
    assert "hx-trigger" not in html  # polling stops: one announcement per notice


def test_notice_since_with_unencoded_plus_and_empty_since(lead_client):
    cores, dies, now = _two_hubs()
    since = cores.last_new_transaction_at.isoformat()  # +00:00
    assert lead_client.get(f"/freshness/notice?hub=cores&since={since}").status_code == 204
    assert lead_client.get("/freshness/notice?hub=cores&since=").status_code == 200


def test_notice_drops_unsafe_refresh_url_and_falls_back_to_current_page(lead_client):
    cores, dies, now = _two_hubs()
    base = "/freshness/notice?hub=cores&since="
    html = lead_client.get(base + "&refresh_url=https://evil.example/").content.decode()
    assert "evil" not in html and '<a class="button" href="" data-takt-refresh>Refresh</a>' in html
    html = lead_client.get(base + "&refresh_url=//evil.example/x&refresh_target=x%22onload").content.decode()
    assert "evil" not in html and "onload" not in html
    html = lead_client.get(base, HTTP_HX_CURRENT_URL="http://takt.plant.local/shift/?date=2026-10-12").content.decode()
    assert 'href="/shift/?date=2026-10-12"' in html


def test_notice_requires_login_and_known_hub(client, lead_client):
    _two_hubs()
    from django.test import Client

    anon = Client()
    assert anon.get("/freshness/notice?hub=cores&since=").status_code == 302
    # Expired session on an htmx poll: 204, never the login page swapped into the live region.
    polled = anon.get("/freshness/notice?hub=cores&since=", HTTP_HX_REQUEST="true")
    assert polled.status_code == 204 and polled.content == b""
    assert lead_client.get("/freshness/notice?hub=nope&since=").status_code == 400
    assert lead_client.get("/freshness/notice?hub=cores&since=garbage").status_code == 400


def test_refresh_fragment_reset_restarts_polling_out_of_band():
    cores, dies, now = _two_hubs()
    page, _ = _poller(cores, refresh_url="/shift/", refresh_target="#shift-table")
    cores.last_new_transaction_at = f.local(cores, 2026, 10, 12, 14, 45)
    cores.save()
    html = Template('{% load takt_freshness %}{% freshness_notice_reset h refresh_url="/shift/" '
                    'refresh_target="#shift-table" %}').render(Context({"h": "cores"}))
    container_id = re.search(r'class="freshness-notice" id="([^"]+)"', page).group(1)
    assert re.search(rf'<div id="{container_id}" hx-swap-oob="innerHTML">', html)
    assert 'hx-trigger="every 300s"' in html and "New data" not in html
    url = re.search(r'hx-get="([^"]+)"', html).group(1).replace("&amp;", "&")
    assert datetime.fromisoformat(parse_qs(urlsplit(url).query)["since"][0]) == cores.last_new_transaction_at


def test_notice_time_uses_the_hubs_plant_timezone(lead_client, clock):
    cores, dies, now = _two_hubs()
    clock(f.local(cores, 2026, 10, 12, 15, 0))  # same local day as the data, not the real clock
    cores.plant_timezone = "America/New_York"
    cores.last_new_transaction_at = f.local(dies, 2026, 10, 12, 14, 45)  # 2:45 PM Chicago = 3:45 PM New York
    cores.save()
    html = lead_client.get("/freshness/notice?hub=cores&since=").content.decode()
    assert "New data from 3:45 PM" in html


def test_page_with_dispute_form_loads_the_refresh_guard(rf, settings):
    _two_hubs()
    request = rf.get("/shift/")
    request.user = f.user("lead2", Profile.LEAD)
    page = Template(
        '{% extends "web/base.html" %}{% load takt_freshness %}{% block content %}'
        '{% freshness_notice "cores" refresh_url="/shift/" refresh_target="#shift-table" %}'
        '<section id="shift-table"><h2>Injection 3</h2>'
        '<form method="post" data-takt-form><input name="note"></form></section>{% endblock %}'
    ).render(RequestContext(request, {}))
    assert re.search(r'<script src="[^"]*ops/freshness\.js" defer></script>', page)
    assert '<form method="post" data-takt-form>' in page
    assert 'id="live-region"' in page and 'aria-live="polite"' in page
    assert 'role="status"' in page and 'hx-trigger="every 300s"' in page
    js = (Path(settings.BASE_DIR) / "ops/static/ops/freshness.js").read_text()
    for needle in ('"htmx:beforeSwap"', "shouldSwap = false", "form[data-takt-form]", "data-takt-refresh",
                   "Finish or cancel your dispute, then Refresh",
                   '"htmx:afterSwap"', "heading.focus()", "live-region", "New data loaded"):
        assert needle in js, needle


# D13: database unreachable.

def test_database_unreachable_renders_plain_503_page(lead_client, monkeypatch):
    _two_hubs()
    calls = []
    monkeypatch.setattr("ops.context_processors._freshness.snapshot", lambda: calls.append(1))

    down = OperationalError("could not connect to server: Connection refused")
    with mock.patch.object(connections["default"], "cursor", side_effect=down):
        resp = lead_client.get("/")
        assert resp.status_code == 503
        assert resp["Retry-After"]
        body = resp.content.decode()
        assert ("Takt can't reach its database. Data is safe; try again in a few minutes. "
                "IT has been alerted via /healthz.") in body
        assert "<h1>Database unreachable</h1>" in body
        assert calls == []
        # /healthz still answers with JSON.
        health = lead_client.get("/healthz")
        assert health.status_code == 503 and health.json()["database"] == "unreachable"


def test_operational_error_with_database_reachable_is_not_masked(lead_client, monkeypatch):
    import web.views

    def boom(*a, **kw):
        raise OperationalError("canceling statement due to statement timeout")

    monkeypatch.setattr(web.views, "render", boom)
    with pytest.raises(OperationalError):
        lead_client.get("/")


def test_db_error_outside_a_view_gets_the_503_page_via_handler500(monkeypatch):
    from django.contrib.sessions.middleware import SessionMiddleware
    from django.test import Client

    def broken_response(self, request, response):
        raise OperationalError("server closed the connection unexpectedly")

    monkeypatch.setattr(SessionMiddleware, "process_response", broken_response)
    monkeypatch.setattr("ops.freshness.database_reachable", lambda: False)
    resp = Client(raise_request_exception=False).get("/login/")
    assert resp.status_code == 503
    assert "<h1>Database unreachable</h1>" in resp.content.decode()


def test_handler500_falls_back_to_default_500_when_database_is_up(rf):
    from ops.views import server_error

    assert server_error(rf.get("/")).status_code == 500
