"""Deterministic geometry extraction from PDF pages.

Everything here is pure math over PyMuPDF primitives — no LLM involvement.
The VLM only *classifies* entities; coordinates and measurements come from
the vector geometry whenever the PDF has any.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from planlint.models import BBox, Parameter

# How far (in PDF points) a VLM box is inflated when looking for vector
# geometry to snap to, and how far a dimension label may sit from an asset.
SNAP_INFLATE_PT = 8.0
LABEL_RADIUS_PT = 40.0
# Segments longer than this are structural (walls), not asset geometry —
# excluded from snapping so a door box never unions in a 30-ft wall.
MAX_SNAP_SEGMENT_PT = 100.0

# A room/corridor's extent is bounded by those long wall runs, so it needs the
# opposite treatment from an opening. These govern snapping a space box to walls.
WALL_MIN_PT = 40.0          # a straight run this long is a wall, not clutter
ROOM_EDGE_SEARCH_PT = 24.0  # a room edge may sit this far from its bounding wall
_AXIS_TOL_PT = 2.0          # max deviation for a run to count as axis-aligned


@dataclass(frozen=True)
class Primitive:
    """A line segment in page coordinates (PDF points). `is_curve` marks a chord
    standing in for a bezier (e.g. a door swing arc) — its length is the chord,
    not a real straight edge, so it must not be measured as a clear width."""

    p0: tuple[float, float]
    p1: tuple[float, float]
    is_curve: bool = False

    @property
    def length(self) -> float:
        return ((self.p1[0] - self.p0[0]) ** 2 + (self.p1[1] - self.p0[1]) ** 2) ** 0.5

    @property
    def midpoint(self) -> tuple[float, float]:
        return ((self.p0[0] + self.p1[0]) / 2, (self.p0[1] + self.p1[1]) / 2)


@dataclass(frozen=True)
class TextLabel:
    text: str
    bbox: BBox


def detect_pdf_type(page) -> str:
    """A page with vector drawing commands is 'vector'; otherwise 'raster'."""
    return "vector" if page.get_drawings() else "raster"


def extract_primitives(page) -> tuple[list[Primitive], list[TextLabel]]:
    """Extract straight segments (lines + rectangle edges) and text labels."""
    primitives: list[Primitive] = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            kind = item[0]
            if kind == "l":  # line: ("l", p0, p1)
                p0, p1 = item[1], item[2]
                primitives.append(Primitive(p0=(p0.x, p0.y), p1=(p1.x, p1.y)))
            elif kind == "re":  # rectangle: ("re", rect, ...)
                r = item[1]
                corners = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
                for a, b in zip(corners, corners[1:] + corners[:1]):
                    primitives.append(Primitive(p0=a, p1=b))
            elif kind == "c":  # bezier: approximate by its chord (e.g. door swing)
                p0, p3 = item[1], item[4]
                primitives.append(
                    Primitive(p0=(p0.x, p0.y), p1=(p3.x, p3.y), is_curve=True)
                )

    # Line-level text (blocks merge unrelated labels that share a baseline).
    labels: list[TextLabel] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            text = "".join(span["text"] for span in line["spans"]).strip()
            if text:
                x0, y0, x1, y1 = line["bbox"]
                labels.append(TextLabel(text=text, bbox=(x0, y0, x1, y1)))
    return primitives, labels


# --------------------------------------------------------------------- scale

_SCALE_ARCH = re.compile(
    r"""(?P<num>\d+)\s*/\s*(?P<den>\d+)\s*"?\s*=\s*(?P<feet>\d+)\s*'(?:\s*-?\s*(?P<inches>\d+)\s*"?)?""",
    re.VERBOSE,
)
_SCALE_RATIO = re.compile(r"\b1\s*:\s*(?P<ratio>\d+)\b")


def parse_scale(text: str) -> float | None:
    """Parse a drawing-scale annotation into real-inches-per-PDF-point.

    '1/4" = 1'-0"'  ->  0.25 paper-in = 12 real-in  ->  48 real-in per paper-in
                    ->  48 / 72 real-in per PDF point.
    '1:50'          ->  50 / 72.
    """
    if not text:
        return None
    m = _SCALE_ARCH.search(text)
    if m:
        paper_in = int(m.group("num")) / int(m.group("den"))
        real_in = int(m.group("feet")) * 12 + int(m.group("inches") or 0)
        if paper_in > 0 and real_in > 0:
            return (real_in / paper_in) / 72.0
    m = _SCALE_RATIO.search(text)
    if m:
        ratio = int(m.group("ratio"))
        if ratio > 0:
            return ratio / 72.0
    return None


# --------------------------------------------------------------- dimensions

_DIM_FT_IN = re.compile(r"""(?P<feet>\d+)\s*'\s*-?\s*(?P<inches>\d+)\s*"?""")
_DIM_IN = re.compile(r"""(?P<inches>\d+(?:\.\d+)?)\s*(?:"|\bin\b\.?)""", re.IGNORECASE)

