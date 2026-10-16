"""
Geometry cleanup pipeline: raw AI output → clean, snapped, valid room polygons.

This is the most critical custom code in the project. It takes the noisy
vector output from GSDiff/HouseDiffusion and produces production-quality
floor plan geometry.

All coordinates are in meters. Polygons are CCW-ordered, closed (first == last).
"""

import math

import numpy as np
from shapely.geometry import Polygon, MultiPolygon, box, LineString
from shapely.ops import unary_union, snap
from shapely.validation import make_valid


def _to_polygon(geom) -> Polygon:
    """Coerce any Shapely geometry to a single Polygon (largest if multi/collection)."""
    from shapely.geometry import GeometryCollection
    if isinstance(geom, Polygon) and geom.is_valid and not geom.is_empty:
        return geom
    if isinstance(geom, MultiPolygon):
        return max(geom.geoms, key=lambda g: g.area)
    if isinstance(geom, GeometryCollection):
        polys = [g for g in geom.geoms if isinstance(g, Polygon) and g.area > 0]
        if polys:
            return max(polys, key=lambda g: g.area)
    # Last resort: buffer(0) often fixes invalid geometry
    fixed = make_valid(geom) if not geom.is_valid else geom
    if isinstance(fixed, Polygon):
        return fixed
    if hasattr(fixed, 'geoms'):
        polys = [g for g in fixed.geoms if isinstance(g, Polygon) and g.area > 0]
        if polys:
            return max(polys, key=lambda g: g.area)
    return Polygon()


def snap_to_grid(polygon: Polygon, grid_size_m: float = 0.05) -> Polygon:
    """Snap all polygon vertices to a regular grid (default 5cm)."""
    polygon = _to_polygon(polygon)
    if polygon.is_empty:
        return polygon
    coords = list(polygon.exterior.coords)
    snapped = [
        (round(x / grid_size_m) * grid_size_m, round(y / grid_size_m) * grid_size_m)
        for x, y in coords
    ]
    result = Polygon(snapped)
    if not result.is_valid:
        result = make_valid(result)
        result = _to_polygon(result)
    return result


def align_walls(polygon: Polygon, angle_threshold_deg: float = 5.0) -> Polygon:
    """
    Force near-axis-aligned wall segments to be perfectly horizontal/vertical.
    Segments within `angle_threshold_deg` of 0°, 90°, 180°, 270° get snapped.
    """
    if polygon.is_empty:
        return polygon
    coords = list(polygon.exterior.coords)
    if not coords:
        return polygon
    aligned = [coords[0]]
    threshold_rad = math.radians(angle_threshold_deg)

    for i in range(1, len(coords)):
        px, py = aligned[-1]
        cx, cy = coords[i]
        dx, dy = cx - px, cy - py
        angle = math.atan2(dy, dx)

        # Snap to nearest axis
        for target in [0, math.pi / 2, math.pi, -math.pi / 2, -math.pi]:
            if abs(angle - target) < threshold_rad:
                length = math.hypot(dx, dy)
                cx = px + length * math.cos(target)
                cy = py + length * math.sin(target)
                break

        aligned.append((cx, cy))

    result = Polygon(aligned)
    if not result.is_valid:
        result = make_valid(result)
    return result


def ensure_minimum_area(polygon: Polygon, min_area_sqm: float) -> Polygon:
    """If polygon is below minimum area, scale it up uniformly from centroid."""
    if polygon.is_empty or polygon.area >= min_area_sqm:
        return polygon
    scale_factor = math.sqrt(min_area_sqm / polygon.area)
    cx, cy = polygon.centroid.x, polygon.centroid.y
    coords = [
        (cx + (x - cx) * scale_factor, cy + (y - cy) * scale_factor)
        for x, y in polygon.exterior.coords
    ]
    return Polygon(coords)


def remove_overlaps(room_polygons: list[Polygon]) -> list[Polygon]:
    """
    Resolve overlapping rooms by shrinking the overlapping region
    and assigning it to the larger room.
    """
    result = list(room_polygons)
    for i in range(len(result)):
        for j in range(i + 1, len(result)):
            if result[i].intersects(result[j]):
                overlap = result[i].intersection(result[j])
                if overlap.area > 0.01:  # > 1cm² overlap
                    # Assign overlap to larger room, remove from smaller
                    if result[i].area >= result[j].area:
                        result[j] = result[j].difference(overlap)
                    else:
                        result[i] = result[i].difference(overlap)
    # Ensure all results are valid polygons (not MultiPolygon)
    cleaned = []
    for p in result:
        if isinstance(p, MultiPolygon):
            # Keep the largest fragment
            cleaned.append(max(p.geoms, key=lambda g: g.area))
        elif isinstance(p, Polygon) and p.is_valid and p.area > 0:
            cleaned.append(p)
        else:
            cleaned.append(make_valid(p))
    return cleaned


