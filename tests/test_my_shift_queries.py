"""My Shift's query count on the demo plant (review item: 228 queries for 29 rows before).

The conversation list reuses the operator groups the table already computed
(api.conversation_from_groups), the transaction-gap job intervals are read once per station, one HubPlan
(shift calendar) and one SiteSettings serve every station, and the routing is read once per line. Each area's
My Shift must stay under MAX_QUERIES (measured: Cores 70, Dies 57)."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import Profile
from core import models as m
from tests import factories as f
from tests.mes_fixtures import demo, mes  # noqa: F401

MAX_QUERIES = 80


def test_my_shift_query_count_on_the_demo(client, demo, monkeypatch):
    monkeypatch.setattr("ops.freshness.current_time", lambda: demo)
    seen_rows = 0
    for area in m.Area.objects.order_by("name"):
        hub = area.lines.first().hub
        shift = m.Shift.objects.filter(hub=hub).order_by("start_time").first()
        client.force_login(f.user(f"lead-q-{area.pk}", Profile.LEAD, areas=[area], shift_obj=shift))
        with CaptureQueriesContext(connection) as q:
            resp = client.get(f"/area/{area.pk}/shift/?shift_id={shift.pk}&date=2026-10-09")
        assert resp.status_code == 200
        seen_rows += resp.content.decode().count('class="operator-row"')
        assert len(q) < MAX_QUERIES, f"{area.name}: {len(q)} queries"
    assert seen_rows >= 30  # the bound means something: the demo shift has real rows
