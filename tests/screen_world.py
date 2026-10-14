"""A small plant for the screen tests (Lane D): one area, Cores Line 1 with Injection 3 (seq 1) -> Sort
(seq 2), part C-4471, shifts every day 1st 07-15 / 2nd 15-23 / 3rd 23-07 (America/Chicago), demand
900/day, both stations graded green with 40 days of history, and a crew on Sort, 1st shift of D:

    key   name                 tenure       Sort, 1st shift of D
    new   Nerissa Quell        30 days      48 units in 3 h  = 16/h  (-20% vs median)
    mid   Mortimer Ashgrove    1000 days    140 units in 7 h = 20/h
    fast  Fenwick Oddsworth    2000 days    168 units in 7 h = 24/h
    temp  (unmapped CT-901)    -            10 units

Injection 3 has a feeder (Philippa Undergrove) completing a job every 10 minutes 07:10-10:00.
Every date is fixed (D is a past Tuesday); tests pin ops.freshness.current_time for defaults.
"""

from datetime import date, timedelta

from accounts.models import Profile
from core import models as m
from tests import factories as f
from tests import golden as g
from tests.mes_fixtures import small_plant

D = date(2026, 9, 15)  # a Tuesday


def t(hub, hh, mm=0, d=D):
    return f.local(hub, d.year, d.month, d.day, hh, mm)


def grade(station, part, value="green", days=40):
    return m.StationPartGrade.objects.update_or_create(
        station=station, part=part,
        defaults=dict(grade=value, d_share=0, c_share=0, o_share=0, job_count=100, days_of_history=days,
                      computed_at=t(station.line.hub, 0)))[0]


def build():
    p = small_plant()
    hub, line, s1, s2, part = p["hub"], p["line"], p["s1"], p["s2"], p["part"]
    p["area"] = line.area
    p["area"].name = "Cores Area"
    p["area"].save()
    g.route(p["family"], s1)
    g.route(p["family"], s2)
    for back in range(0, 15):
        g.demand(line, p["family"], D - timedelta(days=back), 900)
    grade(s1, part)
    grade(s2, part)
    p["void"] = g.scrap_code(hub, "SYN-VOID", s1, "Voids")
    g.scrap(s2, part, p["void"], t(hub, 10), 12)

    feeder = f.alias(f.operator("Philippa Undergrove", hire_date=D - timedelta(days=3000)), hub, "PU-1")
    for i in range(18):
        g.job(s1, part, feeder, t(hub, 7) + timedelta(minutes=10 * i), qty=5)
    ops = {
        "new": f.alias(f.operator("Nerissa Quell", hire_date=D - timedelta(days=30)), hub, "NQ-1"),
        "mid": f.alias(f.operator("Mortimer Ashgrove", hire_date=D - timedelta(days=1000)), hub, "MA-1"),
        "fast": f.alias(f.operator("Fenwick Oddsworth", hire_date=D - timedelta(days=2000)), hub, "FO-1"),
        "temp": f.alias(None, hub, "CT-901"),
    }
    jobs = {k: [] for k in ops}
    jobs["new"] += [g.job(s2, part, ops["new"], t(hub, 8), minutes=120, qty=32),
                    g.job(s2, part, ops["new"], t(hub, 10), minutes=60, qty=16)]
    jobs["mid"] += [g.job(s2, part, ops["mid"], t(hub, 7), minutes=270, qty=90),
                    g.job(s2, part, ops["mid"], t(hub, 12), minutes=150, qty=50)]
    jobs["fast"] += [g.job(s2, part, ops["fast"], t(hub, 7), minutes=270, qty=108),
                     g.job(s2, part, ops["fast"], t(hub, 12), minutes=150, qty=60)]
    jobs["temp"] += [g.job(s2, part, ops["temp"], t(hub, 12), minutes=60, qty=10)]
    p.update(ops=ops, jobs=jobs, feeder=feeder)

    hub.last_successful_ingest_at = t(hub, 14, 38)
    hub.last_new_transaction_at = t(hub, 14, 35)
    hub.save()

    sup = f.user("sup-cores", Profile.SUPERVISOR, areas=[p["area"]])
    sup.first_name, sup.last_name = "Jo", "Ortiz"
    sup.save()
    p["users"] = {
        "builder": f.user("builder", None, superuser=True),
        "ops_manager": f.user("brian", Profile.OPS_MANAGER),
        "supervisor": sup,
        "lead": f.user("lead-cores-1st", Profile.LEAD, areas=[p["area"]], shift_obj=p["first"]),
        "lead_3rd": f.user("lead-cores-3rd", Profile.LEAD, areas=[p["area"]], shift_obj=p["third"]),
        "admin": f.user("admin", Profile.ADMIN),
    }
    return p


NAMES = ["Fenwick Oddsworth", "Mortimer Ashgrove", "Nerissa Quell"]


def ref(user, html, alias):
    """The opaque per-page reference (PageContext.ref) to `alias`'s person on the page `html` rendered for
    `user`: what the page's own buttons and forms carry instead of a pk (D12)."""
    import re

    from accounts.policy import TOKEN_PARAM, read_token, ref_for_key

    token = re.search(r'hx-vals="\{&quot;' + TOKEN_PARAM + r'&quot;: &quot;([^&]+)&quot;\}"', html).group(1)
    key = f"o{alias.operator_id}" if alias.operator_id else f"a{alias.pk}"
    return ref_for_key(read_token(token, user)["n"], key)


def pin(monkeypatch, instant):
    monkeypatch.setattr("ops.freshness.current_time", lambda: instant)