# Signatures that a number lives inside a structural/material *note*, not a
# dimension callout: on-center spacing ('@', 'O.C.') and lumber sections ('2X12').
_ON_CENTER = re.compile(r"@|\bO\.?C\.?\b", re.IGNORECASE)
_LUMBER = re.compile(r"\b\d+\s*[xX]\s*\d+\b")
_ALPHA_RUN = re.compile(r"[A-Za-z]{2,}")
# The only multi-letter words a genuine dimension callout carries: units and
# qualifiers. Anything else beside the number means it's prose (a note).
_DIM_WORDS = {"IN", "FT", "MM", "CM", "MIN", "MAX", "CLR", "TYP", "NOM", "EQ"}


def _is_dimension_note(text: str, matched: str) -> bool:
    """True when `text` is a structural/material note that merely *contains* a
    number ('2X12 JOISTS @ 16" OC', '5/8" GYP BD') rather than being a dimension
    callout — such numbers must never be read as a measurement. A tag like 'D1'
    (letter+digit, no 2-letter alpha run) is allowed; a word like 'JOISTS' is not.
    """
    if _ON_CENTER.search(text) or _LUMBER.search(text):
        return True
    remainder = text.replace(matched, " ", 1)
    return any(
        run.group(0).upper() not in _DIM_WORDS for run in _ALPHA_RUN.finditer(remainder)
    )


def parse_dimension_label(text: str) -> float | None:
    """Find a CAD dimension ('36"', "3'-0\"", '30 in', 'D1 36"') in a label.

    Returns None for structural/material notes that merely contain a number
    (e.g. '2X12 JOISTS @ 16" OC', '5/8" GYP BD') — see `_is_dimension_note`.
    """
    text = text.strip()
    m = _DIM_FT_IN.search(text)
    if m:
        if _is_dimension_note(text, m.group(0)):
            return None
        return int(m.group("feet")) * 12 + int(m.group("inches"))
    m = _DIM_IN.search(text)
    if m:
        if _is_dimension_note(text, m.group(0)):
            return None
        return float(m.group("inches"))
    return None


# Slope annotations: '1:12', '1 in 12', '8.3%'. Bare '/' is excluded so drawing
# scales ('1/4"') aren't misread as slopes.
_SLOPE_RATIO = re.compile(r"(\d+(?:\.\d+)?)\s*(?::|\s+in\s+)\s*(\d+(?:\.\d+)?)", re.IGNORECASE)
_SLOPE_PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*%")


def parse_slope_label(text: str) -> float | None:
    """Grade fraction (rise/run) from a slope annotation: '1:12'/'1 in 12'/'8.3%'
    → 0.083. Orientation-agnostic: a value > 1 is read as run:rise and inverted,
    since a ramp is never steeper than 1:1."""
    m = _SLOPE_PERCENT.search(text)
    if m:
        return float(m.group(1)) / 100.0
    m = _SLOPE_RATIO.search(text)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        if a > 0 and b > 0:
            grade = a / b
            return grade if grade <= 1 else b / a
    return None


# ------------------------------------------------------------------ snapping

def _inflate(box: BBox, by: float) -> BBox:
    x0, y0, x1, y1 = box
    return (x0 - by, y0 - by, x1 + by, y1 + by)


