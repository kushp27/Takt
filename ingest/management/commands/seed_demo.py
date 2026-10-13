"""manage.py seed_demo: the demo plant's Takt registry (synthetic; see ingest/fixture_spec.py).

Idempotent: re-running updates in place. Creates hubs (adapter=fixture, America/Chicago),
areas, lines, stations (one batch kiln, one mid-line station NOT count-trusted), part
families and parts, routings (Die-B visits Machining 2 twice; Core-L skips Deburr),
three shifts per hub with breaks, scrap codes with cause-station hints, the operator
roster with hire dates and their mapped aliases, and demand (TaktConfig) for the data
range with one family at demand 0 on one day and one family-day with no row at all.

Temp badges in the synthetic MES are NOT on the roster: ingest creates those aliases
unmapped (operator=None).
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone as djtz

from core import models as m
from ingest import fixture_spec as spec
from ingest.cli import parse_end


def _scheduled(d):
    return str(d.weekday()) in spec.WEEKDAYS


def _days(first, last):
    d = first
    while d <= last:
        yield d
        d += timedelta(days=1)


class Command(BaseCommand):
    help = "Create the demo plant registry (hubs, lines, stations, routing, shifts, roster, demand)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=42, help="Days of demand before --end (default 42).")
        parser.add_argument("--end", help="Data end, ISO datetime; naive = plant local time (default now).")
        parser.add_argument("--future-days", type=int, default=7, help="Days of demand after --end (default 7).")

    @transaction.atomic
    def handle(self, *args, days, end, future_days, **opts):
        end_dt = parse_end(end)
        builder = self._builder()
        for hs in spec.HUBS:
            hub, _ = m.Hub.objects.update_or_create(
                code=hs.code,
                defaults={"name": hs.name, "plant_timezone": spec.TIMEZONE, "adapter": m.Hub.ADAPTER_FIXTURE,
                          "quantity_is_gross": hs.quantity_is_gross},
            )
            end_date = end_dt.astimezone(ZoneInfo(hub.plant_timezone)).date()
            line = self._registry(hub, hs, builder, end_date)
            self._demand(line, hs, end_date, days, future_days)
        m.SiteSettings.load()
        self.stdout.write(self.style.SUCCESS(
            "Demo registry ready. Next: load_fixture_mes, then ingest --days 400, then compute_trust_grades."
        ))

    def _builder(self):
        User = get_user_model()
        u, created = User.objects.get_or_create(username="builder", defaults={"is_staff": True, "is_superuser": True})
        if created:
            u.set_unusable_password()  # set one with `manage.py changepassword builder`
            u.save()
        return u

    def _registry(self, hub, hs, builder, end_date):
        area, _ = m.Area.objects.get_or_create(name=hs.area)
        line, _ = m.Line.objects.update_or_create(name=hs.line, defaults={"area": area, "hub": hub})
        stations = {}
        for st in hs.stations:
            s, created = m.Station.objects.update_or_create(
                line=line, hub_station_code=st.code,
                defaults={"name": st.name, "sequence": st.sequence,
                          "station_type": m.Station.BATCH if st.batch else m.Station.FLOW},
            )
            if created and st.count_trusted:
                evidence = ("Demo seed (synthetic evidence): one-week reconciliation against the lead's tally, "
                            "transactions within 2.1%.")
                s.count_trusted, s.count_trust_set_by, s.count_trust_set_at = True, builder, djtz.now()
                s.count_trust_evidence = evidence
                s.save()
                m.CountTrustChange.objects.create(station=s, action=m.CountTrustChange.SET, changed_by=builder,
                                                  evidence=evidence)
            elif created:
                s.count_trusted = False
                s.count_trust_evidence = ("Demo seed (synthetic evidence): tally reconciliation off by 14%; "
                                          "adjudication found the transactions wrong. Leads keep the tally here.")
                s.save()
            stations[st.code] = s
        families = {}
        for fam, parts in hs.families.items():
            families[fam], _ = m.PartFamily.objects.get_or_create(name=fam)
            for code in parts:
                m.Part.objects.update_or_create(hub=hub, hub_part_code=code, defaults={"family": families[fam]})
        new_code, new_fam, _ = spec.NEW_PART
        if new_fam in families:
            m.Part.objects.update_or_create(hub=hub, hub_part_code=new_code, defaults={"family": families[new_fam]})
        for fam, visits in hs.routing.items():
            for code, n in visits.items():
                m.Routing.objects.update_or_create(family=families[fam], station=stations[code],
                                                   defaults={"visit_count": n})
        for name, start, end, breaks in spec.SHIFTS:
            sh, _ = m.Shift.objects.update_or_create(
                hub=hub, name=name,
                defaults={"start_time": start, "end_time": end, "weekdays": spec.WEEKDAYS, "active": True})
            sh.breaks.all().delete()
            for b_start, b_end in breaks:
                m.ShiftBreak.objects.create(shift=sh, start_time=b_start, end_time=b_end)
        for code, desc, _found, hint, _share in hs.scrap:
            m.ScrapCode.objects.update_or_create(
                hub=hub, code=code, defaults={"description": desc, "cause_station_hint": stations[hint]})
        for r in spec.roster(hs):
            alias = m.OperatorAlias.objects.filter(hub=hub, hub_operator_id=r.hub_operator_id).first()
            hire = end_date - timedelta(days=r.hire_days_ago)
            if alias and alias.operator:
                alias.operator.name, alias.operator.hire_date = r.name, hire
                alias.operator.save()
                continue
            op = m.Operator.objects.create(name=r.name, hire_date=hire)
            # The builder's alias mapping (design Component 2), already done for the roster.
            if alias:
                alias.operator = op
                alias.save()
            else:
                m.OperatorAlias.objects.create(hub=hub, hub_operator_id=r.hub_operator_id, operator=op)
        return line

    def _demand(self, line, hs, end_date, days, future_days):
        zero, missing = spec.demand_exceptions(end_date, _scheduled)
        dates = set(_days(end_date - timedelta(days=days + 1), end_date + timedelta(days=future_days)))
        dates |= set(_days(*spec.SPRING_FORWARD_SLICE))
        families = {f.name: f for f in m.PartFamily.objects.filter(name__in=hs.demand)}
        for d in sorted(dates):
            if not _scheduled(d):
                continue
            for fam, units in hs.demand.items():
                key = (hs.code, fam, d)
                if key == missing:  # no TaktConfig row at all: "no demand set" + admin warning (eng D7)
                    m.TaktConfig.objects.filter(line=line, family=families[fam], production_date=d).delete()
                    continue
                m.TaktConfig.objects.update_or_create(
                    line=line, family=families[fam], production_date=d,
                    defaults={"demand_units": 0 if key == zero else units})
