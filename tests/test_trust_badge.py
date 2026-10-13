"""D-T6: trust badges as words plus outline glyphs, from trust.matrix (design review D16, D11)."""

import re
from pathlib import Path

import pytest
from django.conf import settings
from django.template import Context, Template

from trust import matrix

RENDER = Template("{% load takt_ui %}{% trust_badge b %}")


def _badge_html(b):
    return RENDER.render(Context({"b": b})).strip()


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()


@pytest.mark.parametrize("grade,trusted,text", [
    ("green", True, "Verified ✓"),
    ("amber", True, "Counts only #"),
    ("not_graded", True, "Not graded yet (needs 4 weeks; 9 days so far) …"),
    ("red", True, "Excluded –"),
    ("green", False, "Counts unverified ?"),
])
def test_badge_words_and_glyphs(grade, trusted, text):
    cell = matrix.cell(grade, trusted, days_of_history=9)
    html = _badge_html(cell.badge)
    assert _text(html) == text
    assert f'class="badge badge-{cell.badge.key}"' in html
    assert '<span class="badge-glyph" aria-hidden="true">' in html  # the word is the accessible name


def test_batch_station_reads_excluded():
    from core.models import Station

    assert _text(_badge_html(matrix.cell("green", True, station_type=Station.BATCH).badge)) == "Excluded –"


def test_every_grade_is_distinguishable_without_colour():
    texts = {_text(_badge_html(matrix.cell(g, t, 9).badge))
             for g, t in [("green", True), ("amber", True), ("not_graded", True), ("red", True), ("green", False)]}
    assert len(texts) == 5
    glyphs = {matrix.cell(g, t, 9).badge.glyph
              for g, t in [("green", True), ("amber", True), ("not_graded", True), ("red", True), ("green", False)]}
    assert len(glyphs) == 5


def test_badge_css_is_outline_never_fill():
    css = (Path(settings.BASE_DIR) / "web/static/css/tokens.css").read_text()
    rule = re.search(r"\.badge\s*\{([^}]*)\}", css).group(1)
    assert "border: 1px solid" in rule and "background: transparent" in rule
    screens = (Path(settings.BASE_DIR) / "web/static/css/screens.css").read_text()
    for m in re.finditer(r"\.badge[\w-]*[^{]*\{([^}]*)\}", screens):
        assert "background:" not in m.group(1).replace("background: transparent", ""), m.group(0)
