"""The display matrix (design Component 4a), applied everywhere: screens, print sheet, lists.

Duration grade (per station-and-part, Component 4) x count trust (per station) -> what a
cell may show. Lane D renders a Cell; it never re-derives these rules.

| | Green | Amber / Not graded yet | Red (incl. batch) |
|---|---|---|---|
| Count-trusted | counts, rates, comparisons, txn gap | counts, rates "n/a" | counts only |
| Not count-trusted | "Counts unverified." no operator rows; out of station pace and bottleneck | same | same |

R3-5 (user decision): the transaction gap shows only on green; the upstream output gap,
tenure under 90 days and the dispute button show on every count-trusted row. A station
that is not count-trusted shows no operator rows and "Counts unverified, keep tally."

Trust words (design review D16): words and a grey outline badge, never fill colour.
"""

from dataclasses import dataclass

from core.models import StationPartGrade

GREEN, AMBER, RED, NOT_GRADED = (StationPartGrade.GREEN, StationPartGrade.AMBER, StationPartGrade.RED,
                                 StationPartGrade.NOT_GRADED)

# D16 badge words and outline glyphs.
BADGES = {
    GREEN: ("Verified", "✓"),
    AMBER: ("Counts only", "#"),
    NOT_GRADED: ("Not graded yet", "…"),
    RED: ("Excluded", "–"),
    "unverified": ("Counts unverified", "?"),
}

# Success criteria + Component 4a: what My Shift and the print sheet say at a station that is
# not count-trusted. Leads keep the sticky note there until trust is established (R3-4).
UNVERIFIED_MESSAGE = "Counts unverified, keep tally."
RATE_NA = "n/a"
GRADE_WEEKS = 4


@dataclass(frozen=True)
class Badge:
    key: str  # green | amber | not_graded | red | unverified
    word: str  # "Verified"
    glyph: str  # "✓"
    detail: str  # full text, e.g. "Not graded yet (needs 4 weeks; 9 days so far)"

    @property
    def text(self):
        return f"{self.word} {self.glyph}"


@dataclass(frozen=True)
class Cell:
    """What one station-part cell may show. All flags are display permissions."""

    grade: str  # green | amber | red | not_graded
    count_trusted: bool
    badge: Badge  # the one badge to show for this cell (Counts unverified wins)
    grade_badge: Badge  # the duration-grade badge, for drill-downs that show both
    show_operator_rows: bool
    show_counts: bool  # operator jobs and quantity (also on the print sheet)
    show_rates: bool  # a numeric rate and rate vs median
    rate_text: str | None  # "n/a" where the rate column shows but has no number; None = no rate column
    show_comparisons: bool  # rate vs median, "worth a conversation"
    show_transaction_gap: bool
    show_upstream_gap: bool
    show_tenure: bool
    show_dispute: bool
    in_station_pace: bool
    in_bottleneck: bool
    on_print_sheet: bool  # operator counts appear on the print sheet
    message: str | None  # station-level text in place of operator rows

    def as_dict(self):
        d = dict(self.__dict__)
        d["badge"] = dict(self.badge.__dict__, text=self.badge.text)
        d["grade_badge"] = dict(self.grade_badge.__dict__, text=self.grade_badge.text)
        return d


def badge(grade, days_of_history=None, count_trusted=True):
    if not count_trusted:
        word, glyph = BADGES["unverified"]
        return Badge("unverified", word, glyph, f"{word} {glyph}")
    grade = grade or NOT_GRADED
    word, glyph = BADGES[grade]
    detail = f"{word} {glyph}"
    if grade == NOT_GRADED:
        days = 0 if days_of_history is None else days_of_history
        # design review D11
        detail = f"Not graded yet (needs {GRADE_WEEKS} weeks; {days} {'day' if days == 1 else 'days'} so far)"
    return Badge(grade, word, glyph, detail)


def cell(grade, count_trusted, days_of_history=None, station_type=None):
    """The matrix cell for a station-part. A missing grade (no StationPartGrade row yet) is
    "not graded"; a batch station is red whatever its stored grade (Component 5)."""
    from core.models import Station

    grade = grade or NOT_GRADED
    if station_type == Station.BATCH:
        grade = RED
    grade_badge = badge(grade, days_of_history)
    if not count_trusted:
        return Cell(
            grade=grade, count_trusted=False, badge=badge(grade, days_of_history, count_trusted=False),
            grade_badge=grade_badge,
            show_operator_rows=False, show_counts=False, show_rates=False, rate_text=None,
            show_comparisons=False, show_transaction_gap=False, show_upstream_gap=False, show_tenure=False,
            show_dispute=False, in_station_pace=False, in_bottleneck=False, on_print_sheet=False,
            message=UNVERIFIED_MESSAGE,
        )
    green = grade == GREEN
    red = grade == RED
    return Cell(
        grade=grade, count_trusted=True, badge=grade_badge, grade_badge=grade_badge,
        show_operator_rows=True, show_counts=True,
        show_rates=green,
        rate_text=None if (green or red) else RATE_NA,  # amber and not graded: "n/a"; red: no rate column
        show_comparisons=green,
        show_transaction_gap=green,
        show_upstream_gap=True, show_tenure=True, show_dispute=True,
        in_station_pace=True, in_bottleneck=True, on_print_sheet=True,
        message=None,
    )


def cell_for(station, grade_row=None):
    """Cell from a Station and its StationPartGrade row (or None)."""
    return cell(
        grade_row.grade if grade_row else None, station.count_trusted,
        grade_row.days_of_history if grade_row else None, station.station_type,
    )


def station_cell(station, grade, days_of_history=None):
    """Station-level cell: the grade covering the majority of the station's volume."""
    return cell(grade, station.count_trusted, days_of_history, station.station_type)


__all__ = ["Badge", "Cell", "badge", "cell", "cell_for", "station_cell", "UNVERIFIED_MESSAGE", "RATE_NA",
           "BADGES"]
