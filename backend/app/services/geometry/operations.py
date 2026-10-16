"""
Boolean operations, buffering, and containment utilities for floor plan geometry.

Wraps Shapely operations with floor-plan-specific logic:
- Union/intersect/difference with validity guarantees
- Polygon clipping to buildable envelope
- Buffer operations for wall thickness
- Area-preserving polygon splitting and merging
"""

from shapely.geometry import Polygon, MultiPolygon, LineString, box
from shapely.ops import unary_union, split
from shapely.validation import make_valid
import numpy as np


def safe_polygon(polygon: Polygon) -> Polygon:
    """Ensure a polygon is valid; repair if needed."""
    if polygon.is_valid:
        return polygon
    repaired = make_valid(polygon)
    if isinstance(repaired, MultiPolygon):
        return max(repaired.geoms, key=lambda g: g.area)
    return repaired


def boolean_union(polygons: list[Polygon]) -> Polygon:
    """Union multiple polygons into one, returning the largest contiguous region."""
    result = unary_union([safe_polygon(p) for p in polygons])
    if isinstance(result, MultiPolygon):
        return max(result.geoms, key=lambda g: g.area)
    return result


def boolean_intersection(a: Polygon, b: Polygon) -> Polygon | None:
    """Intersect two polygons. Returns None if no meaningful overlap."""
    a, b = safe_polygon(a), safe_polygon(b)
    result = a.intersection(b)
    if result.is_empty or result.area < 1e-6:
        return None
    if isinstance(result, MultiPolygon):
        return max(result.geoms, key=lambda g: g.area)
    if isinstance(result, Polygon):
        return result
    return None


def boolean_difference(a: Polygon, b: Polygon) -> Polygon | None:
    """Subtract b from a. Returns None if nothing remains."""
    a, b = safe_polygon(a), safe_polygon(b)
    result = a.difference(b)
    if result.is_empty or result.area < 1e-6:
        return None
    if isinstance(result, MultiPolygon):
        return max(result.geoms, key=lambda g: g.area)
    if isinstance(result, Polygon):
        return result
    return None


def clip_to_boundary(polygon: Polygon, boundary: Polygon) -> Polygon:
    """Clip a polygon to fit within a boundary (buildable envelope)."""
    result = boolean_intersection(safe_polygon(polygon), safe_polygon(boundary))
    if result is None:
        return polygon  # fallback: return original if clipping removes everything
    return result


def buffer_polygon(polygon: Polygon, distance_m: float) -> Polygon:
    """
    Buffer (expand/shrink) a polygon by a distance in meters.
    Positive = expand, negative = shrink.
    Uses flat end caps for architectural geometry.
    """
    polygon = safe_polygon(polygon)
    result = polygon.buffer(distance_m, cap_style="flat", join_style="mitre")
    if result.is_empty:
        return polygon
    if isinstance(result, MultiPolygon):
        return max(result.geoms, key=lambda g: g.area)
    return result


def wall_centerline_to_polygon(
    start: tuple[float, float],
    end: tuple[float, float],
    thickness_m: float = 0.15,
) -> Polygon:
    """Convert a wall centerline (two points) into a rectangular polygon."""
    line = LineString([start, end])
    return line.buffer(thickness_m / 2, cap_style="flat")


def split_polygon_horizontal(polygon: Polygon, ratio: float = 0.5) -> tuple[Polygon, Polygon]:
    """
    Split a polygon horizontally at a given ratio (0-1) of its bounding box height.
    Returns (bottom, top) halves.
    """
    polygon = safe_polygon(polygon)
    minx, miny, maxx, maxy = polygon.bounds
    split_y = miny + (maxy - miny) * ratio

    blade = LineString([(minx - 1, split_y), (maxx + 1, split_y)])
    parts = split(polygon, blade)

    if len(parts.geoms) < 2:
        return polygon, Polygon()

    sorted_parts = sorted(parts.geoms, key=lambda g: g.centroid.y)
    bottom = sorted_parts[0] if isinstance(sorted_parts[0], Polygon) else Polygon()
    top = sorted_parts[-1] if isinstance(sorted_parts[-1], Polygon) else Polygon()
    return bottom, top


def split_polygon_vertical(polygon: Polygon, ratio: float = 0.5) -> tuple[Polygon, Polygon]:
    """
    Split a polygon vertically at a given ratio (0-1) of its bounding box width.
    Returns (left, right) halves.
    """
    polygon = safe_polygon(polygon)
    minx, miny, maxx, maxy = polygon.bounds
    split_x = minx + (maxx - minx) * ratio

    blade = LineString([(split_x, miny - 1), (split_x, maxy + 1)])
    parts = split(polygon, blade)

    if len(parts.geoms) < 2:
        return polygon, Polygon()

    sorted_parts = sorted(parts.geoms, key=lambda g: g.centroid.x)
    left = sorted_parts[0] if isinstance(sorted_parts[0], Polygon) else Polygon()
    right = sorted_parts[-1] if isinstance(sorted_parts[-1], Polygon) else Polygon()
    return left, right


def merge_rooms(a: Polygon, b: Polygon) -> Polygon:
    """Merge two adjacent rooms into one by taking their union."""
    return boolean_union([a, b])


def compute_buildable_envelope(
    lot_width_m: float,
    lot_depth_m: float,
    front_setback_m: float = 4.5,
    rear_setback_m: float = 7.5,
    left_setback_m: float = 1.5,
    right_setback_m: float = 1.5,
) -> Polygon:
    """
    Compute the buildable area within a rectangular lot after applying setbacks.
    Lot origin is at (0, 0), front faces south (y=0).
    """
    return box(
        left_setback_m,
        front_setback_m,
        lot_width_m - right_setback_m,
        lot_depth_m - rear_setback_m,
    )


def polygon_to_coords(polygon: Polygon) -> list[tuple[float, float]]:
    """Extract coordinate list from a Shapely polygon."""
    return list(safe_polygon(polygon).exterior.coords)


def coords_to_polygon(coords: list[tuple[float, float]]) -> Polygon:
    """Create a Shapely polygon from a coordinate list."""
    return safe_polygon(Polygon(coords))


def compute_area_deviation(
    actual_areas: list[float], target_areas: list[float]
) -> float:
    """
    Compute mean percentage deviation between actual and target room areas.
    Returns value 0-100 (percent).
    """
    if not target_areas or not actual_areas:
        return 0.0
    deviations = []
    for actual, target in zip(actual_areas, target_areas):
        if target > 0:
            deviations.append(abs(actual - target) / target * 100)
    return float(np.mean(deviations)) if deviations else 0.0
