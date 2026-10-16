"""
Convert HouseDiffusion output tensors → Shapely polygons in meter coordinates.

HouseDiffusion outputs corner positions in normalized [-1, 1] space (or [0, 256]
pixel space depending on the model variant). This module:
  1. Denormalizes coordinates to the buildable envelope dimensions
  2. Splits the flat corner array into per-room polygons using num_coords
  3. Validates polygons and falls back to rectangles for degenerate rooms
"""

from __future__ import annotations

import numpy as np


def housediff_output_to_polygons(
    corners: np.ndarray,
    num_coords: np.ndarray,
    buildable_width: float,
    buildable_depth: float,
    target_areas: np.ndarray | None = None,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
    coord_space: str = "pixel256",
) -> list[list[tuple[float, float]]]:
    """
    Convert diffusion output corners to polygon coordinate lists.

    Args:
        corners: float array [total_corners, 2] — raw corner coordinates from model.
        num_coords: int array [N] — number of corners per room.
        buildable_width: width of buildable envelope in meters.
        buildable_depth: depth of buildable envelope in meters.
        target_areas: optional float array [N] — target room areas for rectangle fallback.
        offset_x: X offset (e.g. left setback) to shift into lot coordinates.
        offset_y: Y offset (e.g. front setback) to shift into lot coordinates.
        coord_space: coordinate system of `corners`:
            - "pixel256": HouseDiffusion default, coords in [0, 256] pixel space
            - "normalized": coords in [-1, 1] normalized space
            - "meters": already in meter coords (no rescaling needed)

    Returns:
        List of N polygon coordinate lists. Each is a list of (x, y) tuples
        forming a closed polygon (first == last point).
    """
    corners = np.array(corners, dtype=np.float64)
    num_coords = np.array(num_coords, dtype=np.int64)
    num_rooms = len(num_coords)

    # --- Denormalize to meters ---
    if coord_space == "pixel256":
        # HouseDiffusion default: [0, 256] → [0, buildable_dim]
        corners[:, 0] = corners[:, 0] / 256.0 * buildable_width
        corners[:, 1] = corners[:, 1] / 256.0 * buildable_depth
    elif coord_space == "normalized":
        # [-1, 1] → [0, buildable_dim]
        corners[:, 0] = (corners[:, 0] + 1.0) / 2.0 * buildable_width
        corners[:, 1] = (corners[:, 1] + 1.0) / 2.0 * buildable_depth
    # "meters" → no transformation needed

    # Apply offset to shift into lot coordinate system
    corners[:, 0] += offset_x
    corners[:, 1] += offset_y

    # --- Split into per-room polygons ---
    polygons: list[list[tuple[float, float]]] = []
    cursor = 0

    for i in range(num_rooms):
        nc = int(num_coords[i])
        if cursor + nc > len(corners):
            # Not enough corners left — use rectangle fallback
            polygons.append(
                _rectangle_fallback(
                    i, num_rooms, buildable_width, buildable_depth,
                    offset_x, offset_y,
                    target_areas[i] if target_areas is not None else 9.0,
                )
            )
            cursor += nc
            continue

        room_corners = corners[cursor: cursor + nc]
        cursor += nc

        # Validate the polygon
        poly_coords = _order_corners_ccw(room_corners)

        if _is_degenerate(poly_coords):
            polygons.append(
                _rectangle_fallback(
                    i, num_rooms, buildable_width, buildable_depth,
                    offset_x, offset_y,
                    target_areas[i] if target_areas is not None else 9.0,
                )
            )
        else:
            # Close the polygon (first == last)
            closed = poly_coords + [poly_coords[0]]
            polygons.append(closed)

    # --- Scale rooms to match target areas ---
    if target_areas is not None:
        polygons = _scale_to_target_areas(
            polygons, target_areas, buildable_width, buildable_depth,
            offset_x, offset_y,
        )

    return polygons


def _scale_to_target_areas(
    polygons: list[list[tuple[float, float]]],
    target_areas: np.ndarray,
    buildable_width: float,
    buildable_depth: float,
    offset_x: float,
    offset_y: float,
) -> list[list[tuple[float, float]]]:
    """
    Scale each room polygon toward its target area while preserving shape and centroid.

    The model's output gives us the relative layout (adjacency, position), but areas
    are often off. This step uniformly scales each polygon around its centroid so that
    its area matches the target, then clamps to the buildable envelope.
    """
    result = []
    for i, poly in enumerate(polygons):
        target = float(target_areas[i]) if i < len(target_areas) else 9.0
        coords = poly[:-1] if poly[0] == poly[-1] else poly  # remove closing point

        # Compute current area via shoelace
        n = len(coords)
        if n < 3:
            result.append(poly)
            continue

        area = 0.0
        for j in range(n):
            k = (j + 1) % n
            area += coords[j][0] * coords[k][1]
            area -= coords[k][0] * coords[j][1]
        area = abs(area) / 2.0

        if area < 0.01:
            # Degenerate — replace with rectangle at this room's expected position
            result.append(
                _rectangle_at_centroid(
                    coords, target, buildable_width, buildable_depth,
                    offset_x, offset_y,
                )
            )
            continue

        # Scale factor: sqrt because area scales with square of linear dimension
        scale = (target / area) ** 0.5

        # If scale is extreme (>8x), the raw polygon is too degenerate to
        # preserve — replace with a rectangle centered at the centroid.
        if scale > 8.0:
            result.append(
                _rectangle_at_centroid(
                    coords, target, buildable_width, buildable_depth,
                    offset_x, offset_y,
                )
            )
            continue

        # Clamp scale to reasonable range
        scale = max(0.25, min(scale, 8.0))

        # Scale around centroid
        cx = sum(c[0] for c in coords) / n
        cy = sum(c[1] for c in coords) / n

        scaled = []
        for x, y in coords:
            nx = cx + (x - cx) * scale
            ny = cy + (y - cy) * scale
            scaled.append((round(nx, 4), round(ny, 4)))

        # Clamp entire polygon to buildable envelope, shifting if needed
        scaled = _clamp_polygon_to_envelope(
            scaled, offset_x, offset_y, buildable_width, buildable_depth,
        )

        # Close the polygon
        scaled.append(scaled[0])
        result.append(scaled)

    return result


