"""D-T4 + T7: the Area screen (supervisor's home) and the dispute queue.

Area (design review D7): open-disputes strip (only when disputes are open) -> line rows -> 3-shift table.
T7 (eng review D6): open-dispute count badge in the Area header and the nav; overdue (older than 2 shifts)
in red with the word "Overdue"; no email. Queue (D8): oldest first, overdue on top; inline expand with the
job's MES values; Confirm and Reject each require a note; per-job decisions with "apply to all".
"""

import json
import re
from datetime import timedelta

import pytest

from accounts.policy import TOKEN_PARAM
from core import models as m
from disputes import service
from disputes.models import Dispute
from tests.screen_world import D, build, pin, t

TOKEN_RE = re.compile(r'hx-vals="\{&quot;' + TOKEN_PARAM + r'&quot;: &quot;([^&]+)&quot;\}"')


@pytest.fixture
def p(monkeypatch):
    p = build()
    p["now"] = t(p["hub"], 16)  # Tue 16:00: the 1st shift (7-15) has ended
    pin(monkeypatch, p["now"])
    p["hub"].last_new_transaction_at = p["now"] - timedelta(minutes=3)
    p["hub"].save()
    return p


def _login(client, user):
    client.force_login(user)
    return client


def _area(client, p, user, **q):
    return _login(client, user).get(f"/area/{p['area'].pk}/", q)


def _dispute(p, keys=("new",), n=1, reason=Dispute.WRONG_QUANTITY, raised_at=None):
    jobs = [j for k in keys for j in p["jobs"][k][:n]]
    rows = service.submit(p["users"]["lead"], jobs, reason)
    if raised_at:
        Dispute.objects.filter(pk__in=[r.pk for r in rows]).update(raised_at=raised_at)
    return rows


def test_area_layout_order_strip_lines_shift_table(p, client):
    _dispute(p)
    html = _area(client, p, p["users"]["supervisor"]).content.decode()
    i_strip = html.index('<section class="strip disputes-strip"')
    i_lines = html.index('<table class="line-health">')
    i_shift = html.index('<table class="data shift-comparison"')
    assert i_strip < i_lines < i_shift
    strip = re.search(r'<section class="strip disputes-strip".*?</section>', html, re.S).group(0)
    assert "1 open" in strip and "none overdue" in strip and "oldest 0 shifts old" in strip
    assert f'<a href="/area/{p["area"].pk}/disputes/">Review</a>' in strip


def test_strip_only_when_disputes_are_open(p, client):
    html = _area(client, p, p["users"]["supervisor"]).content.decode()
    assert "disputes-strip" not in html and "area-dispute-badge" not in html


def test_shift_comparison_has_three_shifts(p, client):
    html = _area(client, p, p["users"]["supervisor"]).content.decode()
    table = re.search(r'<table class="data shift-comparison".*?</table>', html, re.S).group(0)
    heads = re.findall(r'<th scope="col">([^<]+)</th>', table)
    assert heads == ["Line", "1st shift pace", "1st shift yield", "2nd shift pace", "2nd shift yield",
                     "3rd shift pace", "3rd shift yield"]
    assert "<caption>Pace and yield by shift, Tue Sep 15</caption>" in table


def test_lead_sees_only_their_shift_in_the_comparison(p, client):
    html = _area(client, p, p["users"]["lead"]).content.decode()
    table = re.search(r'<table class="data shift-comparison".*?</table>', html, re.S).group(0)
    assert re.findall(r'<th scope="col">([^<]+)</th>', table) == ["Line", "1st shift pace", "1st shift yield"]
    assert "disputes-strip" not in html  # leads don't decide disputes


def test_badge_in_area_header_and_nav_counts_open_disputes(p, client):
    _dispute(p, keys=("new", "mid"), n=2)
    html = _area(client, p, p["users"]["supervisor"]).content.decode()
    assert re.search(r'<a class="count-badge" href="/area/\d+/disputes/" id="area-dispute-badge">4 open disputes</a>', html)
    assert re.search(r'Disputes<span class="nav-count" id="nav-dispute-count">4<span class="visually-hidden"> open'
                     r'</span></span>', html)
    # leads get no Disputes nav item; the ops manager does
    assert "nav-dispute-count" not in _login(client, p["users"]["lead"]).get(f"/area/{p['area'].pk}/").content.decode()


def test_overdue_disputes_show_red_with_the_word(p, client, monkeypatch):
    _dispute(p, raised_at=t(p["hub"], 9))
    pin(monkeypatch, t(p["hub"], 8, d=D + timedelta(days=1)))  # 2nd, 3rd and next 1st started: 3 shifts
    html = _area(client, p, p["users"]["supervisor"]).content.decode()
    assert '<span class="overdue-label">1 overdue</span>' in html
    assert '<span class="overdue-label">Overdue: 1</span>' in html
    queue = _login(client, p["users"]["supervisor"]).get(f"/area/{p['area'].pk}/disputes/").content.decode()
    assert '<span class="overdue-label">Overdue</span> · 3 shifts' in queue
    css = open("web/static/css/screens.css").read()
    assert re.search(r"\.overdue, \.overdue-label \{[^}]*color: var\(--behind\)", css)


