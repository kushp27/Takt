"""The demo plant: one definition shared by `seed_demo` (the Takt registry) and the
synthetic MES generator (`load_fixture_mes`, `fixture_tick`).

Everything here is SYNTHETIC. No name, code or number comes from the real Cores Hub or
Dies Hub (Open Question 1). Codes are invented and deliberately look invented.

Behaviours drive the trust grade (Component 4) so the demo shows every grade:
- "clean"        -> green
- "eos_close"    -> jobs batch-closed at shift end (C share); amber at 15%, red at 40%
- "overlap"      -> operator runs two jobs at once (O share); amber
- batch station  -> red by definition (the kiln), identical start/complete timestamps
- part C-4473 is introduced 12 days before the data end -> "not graded yet"
"""

import random
from dataclasses import dataclass
from datetime import date, time, timedelta

TIMEZONE = "America/Chicago"
WEEKDAYS = "012345"  # Mon-Sat: Saturday runs so both DST nights (Sat->Sun) have a 3rd shift

# (name, start, end, breaks)
SHIFTS = [
    ("1st", time(7), time(15), [(time(9, 30), time(9, 45)), (time(11, 30), time(12, 0)), (time(13, 30), time(13, 45))]),
    ("2nd", time(15), time(23), [(time(17, 30), time(17, 45)), (time(19, 30), time(20, 0)), (time(21, 30), time(21, 45))]),
    ("3rd", time(23), time(7), [(time(1, 30), time(1, 45)), (time(3, 30), time(4, 0)), (time(5, 30), time(5, 45))]),
]


@dataclass(frozen=True)
class StationSpec:
    code: str
    name: str
    sequence: int
    batch: bool = False
    count_trusted: bool = True
    crew: int = 2
    qty: int = 10  # units per job (per load for the kiln)
    target_per_shift: int = 600  # nominal units per shift at pace 1.0
    pace: float = 1.0  # how fast this station runs relative to demand
    behaviour: str = "clean"
    behaviour_share: float = 0.0


@dataclass(frozen=True)
class HubSpec:
    code: str
    name: str
    area: str
    line: str
    quantity_is_gross: bool
    op_prefix: str
    stations: tuple
    # family -> {part_code: weight}
    families: dict
    # family -> {station_code: visit_count}
    routing: dict
    # family -> daily demand
    demand: dict
    # (code, description, found at station, cause hint station, share of found-station qty)
    scrap: tuple
    unknown_station_code: str = ""
    unknown_part_code: str = ""


CORES = HubSpec(
    code="cores", name="Cores Hub", area="Cores Area", line="Cores Line 1",
    quantity_is_gross=False, op_prefix="C",
    stations=(
        StationSpec("SYN-INJ-3", "Injection 3", 1, crew=3, qty=12, pace=1.04),
        StationSpec("SYN-DBR-1", "Deburr 1", 2, qty=25, target_per_shift=500, pace=1.03,
                    behaviour="eos_close", behaviour_share=0.15),
        StationSpec("SYN-LCH-2", "Leach 2", 3, count_trusted=False, qty=30, pace=0.95,
                    behaviour="eos_close", behaviour_share=0.40),
        StationSpec("SYN-KILN-1", "Fire (kiln)", 4, batch=True, crew=1, qty=50, pace=1.0),
        StationSpec("SYN-SORT-1", "Post-fire sort", 5, qty=20, pace=1.02),
    ),
    families={"Core-S": {"C-4471": 0.6, "C-4472": 0.4}, "Core-L": {"C-5101": 1.0}},
    routing={
        "Core-S": {"SYN-INJ-3": 1, "SYN-DBR-1": 1, "SYN-LCH-2": 1, "SYN-KILN-1": 1, "SYN-SORT-1": 1},
        "Core-L": {"SYN-INJ-3": 1, "SYN-LCH-2": 1, "SYN-KILN-1": 1, "SYN-SORT-1": 1},
    },
    demand={"Core-S": 1500, "Core-L": 300},
    scrap=(
        ("SYN-VOID", "Voids", "SYN-SORT-1", "SYN-INJ-3", 0.030),
        ("SYN-CRACK", "Cracks", "SYN-SORT-1", "SYN-KILN-1", 0.018),
        ("SYN-CHIP", "Chips", "SYN-SORT-1", "SYN-DBR-1", 0.012),
        ("SYN-SHORT", "Short shot", "SYN-INJ-3", "SYN-INJ-3", 0.015),
    ),
    unknown_station_code="SYN-RWK-9", unknown_part_code="C-9999",
)