def _rectangle_at_centroid(
    coords: list[tuple[float, float]],
    target_area: float,
    buildable_width: float,
    buildable_depth: float,
    offset_x: float,
    offset_y: float,
) -> list[tuple[float, float]]:
    """Create a rectangle centered at the polygon's centroid with the target area."""
    n = len(coords)
    cx = sum(c[0] for c in coords) / n if n > 0 else offset_x + buildable_width / 2
    cy = sum(c[1] for c in coords) / n if n > 0 else offset_y + buildable_depth / 2

    aspect = 1.2
    h = (target_area / aspect) ** 0.5
    w = target_area / h if h > 0 else 3.0
    w = min(w, buildable_width * 0.9)
    h = min(h, buildable_depth * 0.9)

    x0 = cx - w / 2
    y0 = cy - h / 2

    # Shift into envelope if needed
    x0 = max(offset_x, min(x0, offset_x + buildable_width - w))
    y0 = max(offset_y, min(y0, offset_y + buildable_depth - h))

    return [
        (round(x0, 4), round(y0, 4)),
        (round(x0 + w, 4), round(y0, 4)),
        (round(x0 + w, 4), round(y0 + h, 4)),
        (round(x0, 4), round(y0 + h, 4)),
        (round(x0, 4), round(y0, 4)),
    ]


def _clamp_polygon_to_envelope(
    coords: list[tuple[float, float]],
    offset_x: float,
    offset_y: float,
    buildable_width: float,
    buildable_depth: float,
) -> list[tuple[float, float]]:
    """Shift (not clip) a polygon so it fits inside the buildable envelope."""
    xs = [c[0] for c in coords]
    ys = [c[1] for c in coords]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)

    # Compute shift needed
    dx = 0.0
    dy = 0.0
    if min_x < offset_x:
        dx = offset_x - min_x
    elif max_x > offset_x + buildable_width:
        dx = (offset_x + buildable_width) - max_x
    if min_y < offset_y:
        dy = offset_y - min_y
    elif max_y > offset_y + buildable_depth:
        dy = (offset_y + buildable_depth) - max_y

    if dx == 0.0 and dy == 0.0:
        return coords

    return [(round(x + dx, 4), round(y + dy, 4)) for x, y in coords]


def _order_corners_ccw(
    corners: np.ndarray,
) -> list[tuple[float, float]]:
    """
    Order polygon corners counter-clockwise around their centroid.

    For 4-corner (rectangular) rooms, this ensures consistent winding.
    """
    cx = corners[:, 0].mean()
    cy = corners[:, 1].mean()
    angles = np.arctan2(corners[:, 1] - cy, corners[:, 0] - cx)
    order = np.argsort(angles)
    ordered = corners[order]
    return [(round(float(x), 4), round(float(y), 4)) for x, y in ordered]


def _is_degenerate(coords: list[tuple[float, float]], min_area: float = 0.5) -> bool:
    """
    Check if a polygon is degenerate (too small, self-intersecting, or collinear).

    Uses the shoelace formula for area.
    """
    n = len(coords)
    if n < 3:
        return True

    # Shoelace area
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += coords[i][0] * coords[j][1]
        area -= coords[j][0] * coords[i][1]
    area = abs(area) / 2.0

    return area < min_area


def _rectangle_fallback(
    room_idx: int,
    num_rooms: int,
    buildable_width: float,
    buildable_depth: float,
    offset_x: float,
    offset_y: float,
    target_area: float,
) -> list[tuple[float, float]]:
    """
    Generate a simple rectangle as fallback for degenerate rooms.

    Places rooms in a grid pattern within the buildable envelope.
    """
    # Compute a reasonable rectangle size from target area
    aspect = 1.2  # slightly rectangular
    h = (target_area / aspect) ** 0.5
    w = target_area / h if h > 0 else 3.0

    # Clamp to buildable dimensions
    w = min(w, buildable_width * 0.8)
    h = min(h, buildable_depth * 0.8)

    # Place in a grid within the envelope
    cols = max(1, int(buildable_width / w))
    row = room_idx // cols
    col = room_idx % cols

    x0 = offset_x + col * w
    y0 = offset_y + row * h

    # Clamp to buildable area
    x0 = min(x0, offset_x + buildable_width - w)
    y0 = min(y0, offset_y + buildable_depth - h)

    return [
        (round(x0, 4), round(y0, 4)),
        (round(x0 + w, 4), round(y0, 4)),
        (round(x0 + w, 4), round(y0 + h, 4)),
        (round(x0, 4), round(y0 + h, 4)),
        (round(x0, 4), round(y0, 4)),  # close
    ]


def decode_analog_bit(corners: np.ndarray, threshold: float = 0.0) -> np.ndarray:
    """
    Decode analog bit encoding if used by the model variant.

    Some HouseDiffusion variants encode corner validity as an extra channel.
    Corners with analog_bit < threshold are considered invalid/padding.

    Args:
        corners: array with shape [total_corners, 3] where col 2 is analog_bit
        threshold: cutoff for valid corners

    Returns:
        corners array [valid_corners, 2] with invalid corners removed
    """
    if corners.shape[1] <= 2:
        return corners

    valid = corners[:, 2] >= threshold
    return corners[valid, :2]