def _contains(box: BBox, point: tuple[float, float]) -> bool:
    x0, y0, x1, y1 = box
    return x0 <= point[0] <= x1 and y0 <= point[1] <= y1


def _touches_text(seg: Primitive, labels: Sequence[TextLabel], pad: float = 2.0) -> bool:
    """True when either endpoint of `seg` sits inside a text label's (inflated)
    bbox — the signature of a callout leader or a label underline, which
    terminate at their text. Walls, door leaves, and swing arcs end at geometry
    (jambs, corners), never at text, so excluding text-terminated segments drops
    annotation without harming real snap targets."""
    for label in labels:
        box = _inflate(label.bbox, pad)
        if _contains(box, seg.p0) or _contains(box, seg.p1):
            return True
    return False


def snap_box(
    vlm_box: BBox, primitives: list[Primitive], labels: Sequence[TextLabel] = ()
) -> tuple[BBox, bool]:
    """Snap an approximate VLM box to the exact vector geometry beneath it.

    Returns (bbox, snapped). When no primitive's midpoint falls inside the
    inflated VLM box, the original box is returned unsnapped. Segments that
    terminate at a text label (callout leaders, label underlines) are excluded
    so the box snaps to real geometry, not annotation.
    """
    search = _inflate(vlm_box, SNAP_INFLATE_PT)
    candidates = [
        p
        for p in primitives
        if p.length <= MAX_SNAP_SEGMENT_PT and not _touches_text(p, labels)
    ]
    nearby = [p for p in candidates if _contains(search, p.midpoint)]
    if not nearby:
        return vlm_box, False

    # One hop of connectivity: pull in segments that share an endpoint with
    # the snapped set (a door's swing chord/arc connects to its leaf but its
    # midpoint often falls outside the label box).
    def touches(a: Primitive, b: Primitive, tol: float = 0.5) -> bool:
        return any(
            abs(pa[0] - pb[0]) <= tol and abs(pa[1] - pb[1]) <= tol
            for pa in (a.p0, a.p1)
            for pb in (b.p0, b.p1)
        )

    connected = [
        p
        for p in candidates
        if p not in nearby and any(touches(p, q) for q in nearby)
    ]
    cluster = nearby + connected
    xs = [c for p in cluster for c in (p.p0[0], p.p1[0])]
    ys = [c for p in cluster for c in (p.p0[1], p.p1[1])]
    return (min(xs), min(ys), max(xs), max(ys)), True


# A double-line (often hatched) wall spans up to this across its section, so the
# room-facing inner line sits within this band of the outer line the first pass may
# have snapped to. A wall corner is called when a parallel wall's endpoint lands
# within _CORNER_TOL_PT of a perpendicular wall's offset.
_WALL_GROUP_PT = 18.0
_CORNER_TOL_PT = 6.0


def _corner_snap(
    edge: float | None,
    runs: list[tuple[float, float, float]],
    anchors: list[float],
    inner: int,
) -> float | None:
    """Refine one room edge to the interior face of its wall, using the corners.

    `runs` are the wall segments parallel to this edge as (offset, lo, hi);
    `anchors` are the offsets of the two perpendicular edges. Among segments within
    a wall-group of `edge` whose endpoint (`lo`/`hi`) coincides with a perpendicular
    wall — a real corner — pick the innermost: `inner=+1` the largest offset (a top
    or left interior face), `-1` the smallest (bottom/right). A double-line wall's
    inner face is often broken at the centre by an opening, so the first pass can't
    reach it by spanning the centre and settles on the continuous outer line; but the
    inner face's segments still *end at the corner walls*, which this keys off. The
    corner requirement also rejects stray non-wall lines. Falls back to `edge` when
    nothing corners (single-line walls already sit on the right line).
    """
    if edge is None or not anchors:
        return edge
    candidates = [
        off
        for off, lo, hi in runs
        if abs(off - edge) <= _WALL_GROUP_PT
        and any(min(abs(lo - a), abs(hi - a)) <= _CORNER_TOL_PT for a in anchors)
    ]
    if not candidates:
        return edge
    return max(candidates) if inner > 0 else min(candidates)