DIES = HubSpec(
    code="dies", name="Dies Hub", area="Dies Area", line="Dies Line 1",
    quantity_is_gross=True, op_prefix="D",
    stations=(
        StationSpec("SYN-PRS-1", "Press 1", 1, qty=5, target_per_shift=200, pace=0.97),
        StationSpec("SYN-MCH-2", "Machining 2", 2, qty=4, target_per_shift=267, pace=0.86,
                    behaviour="overlap", behaviour_share=0.08),
        StationSpec("SYN-INSP-1", "Final inspection", 3, crew=1, qty=10, target_per_shift=200, pace=0.92),
    ),
    families={"Die-A": {"D-100": 0.5, "D-101": 0.5}, "Die-B": {"D-200": 1.0}},
    routing={
        "Die-A": {"SYN-PRS-1": 1, "SYN-MCH-2": 1, "SYN-INSP-1": 1},
        "Die-B": {"SYN-PRS-1": 1, "SYN-MCH-2": 2, "SYN-INSP-1": 1},
    },
    demand={"Die-A": 400, "Die-B": 200},
    scrap=(
        ("SYN-BURR", "Burr", "SYN-INSP-1", "SYN-MCH-2", 0.030),
        ("SYN-DIM", "Out of dimension", "SYN-INSP-1", "SYN-PRS-1", 0.020),
        ("SYN-TOOL", "Tool mark", "SYN-MCH-2", "SYN-MCH-2", 0.010),
    ),
)

HUBS = (CORES, DIES)

# Part introduced late so its station-parts have under 4 weeks of history ("not graded yet").
NEW_PART = ("C-4473", "Core-S", 12)  # code, family, days before the data end
NEW_PART_WEIGHTS = {"C-4471": 0.5, "C-4472": 0.3, "C-4473": 0.2}

# Spring-forward night always included in the default generation (user clarification).
SPRING_FORWARD_SLICE = (date(2026, 3, 6), date(2026, 3, 8))

FIRST = ["Maria", "Dev", "Sam", "Lena", "Jordan", "Priya", "Marcus", "Ana", "Tomas", "Keisha", "Luis", "Mei",
         "Omar", "Grace", "Hector", "Ines", "Jamal", "Kara", "Leo", "Nadia", "Oscar", "Paula", "Quinn", "Rosa",
         "Victor", "Wen", "Yusuf", "Zoe", "Andre", "Bianca", "Carlos", "Dana", "Eli", "Farah", "Gus", "Hana",
         "Ivan", "Jade", "Kofi", "Lucia", "Mateo", "Nora", "Pedro", "Rhea", "Selim", "Tara", "Uma", "Vera"]
LAST = ["Alvarez", "Brooks", "Chen", "Diaz", "Evans", "Fischer", "Garcia", "Hughes", "Ibarra", "Jensen", "Kim",
        "Lopez", "Moreno", "Nguyen", "Okafor", "Patel", "Quintero", "Reyes", "Silva", "Torres", "Underwood",
        "Vargas", "Walsh", "Xu", "Young", "Zamora"]

ROSTER_SEED = 2026  # the roster is fixed, independent of the transaction seed


@dataclass(frozen=True)
class RosterEntry:
    hub_code: str
    hub_operator_id: str
    name: str
    hire_days_ago: int  # relative to the data end
    skill: float
    station_code: str
    shift_name: str


def roster(hub_spec):
    """Regular crews per station and shift, deterministic. Some hired under 90 days ago."""
    rng = random.Random(f"{ROSTER_SEED}|{hub_spec.code}")
    out, n = [], 0
    for st in hub_spec.stations:
        for shift_name, *_ in SHIFTS:
            for _ in range(st.crew):
                n += 1
                new_hire = rng.random() < 0.15
                hire_days = rng.randint(20, 85) if new_hire else rng.randint(200, 3600)
                skill = rng.uniform(0.75, 0.9) if new_hire else rng.uniform(0.85, 1.2)
                name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
                op_id = f"{hub_spec.op_prefix}{1000 + n}"
                out.append(RosterEntry(hub_spec.code, op_id, name, hire_days, round(skill, 3), st.code, shift_name))
    return out


def unmapped_ids(hub_spec):
    """Temp badges that are not on the roster: ingest creates them as unmapped aliases."""
    return [f"{hub_spec.op_prefix}T-90{i}" for i in (1, 2)]


def demand_exceptions(end_date, scheduled):
    """(zero, missing): one family with demand 0 on a day, one family-day with no TaktConfig row.

    `scheduled(d)` says whether d is a working day. Picks the 3rd/2nd working day before end_date.
    """
    days = []
    d = end_date - timedelta(days=1)
    while len(days) < 3:
        if scheduled(d):
            days.append(d)
        d -= timedelta(days=1)
    zero = ("cores", "Core-L", days[2])
    missing = ("dies", "Die-A", days[1])
    return zero, missing


def hub_spec(code):
    return next(h for h in HUBS if h.code == code)


__all__ = ["HUBS", "CORES", "DIES", "SHIFTS", "WEEKDAYS", "TIMEZONE", "roster", "unmapped_ids",
           "demand_exceptions", "hub_spec", "StationSpec", "HubSpec", "RosterEntry", "NEW_PART",
           "NEW_PART_WEIGHTS", "SPRING_FORWARD_SLICE"]
