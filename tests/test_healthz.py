"""T6: /healthz for IT's monitor (eng review D5). No auth; 503 when any hub is stale or the DB is down."""

from datetime import datetime, time, timedelta

from unittest import mock

import pytest
from django.db import OperationalError, connections

from tests import factories as f


@pytest.fixture
def clock(monkeypatch):
    def set_now(instant):
        monkeypatch.setattr("ops.freshness.current_time", lambda: instant)

    return set_now


def _hub_with_first_shift(code="cores"):
    h = f.hub(code)
    f.shift(h, "1st", time(7), time(15))
    return h


def test_zero_hubs_is_200(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok" and body["database"] == "ok" and body["hubs"] == []
    assert body["stale_after_minutes"] == 30


def test_quiet_2959_is_200_and_3000_is_503(client, clock):
    h = _hub_with_first_shift()
    last = f.local(h, 2026, 10, 12, 9, 0)
    h.last_new_transaction_at = h.last_successful_ingest_at = last
    h.save()

    clock(last + timedelta(minutes=29, seconds=59))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["hubs"][0]["stale"] is False

    clock(last + timedelta(minutes=30))
    resp = client.get("/healthz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "stale"
    hub = body["hubs"][0]
    assert set(hub) >= {"code", "last_new_transaction_at", "last_successful_ingest_at", "in_scheduled_shift",
                        "stale", "stale_since", "minutes_quiet"}
    assert hub["code"] == "cores" and hub["stale"] is True and hub["in_scheduled_shift"] is True
    assert hub["minutes_quiet"] == 30
    assert datetime.fromisoformat(hub["stale_since"]) == last + timedelta(minutes=30)
    assert datetime.fromisoformat(hub["last_new_transaction_at"]) == last


def test_outside_a_shift_is_200(client, clock):
    h = _hub_with_first_shift()
    h.last_new_transaction_at = h.last_successful_ingest_at = f.local(h, 2026, 10, 10, 9, 0)
    h.save()
    clock(f.local(h, 2026, 10, 12, 18, 0))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    hub = resp.json()["hubs"][0]
    assert hub["in_scheduled_shift"] is False and hub["stale"] is False and hub["minutes_quiet"] is None


def test_third_shift_across_midnight_counts_as_scheduled(client, clock):
    h = f.hub("dies")
    f.shift(h, "3rd", time(23), time(7))
    h.last_new_transaction_at = h.last_successful_ingest_at = f.local(h, 2026, 10, 12, 23, 50)
    h.save()
    clock(f.local(h, 2026, 10, 13, 0, 20))
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.json()["hubs"][0]["in_scheduled_shift"] is True


def test_one_stale_hub_of_two_is_503(client, clock):
    cores = _hub_with_first_shift("cores")
    dies = _hub_with_first_shift("dies")
    now = f.local(cores, 2026, 10, 12, 10, 0)
    cores.last_new_transaction_at = cores.last_successful_ingest_at = now - timedelta(minutes=5)
    cores.save()
    dies.last_new_transaction_at = dies.last_successful_ingest_at = now - timedelta(minutes=50)
    dies.save()
    clock(now)
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert {h["code"]: h["stale"] for h in resp.json()["hubs"]} == {"cores": False, "dies": True}


def test_database_unreachable_is_503_json(client, monkeypatch):
    down = OperationalError("could not connect to server")
    with mock.patch.object(connections["default"], "cursor", side_effect=down):
        resp = client.get("/healthz")
        assert resp.status_code == 503
        assert resp["Content-Type"].startswith("application/json")
        assert resp.json()["status"] == "error" and resp.json()["database"] == "unreachable"


def test_healthz_needs_no_login_and_is_not_cached(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert "no-cache" in resp["Cache-Control"] or "max-age=0" in resp["Cache-Control"]


def test_compose_healthcheck_polls_healthz():
    """T8 verify: the web container's healthcheck uses /healthz."""
    from pathlib import Path

    from django.conf import settings

    compose = (Path(settings.BASE_DIR) / "docker-compose.yml").read_text()
    assert "http://localhost:8000/healthz" in compose