# A bounding wall need not cross the room's centre (an opening — a doorway — often
# sits there); it must merely cover this fraction of the room's extent along the edge.
_ROOM_EDGE_COVER_FRAC = 0.5


def snap_room_box(
    vlm_box: BBox, primitives: list[Primitive], labels: Sequence[TextLabel] = ()
) -> tuple[BBox, bool]:
    """Snap a room/corridor box to the long wall runs that bound it.

    A space is enclosed by long walls, which `snap_box` deliberately excludes as
    structural — so running a room through the opening snap corrupts its box by
    unioning whatever short interior clutter (door leaves, fixtures, dimension
    ticks) happens to sit inside. Here each edge instead moves to the nearest
    axis-aligned **merged wall run** within ROOM_EDGE_SEARCH_PT that covers at least
    _ROOM_EDGE_COVER_FRAC of the room's extent along that edge, then is refined onto
    the wall's room-facing inner face via the corners (`_corner_snap`). Merged runs
    (not raw segments) and fractional coverage (not centre-crossing) matter for real
    drawings: walls arrive fragmented into many short collinear segments, and a
    room's centre commonly lands in a doorway, so a segment/centre test would miss
    the wall entirely and leave the edge unsnapped. Runs that terminate at a text
    label (dimension/label lines) are excluded so an edge doesn't snap to a
    dimension line. Edges with no such wall keep the VLM position, and the box is
    only reported snapped when at least two edges found a wall — a lone match is too
    weak to trust, so we fall back to the (wall-to-wall) VLM box instead.
    """
    x0, y0, x1, y1 = vlm_box
    runs = build_wall_runs(primitives, labels)
    verticals = [r for r in runs if r.orient == "v"]    # offset = x, intervals over y
    horizontals = [r for r in runs if r.orient == "h"]  # offset = y, intervals over x

    def coverage(run: WallRun, lo: float, hi: float) -> float:
        span = max(hi - lo, 1e-6)
        return sum(max(0.0, min(b, hi) - max(a, lo)) for a, b in run.intervals) / span

    def nearest(target: float, candidates: list[WallRun], lo: float, hi: float) -> float | None:
        best: float | None = None
        for r in candidates:
            if abs(r.offset - target) <= ROOM_EDGE_SEARCH_PT and coverage(r, lo, hi) >= _ROOM_EDGE_COVER_FRAC:
                if best is None or abs(r.offset - target) < abs(best - target):
                    best = r.offset
        return best

    left = nearest(x0, verticals, y0, y1)
    right = nearest(x1, verticals, y0, y1)
    top = nearest(y0, horizontals, x0, x1)
    bottom = nearest(y1, horizontals, x0, x1)
    if sum(edge is not None for edge in (left, right, top, bottom)) < 2:
        return vlm_box, False

    # Refine each edge onto the interior wall face using the perpendicular walls as
    # corner anchors (first-pass offsets — within tolerance of the true corner). The
    # runs' individual intervals carry the segment endpoints where the corners live.
    vx = [v for v in (left, right) if v is not None]
    hy = [v for v in (top, bottom) if v is not None]
    htuples = [(r.offset, a, b) for r in horizontals for a, b in r.intervals]
    vtuples = [(r.offset, a, b) for r in verticals for a, b in r.intervals]
    top = _corner_snap(top, htuples, vx, inner=+1)
    bottom = _corner_snap(bottom, htuples, vx, inner=-1)
    left = _corner_snap(left, vtuples, hy, inner=+1)
    right = _corner_snap(right, vtuples, hy, inner=-1)

    return (
        left if left is not None else x0,
        top if top is not None else y0,
        right if right is not None else x1,
        bottom if bottom is not None else y1,
    ), True


# ------------------------------------------------------------- wall runs

_OFFSET_TOL_PT = 2.0  # lines within this coordinate distance are the same wall run
MIN_OPENING_PT = 8.0   # a gap smaller than this is a joint, not an opening
MAX_OPENING_PT = 240.0 # a gap larger than this spans a room, not one opening

