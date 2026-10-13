"""Line status word and the rule-based summary sentence (design Component 6, design review D3).

No LLM. Each of pace and yield is classified low / normal / high against its 14-day baseline
(mean ± 1 standard deviation); the pair picks one of nine templates. The bottleneck sentence is
appended only when the line is Behind.

Status precedence (user decision): No demand set > Behind (line pace < 100%) > Yield problem
(yield more than 1 SD under its 14-day baseline) > On pace.
"""

from statistics import mean, stdev

LOW, NORMAL, HIGH = "low", "normal", "high"

NO_DEMAND = "No demand set"
BEHIND = "Behind"
YIELD_PROBLEM = "Yield problem"
ON_PACE = "On pace"
# DEVIATION: the doc's four status words do not cover a line whose final station is not
# count-trusted: line pace is suppressed ("unavailable: final-station counts unverified"), so
# neither "Behind" nor "On pace" is known. Conservative reading: say so instead of guessing.
PACE_UNAVAILABLE = "Pace unavailable"

STATUS_KEYS = {NO_DEMAND: "no_demand", PACE_UNAVAILABLE: "pace_unavailable", BEHIND: "behind",
               YIELD_PROBLEM: "yield_problem", ON_PACE: "on_pace"}

# (pace class, yield class) -> template. {p} / {y}: points vs baseline; {mostly}: ", mostly <code>";
# {look}: " Look upstream at <cause station>…" (omitted when the code has no cause hint).
TEMPLATES = {
    (NORMAL, NORMAL): "Pace and yield in their usual range.",
    (NORMAL, LOW): "Pace normal, yield {y} pts under baseline{mostly}.{look_not_pace}",
    (NORMAL, HIGH): "Pace normal, yield {y} pts above baseline.",
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


def classify(value, history):
    """low / normal / high against mean ± 1 SD of `history` (needs 2+ points, else normal)."""
    if value is None:
        return None
    pts = [h for h in history if h is not None]
    if len(pts) < 2:
        return NORMAL
    m, sd = mean(pts), stdev(pts)
    if value < m - sd:
        return LOW
    if value > m + sd:
        return HIGH
    return NORMAL


def baseline(history):
    pts = [h for h in history if h is not None]
    if len(pts) < 2:
        return None
    return {"mean": mean(pts), "sd": stdev(pts), "days": len(pts)}


def status(*, no_demand, pace_value, pace_suppressed, yield_class):
    if no_demand:
        return NO_DEMAND
    if pace_suppressed or pace_value is None:
        return PACE_UNAVAILABLE
    if pace_value < 1.0:
        return BEHIND
    if yield_class == LOW:
        return YIELD_PROBLEM
    return ON_PACE


def _pts(x):
    return f"{abs(x) * 100:.1f}"


def sentence(*, status_word, pace_class, yield_class, pace_delta, yield_delta, top_code=None, cause_station=None,
             no_demand_families=(), pace_note=None, bottleneck=None):
    """The summary sentence. pace_delta / yield_delta are fractions vs the baseline mean
    (0.05 = 5 pts). top_code: the line's top scrap code label; cause_station: its static hint.
    pace_note: a sentence saying why pace has no number, used when pace_class is None."""
    mostly = f", mostly {top_code}" if top_code else ""
    look = f" Look upstream at {cause_station}." if cause_station else ""
    look_not_pace = f" Look upstream at {cause_station}, not at pace." if cause_station else ""
    fmt = dict(p=_pts(pace_delta or 0), y=_pts(yield_delta or 0), mostly=mostly, look=look,
               look_not_pace=look_not_pace)
    parts = []
    if no_demand_families:
        parts.append(f"No demand set for {', '.join(no_demand_families)}.")
    if pace_class is None:
        if pace_note:
            parts.append(pace_note)
        parts.append(YIELD_ONLY[yield_class or NORMAL].format(**fmt))
    else:
        parts.append(TEMPLATES[(pace_class, yield_class or NORMAL)].format(**fmt))
    if status_word == BEHIND and bottleneck:
        parts.append(bottleneck)
    return " ".join(parts)


__all__ = ["classify", "baseline", "status", "sentence", "TEMPLATES", "YIELD_ONLY", "NO_DEMAND", "BEHIND",
           "YIELD_PROBLEM", "ON_PACE", "PACE_UNAVAILABLE", "STATUS_KEYS", "LOW", "NORMAL", "HIGH"]