def check_containment(
    room_polygons: list[Polygon], boundary: Polygon, tolerance_m: float = 0.1
) -> list[bool]:
    """Check which rooms fit within the buildable boundary."""
    buffered = boundary.buffer(tolerance_m)
    return [buffered.contains(room) for room in room_polygons]


def close_gaps(room_polygons: list[Polygon], max_gap_m: float = 0.3) -> list[Polygon]:
    """
    Close small gaps between adjacent rooms by extending walls.
    Gaps smaller than max_gap_m are filled.
    """
    result = list(room_polygons)
    for i in range(len(result)):
        for j in range(i + 1, len(result)):
            dist = result[i].distance(result[j])
            if 0 < dist <= max_gap_m:
                # Expand both toward each other by half the gap
                result[i] = result[i].buffer(dist / 2 + 0.01)
                result[j] = result[j].buffer(dist / 2 + 0.01)
    # Re-snap after expansion
    result = [snap_to_grid(p) for p in result]
    # Re-resolve any new overlaps
    result = remove_overlaps(result)
    return result


def extract_wall_segments(room_polygons: list[Polygon]) -> list[dict]:
    """
    Extract wall segments from room polygons.
    Shared edges between rooms become interior walls.
    Non-shared edges are exterior walls.
    """
    walls = []
    wall_id = 0
    all_edges = []

    # Collect all edges from all rooms
    for room_idx, poly in enumerate(room_polygons):
        if poly.is_empty:
            continue
        coords = list(poly.exterior.coords)
        for i in range(len(coords) - 1):
            edge = (coords[i], coords[i + 1])
            all_edges.append((room_idx, edge))

    # Determine shared vs exterior
    seen_edges = {}
    for room_idx, (start, end) in all_edges:
        # Normalize edge direction for comparison
        key = tuple(sorted([start, end], key=lambda p: (round(p[0], 3), round(p[1], 3))))
        if key in seen_edges:
            seen_edges[key]["is_exterior"] = False
            seen_edges[key]["rooms"].append(room_idx)
        else:
            seen_edges[key] = {
                "start": start,
                "end": end,
                "is_exterior": True,
                "rooms": [room_idx],
            }

    for key, data in seen_edges.items():
        walls.append({
            "id": f"wall_{wall_id}",
            "start": {"x": data["start"][0], "y": data["start"][1]},
            "end": {"x": data["end"][0], "y": data["end"][1]},
            "is_exterior": data["is_exterior"],
            "rooms": data["rooms"],
        })
        wall_id += 1

    return walls


def cleanup_pipeline(
    raw_polygons: list[Polygon],
    boundary: Polygon,
    min_areas: list[float] | None = None,
    grid_size_m: float = 0.05,
) -> dict:
    """
    Full cleanup pipeline: raw AI output → production geometry.

    Args:
        raw_polygons: Room polygons from GSDiff/HouseDiffusion
        boundary: Buildable envelope polygon
        min_areas: Minimum area per room (optional)
        grid_size_m: Grid snap resolution

    Returns:
        {
            "rooms": list[Polygon],      # Clean room polygons
            "walls": list[dict],          # Wall segments
            "boundary": Polygon,          # Validated boundary
            "is_valid": bool,
            "issues": list[str],
        }
    """
    issues = []

    # Step 1: Snap to grid
    polygons = [snap_to_grid(p, grid_size_m) for p in raw_polygons]

    # Step 2: Align near-axis walls
    polygons = [align_walls(p) for p in polygons]

    # Step 3: Enforce minimum areas
    if min_areas:
        for i, min_a in enumerate(min_areas):
            if i < len(polygons) and min_a:
                polygons[i] = ensure_minimum_area(polygons[i], min_a)

    # Step 4: Remove overlaps
    polygons = remove_overlaps(polygons)

    # Step 5: Close gaps
    polygons = close_gaps(polygons)

    # Step 6: Re-snap after all adjustments
    polygons = [snap_to_grid(p, grid_size_m) for p in polygons]

    # Step 7: Check containment
    contained = check_containment(polygons, boundary)
    for i, fits in enumerate(contained):
        if not fits:
            issues.append(f"Room {i} extends beyond buildable boundary")

    # Step 8: Extract walls
    walls = extract_wall_segments(polygons)

    return {
        "rooms": polygons,
        "walls": walls,
        "boundary": boundary,
        "is_valid": len(issues) == 0,
        "issues": issues,
    }