# Two guards stop a door/window snapping to a whole wall section: a wall gap is
# rejected when it is wider than SLACK × the VLM box (a far larger feature the box
# didn't claim) OR, when the scale is known, wider than _MAX_OPENING_IN in reality
# (the scale-free MAX_OPENING_PT alone lets a whole wall through at a small scale).
OPENING_SIZE_SLACK = 3.0
_MAX_OPENING_IN = 200.0      # ~16.7 ft — allows the widest garage door, not a wall
OPENING_THICK_MAX_PT = 20.0  # fallback cap on an opening box's wall-thickness
_WALL_PAIR_BAND_PT = 24.0    # the opposite wall face sits within this of the run
_OPENING_REPOSITION_PT = 24.0  # a gap this near a mis-placed box may host it


@dataclass(frozen=True)
class OpeningResult:
    """Outcome of judging a claimed opening against the wall geometry.
    kind: 'snapped' (real flanked gap — act on bbox/width_pt),
          'refuted' (wall solid across, or a closed/hatched solid occupies it),
          'unknown' (no determinable wall run — keep the VLM box for review)."""

    kind: str
    bbox: BBox | None = None
    width_pt: float | None = None


@dataclass(frozen=True)
class WallRun:
    """One straight wall line: orientation ('h' = constant y, 'v' = constant x),
    its offset (that constant), and the merged intervals occupied by wall along
    the run's axis (x for 'h', y for 'v')."""

    orient: str
    offset: float
    intervals: tuple[tuple[float, float], ...]


