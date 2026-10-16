"""Dimension grid: printed dimensions read off the drawing's dimension lines.

Architectural dimensions live on dimension lines *outside* the element they
measure, with witness lines dropping to the feature — so the number nearest a
room is rarely its size. Here each dimension text is bound to the axis-aligned
dimension line it sits on, and trusted only when the printed value matches that
line's physical length × scale (the geometry-grounds-the-number seam). A value is
then attributed to an asset whose extent the line's span brackets (`measure_span`).

Pure geometry, no LLM. Vector PDFs, axis-aligned dimensions only; anything
unproven is simply absent, never guessed (→ NEEDS_REVIEW downstream).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from planlint.ingest.vector_geometry import (
    _AXIS_TOL_PT,
    Primitive,
    TextLabel,
    parse_dimension_label,
)

# How far (PDF points) a dimension line may sit from its text, perpendicular to
# the measured axis (the text sits just above/below or beside the line).
_LINE_SEARCH_PT = 20.0
# How closely a dimension's span must bracket an asset's extent to be attributed
# (roughly a wall thickness of slack — face-vs-centerline, snap jitter).
_SPAN_TOL_PT = 10.0

# (offset, lo, hi) for an axis-aligned segment: offset = perpendicular coord,
# [lo, hi] = extent along the segment's own axis.
_Seg = tuple[float, float, float]


@dataclass(frozen=True)
class Dimension:
    """One printed dimension bound to its dimension line. `axis` is the measured
    direction ('h' = along x, 'v' = along y); `[lo, hi]` the span in PDF points;
    `offset` the perpendicular coord of the line; `value_in` the printed value in
    inches, already validated against `(hi - lo) * scale`."""

    axis: str
    lo: float
    hi: float
    offset: float
    value_in: float


def _axis_segments(primitives: list[Primitive]) -> tuple[list[_Seg], list[_Seg]]:
    """Split axis-aligned straight segments into (horizontals, verticals). Curves
    (swing arcs) and degenerate points are excluded."""
    horizontals: list[_Seg] = []
    verticals: list[_Seg] = []
    for p in primitives:
        if p.is_curve:
            continue
        (x0, y0), (x1, y1) = p.p0, p.p1
        if abs(x1 - x0) <= _AXIS_TOL_PT and abs(y1 - y0) > _AXIS_TOL_PT:  # vertical
            verticals.append(((x0 + x1) / 2, min(y0, y1), max(y0, y1)))
        elif abs(y1 - y0) <= _AXIS_TOL_PT and abs(x1 - x0) > _AXIS_TOL_PT:  # horizontal
            horizontals.append(((y0 + y1) / 2, min(x0, x1), max(x0, x1)))
    return horizontals, verticals


def _matches(value_in: float, span_pt: float, scale: float) -> bool:
    """The printed value agrees with the dimension line's physical length × scale."""
    return abs(value_in - span_pt * scale) <= max(2.0, 0.03 * value_in)


def _dimension_for_label(
    label: TextLabel, horizontals: list[_Seg], verticals: list[_Seg], scale: float
) -> Dimension | None:
    """Bind one dimension text to the nearest dimension line whose length validates
    the printed value. Orientation is resolved by which line validates, so a note
    that happens to sit near a line of the wrong length is rejected."""
    value = parse_dimension_label(label.text)
    if value is None or value <= 0:
        return None
    x0, y0, x1, y1 = label.bbox
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    best: Dimension | None = None
    best_perp = _LINE_SEARCH_PT + 1.0
    for offset, lo, hi in horizontals:  # horizontal line → measures x; text above/below
        perp = abs(cy - offset)
        if perp < best_perp and lo - _AXIS_TOL_PT <= cx <= hi + _AXIS_TOL_PT:
            if _matches(value, hi - lo, scale):
                best, best_perp = Dimension("h", lo, hi, offset, value), perp
    for offset, lo, hi in verticals:  # vertical line → measures y; text left/right
        perp = abs(cx - offset)
        if perp < best_perp and lo - _AXIS_TOL_PT <= cy <= hi + _AXIS_TOL_PT:
            if _matches(value, hi - lo, scale):
                best, best_perp = Dimension("v", lo, hi, offset, value), perp
    return best


def build_dimension_grid(
    primitives: list[Primitive], labels: Sequence[TextLabel], scale: float
) -> list[Dimension]:
    """Bind each dimension text to its dimension line, keeping only those whose
    printed value matches the line length × scale. Requires a known `scale`."""
    horizontals, verticals = _axis_segments(primitives)
    grid: list[Dimension] = []
    for label in labels:
        dim = _dimension_for_label(label, horizontals, verticals, scale)
        if dim is not None:
            grid.append(dim)
    return grid


def measure_span(
    grid: Sequence[Dimension], axis: str, lo: float, hi: float, tol: float = _SPAN_TOL_PT
) -> float | None:
    """Printed value (inches) of the dimension on `axis` whose span brackets
    [lo, hi] within `tol` at both ends (tightest match wins). None when nothing
    matches — the asset is then left unmeasured, never guessed."""
    best: float | None = None
    best_err = tol * 2 + 1.0
    for d in grid:
        if d.axis != axis:
            continue
        if abs(d.lo - lo) <= tol and abs(d.hi - hi) <= tol:
            err = abs(d.lo - lo) + abs(d.hi - hi)
            if err < best_err:
                best, best_err = d.value_in, err
    return best
