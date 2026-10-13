"""D-T3 + E-T4: My Shift, the lead's home (design Component 6, review D5, D6, D10, D14, D23; eng re-review
D4, D5).

- "Worth a conversation": up to 3 names per green station-part group with context reasons, never ranked;
- the table grouped by station and part (one <table>/<tbody> per group), rows A-Z, rate columns not sortable;
- a station that is not count-trusted shows only "Counts unverified, keep tally.";
- job-level disputes: "This looks wrong" -> the job list -> tick jobs + reason -> one row per job;
- "My disputes" strip with Open / Confirmed / Rejected and the supervisor's note;
- excluded jobs (D23): "N jobs excluded", struck through with reason, who and when;
- live notice wiring (E-T4): the poller sits outside the table and forms, Refresh returns the table plus the
  out-of-band reset, dispute forms carry data-takt-form (the guard refuses to swap over them);
- D10 states: start of shift with zero jobs closed; no shift scheduled today.
"""

import json
import re
from html import unescape
from datetime import timedelta

import pytest

from accounts.policy import TOKEN_PARAM
from core import models as m
from disputes import service
from disputes.models import Dispute
from tests.identity_world import set_gate1
from tests.screen_world import D, NAMES, build, pin, t

TOKEN_RE = re.compile(r'hx-vals="\{&quot;' + TOKEN_PARAM + r'&quot;: &quot;([^&]+)&quot;\}"')


@pytest.fixture
def p(monkeypatch):
    p = build()
    p["now"] = t(p["hub"], 14, 40)  # Tue 14:40, during the 1st shift: live
    pin(monkeypatch, p["now"])
    p["hub"].last_new_transaction_at = p["now"] - timedelta(minutes=5)
    p["hub"].save()
    p["url"] = f"/area/{p['area'].pk}/shift/?shift_id={p['first'].pk}"
    return p


def _page(client, p, user, url=None):
    client.force_login(user)
    resp = client.get(url or p["url"])
    assert resp.status_code == 200, resp.status_code
    return resp.content.decode()


def _token(html):
    return TOKEN_RE.search(html).group(1)


def _text(html):
    return unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip())


def test_lead_lands_on_my_shift_live(p, client):
    client.force_login(p["users"]["lead"])
    resp = client.get("/", follow=True)
    assert resp.redirect_chain == [("/shift/", 302), (p["url"], 302)]
    html = resp.content.decode()
    assert "<h1>My Shift · Cores Area</h1>" in html and "1st shift, Tue Sep 15 (live)" in html


def test_worth_a_conversation_list(p, client):
    html = _page(client, p, p["users"]["builder"])
    block = re.search(r'<section class="conversation".*?</section>', html, re.S).group(0)
    assert "<h3 id=\"conversation-h\">Worth a conversation</h3>" in block
    items = [_text(li) for li in re.findall(r"<li>(.*?)</li>", block, re.S)]
    assert items == ["Nerissa Quell · Sort · −20% vs median · tenure < 90 days · upstream output gap 270 min · "
                     "270 min with no transaction"]
    assert html.index("Worth a conversation") < html.index('<table class="data operators"')


def test_conversation_list_is_never_on_a_non_green_group(p, client):
    m.StationPartGrade.objects.filter(station=p["s2"]).update(grade="amber")
    html = _page(client, p, p["users"]["builder"])
    assert "Worth a conversation" not in html


def test_grouped_by_station_and_part_rows_a_to_z(p, client):
    html = _page(client, p, p["users"]["builder"])
    tables = re.findall(r'<table class="data operators" id="group-(\d+)-(\d+)">(.*?)</table>', html, re.S)
    assert [(int(s), int(pt)) for s, pt, _ in tables] == [(p["s1"].pk, p["part"].pk), (p["s2"].pk, p["part"].pk)]
    sort_body = tables[1][2]
    assert sort_body.count("<tbody>") == 1
    names = re.findall(r'<th scope="row" data-label="Operator">([^<]+)</th>', sort_body)
    assert names == NAMES + ["Unmapped operator (CT-901)"]  # A-Z, never by rate
    thead = re.search(r"<thead>(.*?)</thead>", sort_body, re.S).group(1)
    assert "aria-sort" not in thead and "<a " not in thead and "<button" not in thead
    assert "Verified" in re.search(r"<caption>(.*?)</caption>", sort_body, re.S).group(1)
    assert "median 20 u/hr" in sort_body


