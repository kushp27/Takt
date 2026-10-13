"""Line status word and the rule-based summary sentence (design Component 6, design review D3).

No LLM. Each of pace and yield is classified low / normal / high against its 14-day baseline
(mean ± 1 standard deviation, with an SD floor so a flat baseline does not turn every wobble into
"low"); the pair picks one of nine templates. The bottleneck sentence is appended only when the
line is Behind.

Status precedence (user decision): No demand set > Behind (line pace < 100%) > Yield problem
(yield more than 1 SD under its 14-day baseline) > On pace. "No demand set" means the line's
total units required is 0. When pace has no number for another reason: No demand set > Yield
problem > Pace unavailable.
"""

from statistics import mean, stdev

LOW, NORMAL, HIGH = "low", "normal", "high"

NO_DEMAND = "No demand set"
BEHIND = "Behind"
YIELD_PROBLEM = "Yield problem"
ON_PACE = "On pace"
# DEVIATION (approved by the user, review pass 2026-10-12): the doc's four status words do not
# cover a line whose final station is not count-trusted. Line pace is suppressed ("unavailable:
# final-station counts unverified"), so neither "Behind" nor "On pace" is known; this fifth word
# says so instead of guessing.
PACE_UNAVAILABLE = "Pace unavailable"

STATUS_KEYS = {NO_DEMAND: "no_demand", PACE_UNAVAILABLE: "pace_unavailable", BEHIND: "behind",
               YIELD_PROBLEM: "yield_problem", ON_PACE: "on_pace"}

# The doc's quoted template (Component 6), verbatim. Used when pace is at or above 100% and in its
# normal range, yield is low, and the top code has a cause-station hint.
DOC_TEMPLATE = ("On pace, yield {n} pts under baseline, mostly {top code}. "
                "Look upstream at {cause station}, not at pace.")

# (pace class, yield class) -> template. {lead}: "On pace" when line pace >= 100%, "Pace normal"
# when it is under 100% (Behind) but in its usual range. {p} / {y}: points vs baseline;
# {mostly}: ", mostly <code>"; {look}: " Look upstream at <cause station>." (omitted without a hint).
TEMPLATES = {
    (NORMAL, NORMAL): "{lead}, yield in its usual range.",
    (NORMAL, LOW): "{lead}, yield {y} pts under baseline{mostly}.{look_not_pace}",
    (NORMAL, HIGH): "{lead}, yield {y} pts above baseline.",
    (LOW, NORMAL): "Pace {p} pts under its 14-day baseline, yield in its usual range.",
    (LOW, LOW): "Pace {p} pts and yield {y} pts under baseline{mostly}.{look}",
    (LOW, HIGH): "Pace {p} pts under baseline while yield is {y} pts above it.",
    (HIGH, NORMAL): "Pace {p} pts above its 14-day baseline, yield in its usual range.",
    (HIGH, LOW): "Pace {p} pts above baseline but yield {y} pts under it{mostly}.{look}",
    (HIGH, HIGH): "Pace {p} pts and yield {y} pts above baseline.",
}
# When pace has no number (no demand, or final station not count-trusted), only yield is described.
YIELD_ONLY = {
    NORMAL: "Yield in its usual range.",
    LOW: "Yield {y} pts under baseline{mostly}.{look}",
    HIGH: "Yield {y} pts above baseline.",
}


def baseline(history, sd_floor=0.0):
    """{"mean", "sd", "days"} over the non-None history (needs 2+ points, else None). The SD is
    at least `sd_floor` (a fraction: 0.005 = 0.5 pts)."""
    pts = [h for h in history if h is not None]
    if len(pts) < 2:
        return None
    return {"mean": mean(pts), "sd": max(stdev(pts), sd_floor), "days": len(pts)}


def classify(value, history, sd_floor=0.0):
    """low / normal / high against mean ± 1 SD of `history` (needs 2+ points, else normal)."""
    if value is None:
        return None
    b = baseline(history, sd_floor)
    if b is None:
        return NORMAL
    if value < b["mean"] - b["sd"]:
        return LOW
    if value > b["mean"] + b["sd"]:
        return HIGH
    return NORMAL


def status(*, no_demand, pace_value, pace_suppressed, yield_class):
    """no_demand: the line's total units required is 0 for lack of demand."""
    if no_demand:
        return NO_DEMAND
    if pace_suppressed or pace_value is None:
        return YIELD_PROBLEM if yield_class == LOW else PACE_UNAVAILABLE
    if pace_value < 1.0:
        return BEHIND
    if yield_class == LOW:
        return YIELD_PROBLEM
    return ON_PACE


def _pts(x):
    return f"{abs(x) * 100:.1f}"


def sentence(*, status_word, pace_class, yield_class, pace_delta, yield_delta, top_code=None, cause_station=None,
             no_demand_families=(), pace_note=None, bottleneck=None, pace_value=None):
    """The summary sentence. pace_delta / yield_delta are fractions vs the baseline mean
    (0.05 = 5 pts). top_code: the line's top scrap code label; cause_station: its static hint.
    pace_value: line pace (for "On pace" vs "Pace normal"). pace_note: a sentence saying why pace
    has no number, used when pace_class is None."""
    mostly = f", mostly {top_code}" if top_code else ""
    look = f" Look upstream at {cause_station}." if cause_station else ""
    look_not_pace = f" Look upstream at {cause_station}, not at pace." if cause_station else ""
    on_pace = pace_value is not None and pace_value >= 1.0
    fmt = dict(p=_pts(pace_delta or 0), y=_pts(yield_delta or 0), mostly=mostly, look=look,
               look_not_pace=look_not_pace, lead="On pace" if on_pace else "Pace normal")
    parts = []
    if no_demand_families:
        parts.append(f"No demand set for {', '.join(no_demand_families)}.")
    yc = yield_class or NORMAL
    if pace_class is None:
        if pace_note:
            parts.append(pace_note)
        parts.append(YIELD_ONLY[yc].format(**fmt))
    elif (pace_class, yc) == (NORMAL, LOW) and on_pace and top_code and cause_station:
        parts.append(DOC_TEMPLATE.format(**{"n": fmt["y"], "top code": top_code, "cause station": cause_station}))
    else:
        parts.append(TEMPLATES[(pace_class, yc)].format(**fmt))
    if status_word == BEHIND and bottleneck:
        parts.append(bottleneck)
    return " ".join(parts)


__all__ = ["classify", "baseline", "status", "sentence", "TEMPLATES", "YIELD_ONLY", "DOC_TEMPLATE", "NO_DEMAND",
           "BEHIND", "YIELD_PROBLEM", "ON_PACE", "PACE_UNAVAILABLE", "STATUS_KEYS", "LOW", "NORMAL", "HIGH"]