def test_queue_oldest_first_overdue_on_top_with_doc_columns(p, client, monkeypatch):
    new_sub = _dispute(p, keys=("mid",), reason=Dispute.NOT_MINE, raised_at=t(p["hub"], 9, d=D + timedelta(days=1)))
    old_sub = _dispute(p, keys=("new",), raised_at=t(p["hub"], 9))
    pin(monkeypatch, t(p["hub"], 10, d=D + timedelta(days=1)))
    html = _login(client, p["users"]["supervisor"]).get(f"/area/{p['area'].pk}/disputes/").content.decode()
    heads = re.findall(r'<th scope="col">([^<]+)</th>', re.search(r"<thead>.*?</thead>", html, re.S).group(0))
    assert heads == ["Age", "Station and part", "Job ID and time", "Operator", "Raised by", "Reason"]
    assert html.index(f"submission-{old_sub[0].submission_id}") < html.index(f"submission-{new_sub[0].submission_id}")
    assert "Overdue" in html.split(f"submission-{new_sub[0].submission_id}")[0]
    assert "Names hidden until HR approves operator-level views (Gate 1)." in html


def test_lead_is_refused_from_the_queue(p, client):
    resp = _login(client, p["users"]["lead"]).get(f"/area/{p['area'].pk}/disputes/")
    assert resp.status_code == 403 and "This area isn't in your assignment" in resp.content.decode()


def _token(client, p):
    return TOKEN_RE.search(client.get(f"/area/{p['area'].pk}/disputes/").content.decode()).group(1)


def test_inline_expand_shows_mes_values_and_both_buttons(p, client):
    [d] = _dispute(p)
    c = _login(client, p["users"]["builder"])
    html = c.get(f"/area/{p['area'].pk}/disputes/{d.pk}/", {TOKEN_PARAM: _token(c, p)},
                 HTTP_HX_REQUEST="true").content.decode()
    job = d.job
    assert f'Job <span class="mono">{job.mes_id}</span>: MES values' in html
    assert f"<td>{job.quantity}</td>" in html and "Nerissa Quell" in html
    assert '<button type="submit" name="decision" value="confirm">Confirm</button>' in html
    assert '<button type="submit" name="decision" value="reject">Reject</button>' in html
    assert re.search(r'<form class="dispute-form" data-takt-form', html)
    assert "<textarea" in html and "required" in html


@pytest.mark.parametrize("decision", ["confirm", "reject"])
def test_confirm_and_reject_each_require_a_note(p, client, decision):
    [d] = _dispute(p)
    c = _login(client, p["users"]["supervisor"])
    resp = c.post(f"/area/{p['area'].pk}/disputes/{d.pk}/decide/",
                  {"decision": decision, "note": "  ", TOKEN_PARAM: _token(c, p)}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    assert "A note is required to confirm or reject." in resp.content.decode()
    assert resp["HX-Retarget"] == f"#dispute-{d.pk}-detail" and resp["HX-Reswap"] == "innerHTML"
    d.refresh_from_db()
    assert d.status == Dispute.OPEN and not m.Job.objects.get(pk=d.job_id).excluded


def test_decide_per_job_and_apply_to_all(p, client):
    rows = _dispute(p, keys=("new", "mid"), n=1)
    rows += _dispute(p, keys=("fast",), n=2)
    c = _login(client, p["users"]["supervisor"])
    token = _token(c, p)
    url = f"/area/{p['area'].pk}/disputes/"
    resp = c.post(f"{url}{rows[0].pk}/decide/", {"decision": "reject", "note": "Matches the traveler.",
                                                  TOKEN_PARAM: token}, HTTP_HX_REQUEST="true")
    html = resp.content.decode()
    assert html.lstrip().startswith(f'<tbody class="submission" id="submission-{rows[0].submission_id}">')
    assert "Rejected</span> by J. Ortiz: Matches the traveler." in html
    events = json.loads(resp["HX-Trigger-After-Settle"])
    assert events == {"takt:announce": "Dispute rejected", "takt:navcount": 3}
    resp = c.post(f"{url}{rows[2].pk}/decide/", {"decision": "confirm", "note": "Double scanned.",
                                                  "apply_to_all": "1", TOKEN_PARAM: token}, HTTP_HX_REQUEST="true")
    assert json.loads(resp["HX-Trigger-After-Settle"])["takt:announce"] == "2 disputes confirmed"
    statuses = {d.pk: d.status for d in Dispute.objects.all()}
    assert statuses == {rows[0].pk: "rejected", rows[1].pk: "open", rows[2].pk: "confirmed", rows[3].pk: "confirmed"}
    assert set(m.Job.objects.filter(excluded=True).values_list("pk", flat=True)) == {rows[2].job_id, rows[3].job_id}


def test_t7_dispute_badge_confirm_exclusion(p, client):
    """T7 verify: dispute -> badge count -> confirm -> exclusion (no email anywhere)."""
    [d] = _dispute(p)
    c = _login(client, p["users"]["supervisor"])
    assert ">1 open dispute<" in c.get(f"/area/{p['area'].pk}/").content.decode()
    c.post(f"/area/{p['area'].pk}/disputes/{d.pk}/decide/",
           {"decision": "confirm", "note": "Traveler says 30.", "affects_counts": "1",  # pre-ticked for wrong quantity
            TOKEN_PARAM: _token(c, p)}, HTTP_HX_REQUEST="true")
    job = m.Job.objects.get(pk=d.job_id)
    assert (job.excluded, job.excluded_from_counts, job.exclusion_reason) == (True, True, "wrong quantity")
    assert job.excluded_by == p["users"]["supervisor"] and job.excluded_at == p["now"]
    html = c.get(f"/area/{p['area'].pk}/").content.decode()
    assert "area-dispute-badge" not in html and "disputes-strip" not in html
    from django.core import mail

    assert mail.outbox == []
