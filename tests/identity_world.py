"""A small plant with distinctive operator names, every role, and an oracle for who may see whom.

Used by the identity sweep (tests/test_identity_sweep.py) and the policy tests.
Names are deliberately unusual so a substring search over a response can't
false-positive on ordinary page text.

    area_in  (North): line_in  -> st_a1, st_a2
    area_out (South): line_out -> st_b1
    shifts: shift_1st 07:00-15:00, shift_3rd 23:00-07:00 (same hub)

    key  name                    area      shift      station
    zeb  Zebulon Quixote         area_in   shift_1st  st_a1
    ott  Ottoline Brackwater     area_in   shift_1st  st_a2
    pem  Pemberly Vashti         area_in   shift_3rd  st_a1
    hub  (unmapped HUBQX-77)     area_in   shift_1st  st_a1
    mar  Marigold Thistlewood    area_out  shift_1st  st_b1
"""

from dataclasses import dataclass, field
from datetime import time, timedelta

from django.utils import timezone

from accounts.models import Profile
from core.models import SiteSettings
from disputes.models import Dispute
from core.shifts import local_date
from tests import factories as f

ROLES = ["builder", "ops_manager", "sup_in", "sup_out", "lead_own", "lead_other", "admin", "anonymous"]


@dataclass
class FixtureOperator:
    key: str
    name: str | None  # None = unmapped alias
    hub_id: str
    area: str
    shift: str
    station: str
    operator: object = None
    alias: object = None


@dataclass
class World:
    ops: dict = field(default_factory=dict)
    users: dict = field(default_factory=dict)

    def __getitem__(self, item):
        return getattr(self, item)

    @property
    def names(self):
        return {k: o.name for k, o in self.ops.items() if o.name}

    @property
    def hub_ids(self):
        return {k: o.hub_id for k, o in self.ops.items()}

    def in_scope(self, area=None, shift=None, station=None):
        """Operator keys with jobs in the given area / shift / station (World attribute names)."""
        return {
            k for k, o in self.ops.items()
            if (area is None or o.area == area) and (shift is None or o.shift == shift)
            and (station is None or o.station == station)
        }

    # ---- oracle: an independent restatement of design Component 7 + user clarifications

    @staticmethod
    def role_area_ok(role, area):
        return {
            "builder": True, "ops_manager": True,
            "sup_in": area == "area_in", "sup_out": area == "area_out",
            "lead_own": area == "area_in", "lead_other": area == "area_in",
        }.get(role, False)

    @staticmethod
    def role_shift_ok(role, shift):
        if role == "lead_own":
            return shift == "shift_1st"
        if role == "lead_other":
            return shift == "shift_3rd"
        return role in ("builder", "ops_manager", "sup_in", "sup_out")

    def may_see(self, role, key):
        o = self.ops[key]
        return self.role_area_ok(role, o.area) and self.role_shift_ok(role, o.shift)

    @staticmethod
    def mode(role, gate1):
        if role in ("admin", "anonymous"):
            return "hidden"
        if role == "builder" or gate1:
            return "named"
        return "letter"


def set_gate1(on):
    s = SiteSettings.load()
    s.gate1_approved = on
    s.save()


def build_world():
    w = World()
    w.hub = f.hub("cores")
    w.area_in = f.area("North")
    w.area_out = f.area("South")
    w.line_in = f.line("North Line", w.hub, w.area_in)
    w.line_out = f.line("South Line", w.hub, w.area_out)
    w.st_a1 = f.station(w.line_in, 1, "Injection")
    w.st_a2 = f.station(w.line_in, 2, "Trim")
    w.st_b1 = f.station(w.line_out, 1, "Press")
    w.shift_1st = f.shift(w.hub, "1st", time(7), time(15))
    w.shift_3rd = f.shift(w.hub, "3rd", time(23), time(7))
    w.part = f.part("PX-1", hub_obj=w.hub)

    today = local_date(w.hub, timezone.now())
    w.today = today
    specs = [
        ("zeb", "Zebulon Quixote", "ZQ-101", "area_in", "shift_1st", "st_a1"),
        ("ott", "Ottoline Brackwater", "OB-202", "area_in", "shift_1st", "st_a2"),
        ("pem", "Pemberly Vashti", "PV-303", "area_in", "shift_3rd", "st_a1"),
        ("hub", None, "HUBQX-77", "area_in", "shift_1st", "st_a1"),
        ("mar", "Marigold Thistlewood", "MT-404", "area_out", "shift_1st", "st_b1"),
    ]
    for key, name, hub_id, area, shift, station in specs:
        op = f.operator(name) if name else None
        al = f.alias(op, w.hub, hub_id)
        w.ops[key] = FixtureOperator(key, name, hub_id, area, shift, station, op, al)
        sh = w[shift]
        start = f.local(w.hub, today.year, today.month, today.day, sh.start_time.hour) + timedelta(minutes=30)
        for i in range(2):
            f.job(w[station], w.part, al, start + timedelta(minutes=20 * i), minutes=15,
                  shift=sh, production_date=today)

    w.users = {
        "builder": f.user("builder", None, superuser=True),
        "ops_manager": f.user("brian", Profile.OPS_MANAGER),
        "sup_in": f.user("sup-north", Profile.SUPERVISOR, areas=[w.area_in]),
        "sup_out": f.user("sup-south", Profile.SUPERVISOR, areas=[w.area_out]),
        "lead_own": f.user("lead-north-1st", Profile.LEAD, areas=[w.area_in], shift_obj=w.shift_1st),
        "lead_other": f.user("lead-north-3rd", Profile.LEAD, areas=[w.area_in], shift_obj=w.shift_3rd),
        "admin": f.user("admin", Profile.ADMIN),
        "anonymous": None,
    }
    # One open dispute on Zebulon's first job, raised by the 1st-shift lead (dispute queue screens).
    w.dispute = Dispute.objects.create(
        job=w.ops["zeb"].alias.jobs.order_by("pk").first(), reason=Dispute.WRONG_QUANTITY,
        raised_by=w.users["lead_own"],
    )
    return w
