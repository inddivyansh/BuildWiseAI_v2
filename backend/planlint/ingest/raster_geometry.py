"""Raster geometry: deterministic OpenCV analysis of scanned plan pages.

The raster analog of vector_geometry. From the rendered page image it builds
a wall mask (thick strokes survive; text, hatching, and dimension lines are
morphologically removed), then:

- snaps VLM room boxes to flood-filled room interiors (the VLM only has to
  land a point inside the room; extents come from the pixels),
- snaps opening boxes (doors/windows) to the actual gap in the wall run and
  measures their widths,
- reports openings whose claimed span shows no wall gap ("blocked" —
  informational, since dense window symbols and wall piers look alike here),
- converts room interiors to floor areas when a drawing scale is known.

Pure pixel arithmetic — no models, fully deterministic. All public functions
speak PDF points; pixels are internal.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from planlint.models import BBox

# Wall-thickness estimate bounds, in pixels of the analyzed image.
_MIN_THICKNESS_PX = 3
_MAX_THICKNESS_PX = 40
# A wall component must be at least this long (px) to be kept as wall.
_MIN_WALL_COMPONENT_PX = 40
# Cap on the wall-opening kernel. The opening erases strokes thinner than itself;
# sizing it to half the GLOBAL thickness (dominated by hatched exterior walls)
# erases thin interior partitions, so cap it here — a partition this wide survives.
_MAX_OPEN_PX = 5
# Flood fills larger than this fraction of the page leaked outside the plan.
_MAX_ROOM_PAGE_FRACTION = 0.5
# How far (in wall thicknesses) beyond a claimed opening we require flanking
# wall evidence before judging the claim.
_FLANK_REACH = 6.0
_SQIN_TO_SQM = 0.00064516


@dataclass
class RasterAnalysis:
    """Per-page pixel analysis. Arrays are uint8 {0, 255}."""

    ink: np.ndarray
    walls: np.ndarray
    thickness_px: int
    px_per_point: float

    @property
    def height(self) -> int:
        return int(self.walls.shape[0])

    @property
    def width(self) -> int:
        return int(self.walls.shape[1])


@dataclass(frozen=True)
class OpeningResult:
    """Outcome of judging a claimed opening against the wall mask.

    - "snapped": a genuine flanked gap — strong evidence; act on it.
    - "blocked": the consolidated wall runs continuous across the claim.
      Informational only: a hatched pier and a window with dense frame
      symbols are indistinguishable here, so callers must NOT penalize.
    - "unknown": no flanked wall run under the claim; nothing to judge.
    """

    kind: str  # "snapped" | "blocked" | "unknown"
    bbox: BBox | None = None
    width_pt: float | None = None


def analyze_page_image(png_bytes: bytes, px_per_point: float) -> RasterAnalysis | None:
    """Build ink and wall masks for one rendered page. Returns None when the
    image cannot be decoded or contains no meaningful ink."""
    data = np.frombuffer(png_bytes, dtype=np.uint8)
    gray = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if gray is None or gray.size == 0:
        return None

    # Ink mask: adaptive threshold survives uneven scan exposure.
    ink = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, 51, 15
    )
    if int(np.count_nonzero(ink)) < 100:
        return None

    # Consolidate wall bands: hatched/double-line walls become solid after a
    # closing whose kernel spans the hatch spacing.
    close_k = _kernel(9)
    solid = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, close_k)

    # Estimate wall thickness from the stroke-width distribution of the
    # consolidated image (distance transform doubles to stroke width).
    dist = cv2.distanceTransform(solid, cv2.DIST_L2, 5)
    widths = dist[dist > 0.5] * 2.0
    if widths.size == 0:
        return None
    thickness = int(np.clip(np.percentile(widths, 90), _MIN_THICKNESS_PX, _MAX_THICKNESS_PX))

    # Walls = strokes at least ~half the wall thickness wide, but the kernel is
    # capped (_MAX_OPEN_PX) so a thin interior partition isn't erased when the
    # thickness is set by far heavier exterior walls.
    open_k = _kernel(max(3, min(int(thickness * 0.5), _MAX_OPEN_PX)))
    walls = cv2.morphologyEx(solid, cv2.MORPH_OPEN, open_k)
    # …that are also long: drop text blobs and symbol fragments.
    count, cc_labels, stats, _ = cv2.connectedComponentsWithStats(walls, connectivity=8)
    keep = np.zeros_like(walls)
    for i in range(1, count):
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        if max(w, h) >= _MIN_WALL_COMPONENT_PX:
            keep[cc_labels == i] = 255
    return RasterAnalysis(
        ink=ink, walls=keep, thickness_px=thickness, px_per_point=px_per_point
    )


def snap_room(
    analysis: RasterAnalysis,
    box_pt: BBox,
    plugs: list[BBox] = [],
    exclude_points_pt: list[tuple[float, float]] = [],
) -> tuple[BBox, float] | None:
    """Flood-fill the room interior around the box's center and return
    (bbox in points, interior area in points²), or None when no bounded
    region is found.

    `plugs` are the page's detected openings (doors/windows, snapped or as
    claimed by the VLM): each is sealed into the wall barrier so the fill
    cannot escape through them — the accurate way to bound a room, since it
    only closes real openings instead of guessing with big morphology.
    Directional closings remain as a guarded fallback for openings the VLM
    missed. A fill is accepted only if it stays on-page, is room-sized, and
    covers most of the claimed box."""
    t = analysis.thickness_px
    cx, cy = _center_px(analysis, box_pt)
    ppp = analysis.px_per_point
    box_area_pt2 = max((box_pt[2] - box_pt[0]) * (box_pt[3] - box_pt[1]), 1.0)

    plugged = analysis.walls.copy()
    for plug in plugs:
        x0, y0, x1, y1 = (int(round(v * ppp)) for v in _inflate(plug, t / ppp))
        cv2.rectangle(plugged, (x0, y0), (x1, y1), 255, thickness=-1)
    # Seal hairline scan breaks without distorting geometry.
    plugged = cv2.morphologyEx(plugged, cv2.MORPH_CLOSE, _kernel(max(t // 2, 3)))

    def directional_close(base: np.ndarray, reach: int) -> np.ndarray:
        length = 2 * reach + 1
        h = cv2.morphologyEx(
            base, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1))
        )
        v = cv2.morphologyEx(
            base, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (1, length))
        )
        return cv2.bitwise_or(h, v)

    attempts = [plugged, directional_close(plugged, 2 * t), directional_close(plugged, 4 * t)]
    for barrier in attempts:
        seed = _nearest_free(barrier, cx, cy, reach=4 * t)
        if seed is None:
            continue
        fill = _flood(barrier, seed)
        if fill is None:
            continue
        area_pt2 = float(np.count_nonzero(fill)) / (ppp * ppp)
        frac = float(np.count_nonzero(fill)) / fill.size
        if frac > _MAX_ROOM_PAGE_FRACTION or _touches_border(fill):
            continue  # leaked off the plan — try a stronger barrier
        if area_pt2 > 2.5 * box_area_pt2:
            # Swallowed neighboring space (e.g. rooms separated by thin
            # partition strokes the wall mask cannot hold). Refusing beats
            # returning a merged, wrong-but-confident region.
            continue
        ys, xs = np.nonzero(fill)
        bbox = (
            float(xs.min()) / ppp,
            float(ys.min()) / ppp,
            float(xs.max() + 1) / ppp,
            float(ys.max() + 1) / ppp,
        )
        if _overlap_fraction(bbox, box_pt) < 0.7:
            continue  # a partition artifact, not the claimed room
        if any(
            fill[int(np.clip(py * ppp, 0, analysis.height - 1)),
                 int(np.clip(px * ppp, 0, analysis.width - 1))]
            for px, py in exclude_points_pt
        ):
            continue  # the fill swallowed another claimed room — a merge
        return bbox, area_pt2
    return None


def classify_opening(analysis: RasterAnalysis, box_pt: BBox) -> OpeningResult:
    """Judge a claimed door/window against the wall mask.

    - "snapped": the box sits on a genuine gap in a wall run — bbox tightened
      to the gap span × wall thickness, width measured.
    - "refuted": the wall runs solid across the claimed opening (e.g. a
      hatched pier misread as a window).
    - "unknown": no flanked wall run under the box; nothing to judge against.
    """
    walls = analysis.walls
    t = analysis.thickness_px
    ppp = analysis.px_per_point
    x0, y0, x1, y1 = (int(round(v * ppp)) for v in box_pt)
    cx = int(np.clip((x0 + x1) // 2, 0, analysis.width - 1))
    cy = int(np.clip((y0 + y1) // 2, 0, analysis.height - 1))
    reach = int(_FLANK_REACH * t)

    for axis in ("h", "v"):
        if axis == "h":
            lo = max(cy - int(1.5 * t), 0)
            hi = min(cy + int(1.5 * t) + 1, analysis.height)
            profile = walls[lo:hi, :].max(axis=0)  # wall presence per column
            span_lo, span_hi, center = x0, x1, cx
        else:
            lo = max(cx - int(1.5 * t), 0)
            hi = min(cx + int(1.5 * t) + 1, analysis.width)
            profile = walls[:, lo:hi].max(axis=1)  # wall presence per row
            span_lo, span_hi, center = y0, y1, cy
        present = profile > 0

        left_flank = present[max(span_lo - reach, 0) : span_lo].any()
        right_flank = present[span_hi : span_hi + reach].any()
        if not (left_flank and right_flank):
            continue  # no wall run through this box along this axis

        # Find the gap containing (or nearest inside) the claimed span.
        gap = _gap_around(present, span_lo, span_hi, center, min_len=max(int(0.6 * t), 2))
        if gap is None:
            return OpeningResult(kind="blocked")
        g0, g1 = gap
        width_pt = (g1 - g0) / ppp
        band_center = (lo + hi) / 2.0
        half = 1.5 * t
        if axis == "h":
            bbox = (g0 / ppp, (band_center - half) / ppp, g1 / ppp, (band_center + half) / ppp)
        else:
            bbox = ((band_center - half) / ppp, g0 / ppp, (band_center + half) / ppp, g1 / ppp)
        return OpeningResult(kind="snapped", bbox=bbox, width_pt=width_pt)

    return OpeningResult(kind="unknown")


def fill_area_to_m2(area_pt2: float, scale_in_per_pt: float) -> float:
    """Room interior area in points² → square metres via the drawing scale."""
    return area_pt2 * (scale_in_per_pt**2) * _SQIN_TO_SQM


def area_agreement(printed_m2: float, computed_m2: float, tolerance: float = 0.25) -> bool:
    """Do the printed floor area and the pixel-derived one corroborate?"""
    if printed_m2 <= 0:
        return False
    return abs(computed_m2 - printed_m2) / printed_m2 <= tolerance


# Reconciling the VLM room box with the flood-fill box, per edge.
_ROOM_EDGE_AGREE_T = 1.0  # × wall thickness: within this the two boxes agree
_WALL_BACK_FRAC = 0.35    # a fill edge is wall-backed if this fraction of its span is wall


def reconcile_room_box(
    analysis: RasterAnalysis, vlm_box: BBox, fill_box: BBox
) -> BBox:
    """Combine the VLM room box with the flood-fill box edge by edge.

    Neither box is uniformly right: the flood fill hugs the true walls where they
    are solid but escapes through door gaps (an edge left floating in open space);
    the VLM box never leaks but can sit inset from the walls. Magnitude can't tell a
    legitimate expansion to a far wall from a leak through a door — they're the same
    size — so each edge is judged by whether a **wall actually backs it**:

    - the two agree (within a wall thickness) → keep the clean VLM edge;
    - else, a wall run backs the fill edge over its span → trust the fill (it found
      the wall the VLM box missed, inward or outward);
    - else, the fill edge floats in open space → it leaked; keep the VLM edge.
    """
    t_pt = analysis.thickness_px / analysis.px_per_point
    agree = _ROOM_EDGE_AGREE_T * t_pt
    fx0, fy0, fx1, fy1 = fill_box
    out = list(vlm_box)
    # (index into the box, edge is vertical, span of the perpendicular fill extent)
    edges = [
        (0, True, fy0, fy1),   # left
        (1, False, fx0, fx1),  # top
        (2, True, fy0, fy1),   # right
        (3, False, fx0, fx1),  # bottom
    ]
    for i, vertical, span_lo, span_hi in edges:
        v, f = vlm_box[i], fill_box[i]
        if abs(f - v) <= agree:
            out[i] = v
        elif _edge_wall_fraction(analysis, f, span_lo, span_hi, vertical) >= _WALL_BACK_FRAC:
            out[i] = f
        else:
            out[i] = v
    return (out[0], out[1], out[2], out[3])


# ------------------------------------------------------------------ helpers


def _edge_wall_fraction(
    analysis: RasterAnalysis, offset: float, span_lo: float, span_hi: float, vertical: bool
) -> float:
    """Fraction of a box edge's span that sits on a wall in the mask. `offset` is
    the edge's constant coordinate (x for a vertical edge, y for a horizontal one);
    the span is the edge's extent along the other axis. A wall within ±thickness of
    the edge counts, so a real wall a stroke's-width off the edge still corroborates."""
    ppp, t = analysis.px_per_point, analysis.thickness_px
    walls = analysis.walls
    o = int(round(offset * ppp))
    lo, hi = int(round(span_lo * ppp)), int(round(span_hi * ppp))
    if vertical:
        x0, x1 = max(o - t, 0), min(o + t + 1, analysis.width)
        lo, hi = max(lo, 0), min(hi, analysis.height)
        if hi <= lo or x1 <= x0:
            return 0.0
        band = walls[lo:hi, x0:x1].max(axis=1)
    else:
        y0, y1 = max(o - t, 0), min(o + t + 1, analysis.height)
        lo, hi = max(lo, 0), min(hi, analysis.width)
        if hi <= lo or y1 <= y0:
            return 0.0
        band = walls[y0:y1, lo:hi].max(axis=0)
    return float(np.count_nonzero(band)) / band.size if band.size else 0.0


