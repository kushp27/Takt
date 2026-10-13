"""The 14-day sparkline geometry (design review D22).

Two thin lines on one scale: pace in ink, yield in --text-2 grey. Each has a faint baseline band
(mean ± 1 SD, from the API's 14-day baseline) and today's point (the window's last day) is marked.
The accessible name is the API's trend_text; the SVG itself is decorative detail.
"""

from dataclasses import dataclass

WIDTH = 120
HEIGHT = 32
PAD = 3


@dataclass(frozen=True)
class Series:
    points: str  # "x,y x,y ..." for <polyline>; segments split at missing days
    segments: tuple  # (points string, ...)
    band: tuple | None  # (y_top, height) of the ±1 SD band
    last: tuple | None  # (x, y) of today's point, or None when today has no value


@dataclass(frozen=True)
class Spark:
    width: int
    height: int
    pace: Series
    yield_: Series
    empty: bool


def _band(baseline):
    if not baseline:
        return None
    return baseline["mean"] - baseline["sd"], baseline["mean"] + baseline["sd"]


def build(trend, pace_baseline=None, yield_baseline=None, width=WIDTH, height=HEIGHT):
    pace = [t.pace for t in trend]
    yld = [t.yield_ for t in trend]
    values = [v for v in pace + yld if v is not None]
    for b in (_band(pace_baseline), _band(yield_baseline)):
        if b:
            values += list(b)
    if not values:
        empty = Series("", (), None, None)
        return Spark(width, height, empty, empty, True)
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        lo, hi = lo - 0.05, hi + 0.05
    n = max(len(trend) - 1, 1)

    def x(i):
        return round(PAD + i * (width - 2 * PAD) / n, 1)

    def y(v):
        return round(height - PAD - (v - lo) * (height - 2 * PAD) / (hi - lo), 1)

    def series(vals, baseline):
        segs, cur = [], []
        for i, v in enumerate(vals):
            if v is None:
                if cur:
                    segs.append(" ".join(cur))
                cur = []
                continue
            cur.append(f"{x(i)},{y(v)}")
        if cur:
            segs.append(" ".join(cur))
        b = _band(baseline)
        band = None
        if b:
            top, bottom = y(b[1]), y(b[0])
            band = (top, round(max(bottom - top, 1.0), 1))
        last = (x(len(vals) - 1), y(vals[-1])) if vals and vals[-1] is not None else None
        return Series(" ".join(segs), tuple(segs), band, last)

    return Spark(width, height, series(pace, pace_baseline), series(yld, yield_baseline), False)