def test_letters_ascend_in_each_group_before_gate1(p, client):
    set_gate1(False)
    html = _page(client, p, p["users"]["lead"])
    assert "Names hidden until HR approves operator-level views (Gate 1)." in html
    assert not any(n in html for n in NAMES)
    for body in re.findall(r"<tbody>(.*?)</tbody>", html, re.S):
        letters = re.findall(r'data-label="Operator">(?:Unmapped operator|Operator) ([A-Z]+)<', body)
        assert letters == sorted(letters, key=lambda x: (len(x), x))


def test_station_not_count_trusted_shows_only_the_message(p, client):
    p["s2"].count_trusted = False
    p["s2"].save()
    html = _page(client, p, p["users"]["builder"])
    block = re.search(rf'<div class="group-unverified" id="group-{p["s2"].pk}-{p["part"].pk}">(.*?)</div>', html,
                      re.S).group(1)
    assert "Counts unverified, keep tally." in block and "Counts unverified" in block
    assert not any(n in html for n in NAMES)  # no operator rows at all for Sort


def test_matrix_columns_on_my_shift(p, client):
    m.StationPartGrade.objects.filter(station=p["s2"]).update(grade="red")
    html = _page(client, p, p["users"]["builder"])
    sort = re.search(rf'id="group-{p["s2"].pk}-{p["part"].pk}">(.*?)</table>', html, re.S).group(1)
    assert "Rate vs median" not in sort and "Time with no transaction" not in sort
    assert "Upstream output gap" in sort and "Tenure" in sort and "This looks wrong" in sort


def _jobs_url(p, key):
    return f"/area/{p['area'].pk}/shift/jobs/{p['ops'][key].pk}/"


def test_this_looks_wrong_opens_the_job_list(p, client):
    html = _page(client, p, p["users"]["lead"])
    assert re.search(r'<button type="button" class="button-link drill-toggle dispute-toggle" hx-get="'
                     + re.escape(_jobs_url(p, "new")) + r'\?date=2026-09-15&amp;shift_id=\d+"', html)
    resp = client.get(_jobs_url(p, "new"), {"shift_id": p["first"].pk, "date": "2026-09-15",
                                            TOKEN_PARAM: _token(html)}, HTTP_HX_REQUEST="true")
    jl = resp.content.decode()
    assert re.search(r'<form class="dispute-form job-list" data-takt-form method="post" hx-post="/area/\d+/shift/disputes/"', jl)
    assert jl.count('type="checkbox" name="job"') == 2
    for label in ("Wrong quantity", "Wrong operator", "Job not mine", "Time looks wrong", "Other"):
        assert f">{label}</label>" in jl
    assert "If other, what looks wrong?" in jl


