"""A shift belongs to one hub. In an area with a Cores line and a Dies line, both hubs have a "1st" shift:
a lead on Cores "1st" must get none of Dies "1st" jobs (My Shift, job list, dispute, drill-down), even for a
person who works on both hubs (one Operator, two aliases)."""

import re
from datetime import time, timedelta

import pytest

from accounts.models import Profile
from core import models as m
from disputes.models import Dispute
from tests import factories as f
from tests import golden as g
from tests.screen_world import D, build, pin, ref, t


@pytest.fixture
def p(monkeypatch):
    p = build()
    dies = f.hub("dies")
    line = f.line("Dies Line 1", dies, p["area"])
    st = f.station(line, 1, "Press 7")
    part = f.part("D-9001", f.family("Dies-X"), dies)
    p["dies_first"] = f.shift(dies, "1st", time(7), time(15))
    both = p["ops"]["mid"].operator  # Mortimer works on both hubs
    p["dies_alias"] = f.alias(both, dies, "MA-DIES")
    p["dies_only"] = f.alias(f.operator("Quentin Dieworthy"), dies, "QD-1")
    p["dies_jobs"] = [g.job(st, part, p["dies_alias"], t(dies, 9), qty=7),
                      g.job(st, part, p["dies_only"], t(dies, 10), qty=8)]
    assert all(j.shift == p["dies_first"] for j in p["dies_jobs"])
    pin(monkeypatch, t(p["hub"], 16))
    return p


def test_cores_lead_gets_no_dies_jobs(p, client):
    lead = p["users"]["lead"]
    client.force_login(lead)
    html = client.get(f"/area/{p['area'].pk}/shift/?shift_id={p['first'].pk}&date=2026-09-15").content.decode()
    assert "Press 7" not in html and "D-9001" not in html
    # the person on both hubs: the job list has only the Cores jobs
    r = ref(lead, html, p["ops"]["mid"])
    token = re.search(r"takt_pt&quot;: &quot;([^&]+)&quot;", html).group(1)
    jl = client.get(f"/area/{p['area'].pk}/shift/jobs/{r}/",
                    {"shift_id": p["first"].pk, "date": "2026-09-15", "takt_pt": token},
                    HTTP_HX_REQUEST="true").content.decode()
    assert "Sort" in jl and "Press 7" not in jl and p["dies_jobs"][0].mes_id not in jl
    # and can't dispute the Dies job through that list
    resp = client.post(f"/area/{p['area'].pk}/shift/disputes/", {
        "shift_id": p["first"].pk, "date": "2026-09-15", "ref": r, "job": [p["dies_jobs"][0].pk],
        "reason": "wrong_quantity", "takt_pt": token}, HTTP_HX_REQUEST="true")
    assert "Tick jobs from this list." in resp.content.decode() and Dispute.objects.count() == 0


def test_dies_only_operator_has_no_ref_on_the_cores_page(p, client):
    lead = p["users"]["lead"]
    client.force_login(lead)
    html = client.get(f"/area/{p['area'].pk}/shift/?shift_id={p['first'].pk}&date=2026-09-15").content.decode()
    token = re.search(r"takt_pt&quot;: &quot;([^&]+)&quot;", html).group(1)
    r = ref(lead, html, p["dies_only"])  # what a forged ref for that person would be
    resp = client.get(f"/area/{p['area'].pk}/shift/jobs/{r}/",
                      {"shift_id": p["first"].pk, "date": "2026-09-15", "takt_pt": token}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 403 and "Quentin" not in resp.content.decode()


def test_drill_down_refuses_another_hubs_same_named_shift(p, client):
    sup = p["users"]["supervisor"]
    client.force_login(sup)
    press = m.Station.objects.get(name="Press 7")
    resp = client.get(f"/station/{press.pk}/operators/", {"date": "2026-09-15", "shift_id": p["first"].pk},
                      HTTP_HX_REQUEST="true")
    assert resp.status_code == 403
    html = client.get(f"/area/{p['area'].pk}/", {"date": "2026-09-15", "shift_id": p["first"].pk}).content.decode()
    assert "Dies Line 1" not in re.search(r'<table class="line-health">.*?</table>', html, re.S).group(0)


def test_print_sheet_is_hub_scoped(p, client):
    client.force_login(p["users"]["builder"])
    html = client.get(f"/area/{p['area'].pk}/shift/print/?shift_id={p['first'].pk}&date=2026-09-15").content.decode()
    assert "Press 7" not in html and "Quentin Dieworthy" not in html