def _merge_intervals(spans: list[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    spans = sorted(spans)
    merged: list[tuple[float, float]] = []
    for lo, hi in spans:
        if merged and lo <= merged[-1][1] + _AXIS_TOL_PT:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return tuple(merged)


def build_wall_runs(
    primitives: list[Primitive], labels: Sequence[TextLabel] = ()
) -> list[WallRun]:
    """Group long, straight, axis-aligned wall segments into runs by orientation
    and offset (curves and text-terminated segments excluded)."""
    buckets: dict[tuple[str, int], tuple[float, list[tuple[float, float]]]] = {}
    for p in primitives:
        if p.is_curve or p.length < WALL_MIN_PT or _touches_text(p, labels):
            continue
        (ax0, ay0), (ax1, ay1) = p.p0, p.p1
        if abs(ax1 - ax0) <= _AXIS_TOL_PT:  # vertical
            offset = (ax0 + ax1) / 2
            lo, hi = sorted((ay0, ay1))
            orient = "v"
        elif abs(ay1 - ay0) <= _AXIS_TOL_PT:  # horizontal
            offset = (ay0 + ay1) / 2
            lo, hi = sorted((ax0, ax1))
            orient = "h"
        else:
            continue
        key = (orient, round(offset / _OFFSET_TOL_PT))
        rep, spans = buckets.setdefault(key, (offset, []))
        spans.append((lo, hi))
    return [
        WallRun(orient=key[0], offset=rep, intervals=_merge_intervals(spans))
        for key, (rep, spans) in buckets.items()
    ]


def _gap_in_run(run: WallRun, span_lo: float, span_hi: float) -> tuple[float, float] | None:
    """The gap between two consecutive occupied intervals that overlaps the
    claimed span — flanked by wall on both sides by construction. Rejects gaps
    outside the plausible opening size."""
    ivs = sorted(run.intervals)
    for (_, a_hi), (b_lo, _) in zip(ivs, ivs[1:]):
        gap_lo, gap_hi = a_hi, b_lo
        if gap_hi <= gap_lo:
            continue
        overlaps = gap_lo < span_hi and gap_hi > span_lo
        if overlaps and MIN_OPENING_PT <= (gap_hi - gap_lo) <= MAX_OPENING_PT:
            return (gap_lo, gap_hi)
    return None


_HATCH_MIN_SEGMENTS = 5  # this many short interior strokes reads as fill/hatching


def _gap_is_occupied(box: BBox, primitives: list[Primitive]) -> bool:
    """True when the gap interior holds a closed/hatched solid (a chimney or
    pier) rather than being empty except for glazing lines parallel to the wall.
    Interior strokes are counted; a run of short, non-axis-aligned or crossing
    segments is hatching/fill."""
    interior = [p for p in primitives if not p.is_curve and _contains(box, p.midpoint)]
    hatch = 0
    for p in interior:
        (x0, y0), (x1, y1) = p.p0, p.p1
        axis_aligned = abs(x1 - x0) <= _AXIS_TOL_PT or abs(y1 - y0) <= _AXIS_TOL_PT
        if not axis_aligned:
            hatch += 1
    return hatch >= _HATCH_MIN_SEGMENTS


_RUN_NEAR_PT = 12.0  # a wall run this close to the box (perpendicular) hosts the opening


def _candidate_gaps(
    run: WallRun, span_lo: float, span_hi: float, vlm_size: float,
    scale: float | None = None,
) -> list[tuple[float, float, float]]:
    """Corroborated opening gaps in `run`, as (gap_lo, gap_hi, distance): distance
    0 means the gap overlaps the claimed span. A gap is dropped when its size is
    implausible, wider than OPENING_SIZE_SLACK × the VLM box (a larger feature the
    box didn't claim), or — when the scale is known — wider than _MAX_OPENING_IN in
    reality. Together these keep a door from snapping to a whole wall."""
    out: list[tuple[float, float, float]] = []
    ivs = sorted(run.intervals)
    for (_, a_hi), (b_lo, _) in zip(ivs, ivs[1:]):
        gap_lo, gap_hi = a_hi, b_lo
        width = gap_hi - gap_lo
        if not (MIN_OPENING_PT <= width <= MAX_OPENING_PT):
            continue
        if width > OPENING_SIZE_SLACK * vlm_size + _OFFSET_TOL_PT:
            continue
        if scale is not None and width * scale > _MAX_OPENING_IN:
            continue
        distance = max(gap_lo - span_hi, span_lo - gap_hi, 0.0)
        out.append((gap_lo, gap_hi, distance))
    return out


def _wall_thickness_at(
    run: WallRun, gap: tuple[float, float], runs: list[WallRun], fallback_cross: float
) -> float:
    """Thickness of the wall the opening cuts through: the distance to the nearest
    parallel wall run (the wall's other face) that runs alongside the gap, within
    _WALL_PAIR_BAND_PT. Falls back to the capped VLM cross-dimension when the wall
    is drawn as a single line, so the box hugs the wall instead of the swing arc."""
    gap_lo, gap_hi = gap
    nearest: float | None = None
    for other in runs:
        if other.orient != run.orient or other.offset == run.offset:
            continue
        distance = abs(other.offset - run.offset)
        if not (_AXIS_TOL_PT < distance <= _WALL_PAIR_BAND_PT):
            continue
        # the other face must run alongside the opening (same wall), not be an
        # unrelated parallel line elsewhere on the sheet.
        alongside = any(
            lo < gap_hi + _WALL_PAIR_BAND_PT and hi > gap_lo - _WALL_PAIR_BAND_PT
            for lo, hi in other.intervals
        )
        if alongside and (nearest is None or distance < nearest):
            nearest = distance
    if nearest is not None:
        return nearest
    return max(min(fallback_cross, OPENING_THICK_MAX_PT), MIN_OPENING_PT)


def classify_opening_vector(
    vlm_box: BBox, primitives: list[Primitive], labels: Sequence[TextLabel] = (),
    scale: float | None = None,
) -> OpeningResult:
    """Judge a claimed door/window box against vector wall geometry.

    The snapped box is grounded in the flanked wall gap, but only when that gap is
    comparable in size to the VLM box and plausible for a real opening (a far larger
    gap is a different feature, not this opening); its thickness comes from the wall,
    not the swing-inflated box. A box that landed beside the opening is repositioned
    onto a single nearby gap. Only positive solid evidence (hatching/fill = a chimney
    or pier) refutes an opening; an un-cut or unconfirmable wall yields 'unknown'
    (kept for review), never a drop, so a real door over a continuous wall isn't lost."""
    x0, y0, x1, y1 = vlm_box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    vlm_size = max(x1 - x0, y1 - y0)
    runs = build_wall_runs(primitives, labels)

    best: tuple[float, WallRun, tuple[float, float], str] | None = None
    for run in runs:
        if run.orient == "h":
            if abs(run.offset - cy) > (y1 - y0) / 2 + _RUN_NEAR_PT:
                continue
            span_lo, span_hi, axis = x0, x1, "x"
        else:
            if abs(run.offset - cx) > (x1 - x0) / 2 + _RUN_NEAR_PT:
                continue
            span_lo, span_hi, axis = y0, y1, "y"

        candidates = _candidate_gaps(run, span_lo, span_hi, vlm_size, scale)
        overlapping = [g for g in candidates if g[2] == 0.0]
        if overlapping:  # the box sits over a real gap — take the best-overlapping
            gap_lo, gap_hi, _ = max(
                overlapping, key=lambda g: min(g[1], span_hi) - max(g[0], span_lo)
            )
        else:  # box beside the opening: reposition only onto a *single* near gap
            near = [g for g in candidates if g[2] <= _OPENING_REPOSITION_PT]
            if len(near) != 1:
                continue
            gap_lo, gap_hi, _ = near[0]

        wall_dist = abs(run.offset - (cy if run.orient == "h" else cx))
        if best is None or wall_dist < best[0]:
            best = (wall_dist, run, (gap_lo, gap_hi), axis)

    if best is not None:
        _, run, (gap_lo, gap_hi), axis = best
        vlm_cross = (y1 - y0) if axis == "x" else (x1 - x0)
        thickness = _wall_thickness_at(run, (gap_lo, gap_hi), runs, vlm_cross)
        if axis == "x":
            bbox = (gap_lo, run.offset - thickness / 2, gap_hi, run.offset + thickness / 2)
        else:
            bbox = (run.offset - thickness / 2, gap_lo, run.offset + thickness / 2, gap_hi)
        if _gap_is_occupied(bbox, primitives):
            return OpeningResult(kind="refuted")  # a solid (hatched) shape fills the gap
        return OpeningResult(kind="snapped", bbox=bbox, width_pt=gap_hi - gap_lo)

    # No confirmable gap. Refute only on positive solid evidence in the claimed
    # box (hatching = chimney/pier); otherwise keep for review.
    if _gap_is_occupied(vlm_box, primitives):
        return OpeningResult(kind="refuted")
    return OpeningResult(kind="unknown")


# --------------------------------------------------------------- measurement

def _box_distance(a: BBox, b: BBox) -> float:
    """Gap between two boxes (0 when they overlap)."""
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return (dx * dx + dy * dy) ** 0.5


def measure_asset(
    asset_box: BBox,
    primitives: list[Primitive],
    labels: list[TextLabel],
    scale: float | None,
) -> tuple[dict[Parameter, float], bool] | None:
    """Derive real-world measurements for an asset.

    Priority: (1) a dimension label within LABEL_RADIUS_PT of the asset,
    (2) longest straight segment inside the box × scale. Returns
    (measurements, from_label) or None when nothing measurable exists.
    """
    best_label: tuple[float, float] | None = None  # (distance, inches)
    for label in labels:
        inches = parse_dimension_label(label.text)
        if inches is None:
            continue
        distance = _box_distance(asset_box, label.bbox)
        if distance <= LABEL_RADIUS_PT and (best_label is None or distance < best_label[0]):
            best_label = (distance, inches)
    if best_label is not None:
        return {Parameter.CLEAR_WIDTH: best_label[1]}, True

    if scale is not None:
        search = _inflate(asset_box, SNAP_INFLATE_PT)
        # Curve chords (door swing arcs) are excluded: a 90° arc's chord is
        # √2× its radius, so measuring it as the width over-reports a 36" door
        # as ~51". The door leaf — a straight line the width of the opening —
        # is the right segment to measure.
        inside = [p for p in primitives if not p.is_curve and _contains(search, p.midpoint)]
        if inside:
            longest = max(p.length for p in inside)
            if longest > 0:
                return {Parameter.CLEAR_WIDTH: longest * scale}, False

    return None