def test_three_job_dispute_submission_and_my_disputes(p, client, monkeypatch):
    """Test plan (re-review): a lead ticks 3 jobs and a reason; the supervisor confirms 2, rejects 1 with
    notes; the lead's "My disputes" shows each status and note."""
    lead = p["users"]["lead"]
    html = _page(client, p, lead)
    token = _token(html)
    assert '<section id="my-disputes"' in html and 'hidden>' in re.search(r'<section id="my-disputes"[^>]*>', html).group(0)
    # The job list is one person's: give Mortimer a third job on the shift and tick all three.
    extra = m.Job.objects.create(hub=p["hub"], mes_id="EXTRA-1", alias=p["ops"]["mid"], station=p["s2"], part=p["part"],
                                 started_at=t(p["hub"], 9), completed_at=t(p["hub"], 9, 30), quantity=5,
                                 shift=p["first"], production_date=D)
    jobs = p["jobs"]["mid"] + [extra]
    resp = client.post(f"/area/{p['area'].pk}/shift/disputes/", {
        "shift_id": p["first"].pk, "date": "2026-09-15", "alias_id": p["ops"]["mid"].pk,
        "job": [j.pk for j in jobs], "reason": "wrong_quantity", TOKEN_PARAM: token}, HTTP_HX_REQUEST="true")
    assert resp.status_code == 200
    out = resp.content.decode()
    assert "Dispute submitted" in out and "3 jobs sent to the area supervisor." in out
    assert '<section id="my-disputes" class="strip my-disputes" aria-labelledby="my-disputes-h" hx-swap-oob="outerHTML">' in out
    assert json.loads(resp["HX-Trigger-After-Settle"]) == {"takt:announce": "Dispute submitted"}
    rows = list(Dispute.objects.order_by("pk"))
    assert len(rows) == 3 and len({r.submission_id for r in rows}) == 1
    assert {r.job_id for r in rows} == {j.pk for j in jobs} and all(r.raised_by == lead for r in rows)

    sup = p["users"]["supervisor"]
    service.decide(rows[0], sup, service.CONFIRM, "Traveler says 80.", now=p["now"])
    service.decide(rows[1], sup, service.CONFIRM, "Double scan.", now=p["now"])
    service.decide(rows[2], sup, service.REJECT, "Bin count matches.", now=p["now"])
    html = _page(client, p, lead)
    strip = re.search(r'<section id="my-disputes".*?</section>', html, re.S).group(0)
    assert strip.count("Confirmed</span>") == 2 and strip.count("Rejected</span>") == 1
    assert "Traveler says 80. (J. Ortiz)" in strip and "Bin count matches. (J. Ortiz)" in strip


def test_dispute_errors_rerender_the_form(p, client):
    html = _page(client, p, p["users"]["lead"])
    resp = client.post(f"/area/{p['area'].pk}/shift/disputes/", {
        "shift_id": p["first"].pk, "date": "2026-09-15", "alias_id": p["ops"]["new"].pk,
        "job": [p["jobs"]["new"][0].pk], "reason": "other", "reason_text": "", TOKEN_PARAM: _token(html)},
        HTTP_HX_REQUEST="true")
    out = resp.content.decode()
    assert 'Say what looks wrong when the reason is "Other".' in unescape(out) and "data-takt-form" in out
    assert Dispute.objects.count() == 0
    # a job from someone else's list is refused
    resp = client.post(f"/area/{p['area'].pk}/shift/disputes/", {
        "shift_id": p["first"].pk, "date": "2026-09-15", "alias_id": p["ops"]["new"].pk,
        "job": [p["jobs"]["mid"][0].pk], "reason": "wrong_quantity", TOKEN_PARAM: _token(html)},
        HTTP_HX_REQUEST="true")
    assert "Tick jobs from this list." in resp.content.decode() and Dispute.objects.count() == 0


def test_excluded_jobs_link_and_strikethrough(p, client):
    sup = p["users"]["supervisor"]
    [d] = service.submit(p["users"]["lead"], p["jobs"]["new"][:1], Dispute.WRONG_QUANTITY)
    # Not a count dispute: a confirmed count dispute here (1 of 8 jobs) would revoke Sort's count trust.
    service.decide(d, sup, service.CONFIRM, "Recounted: 30.", affects_counts=False, now=t(p["hub"], 15, 10))
    html = _page(client, p, p["users"]["lead"])
    assert re.search(r'class="button-link drill-toggle excluded-link"[^>]*>1 job excluded</button>', html)
    jl = client.get(_jobs_url(p, "new"), {"shift_id": p["first"].pk, "date": "2026-09-15",
                                          TOKEN_PARAM: _token(html)}, HTTP_HX_REQUEST="true").content.decode()
    row = re.search(r'<tr class="job-excluded">(.*?)</tr>', jl, re.S).group(1)
    assert "Excluded: wrong quantity, confirmed by J. Ortiz, Tue 3:10 PM" in _text(row)
    assert 'name="job"' not in row  # an excluded job can't be disputed again
    css = open("web/static/css/screens.css").read()
    assert ".job-excluded td.job-cell { text-decoration: line-through; }" in css