def _kernel(size: int) -> np.ndarray:
    size = max(int(size), 1)
    return cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))


def _inflate(box: BBox, by: float) -> BBox:
    x0, y0, x1, y1 = box
    return (x0 - by, y0 - by, x1 + by, y1 + by)


def _overlap_fraction(container: BBox, box: BBox) -> float:
    """Fraction of `box`'s area covered by `container`."""
    x0 = max(container[0], box[0])
    y0 = max(container[1], box[1])
    x1 = min(container[2], box[2])
    y1 = min(container[3], box[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    box_area = max((box[2] - box[0]) * (box[3] - box[1]), 1e-6)
    return ((x1 - x0) * (y1 - y0)) / box_area


def _center_px(analysis: RasterAnalysis, box_pt: BBox) -> tuple[int, int]:
    ppp = analysis.px_per_point
    cx = int(np.clip((box_pt[0] + box_pt[2]) / 2 * ppp, 0, analysis.width - 1))
    cy = int(np.clip((box_pt[1] + box_pt[3]) / 2 * ppp, 0, analysis.height - 1))
    return cx, cy


def _nearest_free(closed_walls: np.ndarray, cx: int, cy: int, reach: int) -> tuple[int, int] | None:
    """The free pixel nearest to (cx, cy) within `reach`, or None."""
    if closed_walls[cy, cx] == 0:
        return cx, cy
    h, w = closed_walls.shape
    y0, y1 = max(cy - reach, 0), min(cy + reach + 1, h)
    x0, x1 = max(cx - reach, 0), min(cx + reach + 1, w)
    window = closed_walls[y0:y1, x0:x1]
    ys, xs = np.nonzero(window == 0)
    if ys.size == 0:
        return None
    d2 = (ys + y0 - cy) ** 2 + (xs + x0 - cx) ** 2
    best = int(np.argmin(d2))
    return int(xs[best] + x0), int(ys[best] + y0)


def _flood(closed_walls: np.ndarray, seed: tuple[int, int]) -> np.ndarray | None:
    """Region of free space connected to seed, as uint8 {0,255}."""
    h, w = closed_walls.shape
    mask = np.zeros((h + 2, w + 2), dtype=np.uint8)
    image = closed_walls.copy()
    flags = 4 | cv2.FLOODFILL_MASK_ONLY | (255 << 8)
    cv2.floodFill(image, mask, seed, 0, loDiff=0, upDiff=0, flags=flags)
    region = mask[1:-1, 1:-1]
    if not region.any():
        return None
    return region


def _touches_border(region: np.ndarray) -> bool:
    return bool(
        region[0, :].any() or region[-1, :].any() or region[:, 0].any() or region[:, -1].any()
    )


def _gap_around(
    present: np.ndarray, span_lo: int, span_hi: int, center: int, min_len: int
) -> tuple[int, int] | None:
    """The run of wall-free positions overlapping [span_lo, span_hi],
    anchored at `center` when it is free, else at the largest free run inside
    the span. Returns (start, end) or None when the wall is solid there."""
    n = present.size
    span_lo = int(np.clip(span_lo, 0, n - 1))
    span_hi = int(np.clip(span_hi, span_lo + 1, n))
    center = int(np.clip(center, span_lo, span_hi - 1))

    anchor = None
    if not present[center]:
        anchor = center
    else:
        free = ~present[span_lo:span_hi]
        if not free.any():
            return None
        # Largest free run inside the claimed span.
        best_len, best_start, run_start = 0, None, None
        for i, is_free in enumerate(free):
            if is_free and run_start is None:
                run_start = i
            if (not is_free or i == free.size - 1) and run_start is not None:
                end = i + 1 if is_free else i
                if end - run_start > best_len:
                    best_len, best_start = end - run_start, run_start
                run_start = None
        if best_start is None or best_len < min_len:
            return None
        anchor = span_lo + best_start + best_len // 2

    g0 = anchor
    while g0 > 0 and not present[g0 - 1]:
        g0 -= 1
    g1 = anchor
    while g1 < n - 1 and not present[g1 + 1]:
        g1 += 1
    g1 += 1
    if g1 - g0 < min_len:
        return None
    return g0, g1
