"""Dimension-grid tests: binding a dimension text to its dimension line, the
value↔span×scale validation seam, and span attribution. Pure geometry — no LLM,
no DB. Coordinates are synthetic PDF points; scale is real-inches per point."""

from __future__ import annotations

from planlint.ingest.dimensions import (
    Dimension,
    build_dimension_grid,
    measure_span,
)
from planlint.ingest.vector_geometry import Primitive, TextLabel


def _text(s: str, cx: float, cy: float) -> TextLabel:
    return TextLabel(text=s, bbox=(cx - 6, cy - 4, cx + 6, cy + 4))


# A horizontal dimension line spanning 148 pt, with its text centred above it.
H_LINE = Primitive(p0=(100.0, 50.0), p1=(248.0, 50.0))


def test_binds_dimension_to_its_line():
    grid = build_dimension_grid([H_LINE], [_text('12\'-4"', 174, 40)], scale=1.0)
    assert grid == [Dimension(axis="h", lo=100.0, hi=248.0, offset=50.0, value_in=148.0)]


def test_value_span_mismatch_is_dropped():
    # '36"' sitting on a 148-pt line does not match its length — not a dimension
    # for that line (a mis-placed note or sub-chain fragment), so it is dropped.
    grid = build_dimension_grid([H_LINE], [_text('36"', 174, 40)], scale=1.0)
    assert grid == []


def test_structural_note_is_never_bound():
    # parse_dimension_label rejects the note, so it cannot enter the grid even
    # when it sits right on a line of the "right" length.
    grid = build_dimension_grid([H_LINE], [_text('2X12 JOISTS @ 148" OC', 174, 40)], scale=1.0)
    assert grid == []


def test_measure_span_matches_asset_extent():
    grid = build_dimension_grid([H_LINE], [_text('12\'-4"', 174, 40)], scale=1.0)
    # A room whose x-extent equals the dim span (slightly inset — snap jitter).
    assert measure_span(grid, "h", 105.0, 244.0) == 148.0
    # No dimension brackets a different extent, or the wrong axis.
    assert measure_span(grid, "h", 300.0, 400.0) is None
    assert measure_span(grid, "v", 100.0, 248.0) is None


def test_vertical_dimension():
    v_line = Primitive(p0=(50.0, 100.0), p1=(50.0, 220.0))  # span 120 pt
    grid = build_dimension_grid([v_line], [_text('10\'-0"', 40, 160)], scale=1.0)
    assert grid == [Dimension(axis="v", lo=100.0, hi=220.0, offset=50.0, value_in=120.0)]
    assert measure_span(grid, "v", 100.0, 220.0) == 120.0