def test_e_t4_live_notice_wiring_with_an_open_dispute_form(p, client):
    """E-T4: a My Shift page with an open dispute form has the guard wiring; the real browser E2E (an open
    form survives a poll) runs at integration."""
    html = _page(client, p, p["users"]["lead"])
    # the poller is in its own container, outside #shift-table and outside every form
    notice = re.search(r'<div class="live-notice no-print"><div class="freshness-notice" id="freshness-notice-cores" '
                       r'role="status" aria-live="polite" aria-atomic="true">(.*?)</div>\s*</div>', html, re.S)
    assert notice, "freshness_notice container missing"
    assert html.index('class="live-notice') < html.index('<section id="shift-table"')
    before = html[:html.index('class="live-notice')]
    assert before.count("<form") == before.count("</form>")
    poll = notice.group(1)
    assert 'hx-trigger="every 300s"' in poll
    assert "refresh_target=%23shift-table" in poll
    assert f"refresh_url=%2Farea%2F{p['area'].pk}%2Fshift%2Ftable%2F%3Fdate%3D2026-09-15%26shift_id%3D{p['first'].pk}" in poll
    assert re.search(r'<script src="[^"]*ops/freshness\.js" defer></script>', html)
    # open a dispute form: it is marked data-takt-form and lives inside #shift-table (the Refresh target)
    jl = client.get(_jobs_url(p, "new"), {"shift_id": p["first"].pk, "date": "2026-09-15",
                                          TOKEN_PARAM: _token(html)}, HTTP_HX_REQUEST="true").content.decode()
    assert "<form class=\"dispute-form job-list\" data-takt-form" in jl
    table = re.search(r'<section id="shift-table".*', html, re.S).group(0)
    assert 'id="jobs-' in table  # the job list container the form loads into is inside the Refresh target
    js = open("ops/static/ops/freshness.js").read()
    assert 'busyForm(evt.detail.target)' in js and "form[data-takt-form]" in js


def test_refresh_fragment_carries_the_out_of_band_reset(p, client):
    html = _page(client, p, p["users"]["lead"])
    resp = client.get(f"/area/{p['area'].pk}/shift/table/", {"shift_id": p["first"].pk, "date": "2026-09-15",
                                                            TOKEN_PARAM: _token(html)}, HTTP_HX_REQUEST="true")
    frag = resp.content.decode()
    assert frag.lstrip().startswith('<section id="shift-table"')
    assert '<div id="freshness-notice-cores" hx-swap-oob="innerHTML">' in frag
    assert 'hx-trigger="every 300s"' in frag
    # letters on the fragment match the page (same token)
    page_letters = re.findall(r'data-label="Operator">(Operator [A-Z]+)<', html)
    assert re.findall(r'data-label="Operator">(Operator [A-Z]+)<', frag) == page_letters


def test_start_of_shift_with_zero_jobs_closed(p, client, monkeypatch):
    nxt = D + timedelta(days=1)
    pin(monkeypatch, t(p["hub"], 7, 5, d=nxt))
    html = _page(client, p, p["users"]["lead"])
    assert "1st shift started 7:00 AM. First jobs usually close within 20 minutes." in html
    assert (f'<a href="/area/{p["area"].pk}/shift/print/?shift_id={p["first"].pk}&amp;date=2026-09-15">'
            "Last shift's sheet</a>") in html


def test_no_shift_scheduled_today(p, client, monkeypatch):
    m.Shift.objects.filter(pk=p["first"].pk).update(weekdays="0234")  # not on Tuesdays (1)
    html = _page(client, p, p["users"]["lead"])
    assert "No shift scheduled today" in html
    assert re.search(r'<a href="/area/\d+/shift/\?shift_id=\d+&amp;date=2026-09-14">Last shift</a>', html)
    assert re.search(r'<a href="/line/\?date=2026-09-14&amp;days=14&amp;shift_id=\d+">14-day view</a>', html)
    assert "<table class=\"data operators\"" not in html


def test_other_leads_shift_is_refused(p, client):
    client.force_login(p["users"]["lead_3rd"])
    resp = client.get(p["url"])
    assert resp.status_code == 403 and "This shift belongs to another lead." in resp.content.decode()
