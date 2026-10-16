"""
Convert GSDiff output tensors → Shapely-compatible polygon coordinates in meters.

GSDiff outputs corners in a 512×512 pixel space with semantic labels and
edge predictions. This module:
  1. Merges nearby corners (threshold = resolution × 0.01 ≈ 5.12 pixels)
  2. Aligns corners to grid bands (x-align then y-align)
  3. Builds edge list from binary adjacency matrix
  4. Extracts planar cycles → polygon coords + semantic labels
  5. Converts pixel (512×512) → meters using buildable dimensions
  6. Applies setback offset

Reference: gsdiff/utils.py — get_cycle_basis_and_semantic_3_semansimplified,
edges_to_coordinates, get_near_corners, merge_array_elements
"""

from __future__ import annotations

import numpy as np
from collections import defaultdict

from .gsdiff_preprocessing import (
    RESOLUTION,
    NUM_SEMANTIC_CLASSES,
    MAX_CORNERS,
)


# ---------------------------------------------------------------------------
# Corner merging and alignment
# ---------------------------------------------------------------------------

MERGE_THRESHOLD = RESOLUTION * 0.01  # ~5.12 pixels
ALIGN_THRESHOLD = RESOLUTION * 0.015  # ~7.68 pixels for grid alignment


def merge_nearby_corners(
    corners: np.ndarray,
    threshold: float = MERGE_THRESHOLD,
) -> tuple[np.ndarray, dict[int, int]]:
    """
    Merge corners that are within `threshold` distance of each other.

    Returns:
        merged_corners: deduplicated corner array
        mapping: old_index → new_index mapping
    """
    n = len(corners)
    if n == 0:
        return corners, {}

    # Build merge groups using simple union-find
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(n):
        for j in range(i + 1, n):
            dist = np.linalg.norm(corners[i] - corners[j])
            if dist < threshold:
                union(i, j)

    # Group corners by their root
    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        groups[find(i)].append(i)

    # Compute merged corners (centroid of each group)
    merged = []
    mapping = {}
    for new_idx, (_, group) in enumerate(sorted(groups.items())):
        centroid = corners[group].mean(axis=0)
        merged.append(centroid)
        for old_idx in group:
            mapping[old_idx] = new_idx

    return np.array(merged), mapping


def align_corners_to_grid(
    corners: np.ndarray,
    threshold: float = ALIGN_THRESHOLD,
) -> np.ndarray:
    """
    Snap corners to shared x and y coordinate bands.

    Groups corners with similar x (or y) coordinates and aligns them
    to the group median. This produces cleaner rectangular rooms.
    """
    if len(corners) == 0:
        return corners

    result = corners.copy()

    # X-align
    x_sorted = np.argsort(result[:, 0])
    bands_x: list[list[int]] = []
    current_band: list[int] = [x_sorted[0]]

    for i in range(1, len(x_sorted)):
        if result[x_sorted[i], 0] - result[x_sorted[i - 1], 0] < threshold:
            current_band.append(x_sorted[i])
        else:
            bands_x.append(current_band)
            current_band = [x_sorted[i]]
    bands_x.append(current_band)

    for band in bands_x:
        median_x = np.median(result[band, 0])
        result[band, 0] = median_x

    # Y-align
    y_sorted = np.argsort(result[:, 1])
    bands_y: list[list[int]] = []
    current_band = [y_sorted[0]]

    for i in range(1, len(y_sorted)):
        if result[y_sorted[i], 1] - result[y_sorted[i - 1], 1] < threshold:
            current_band.append(y_sorted[i])
        else:
            bands_y.append(current_band)
            current_band = [y_sorted[i]]
    bands_y.append(current_band)

    for band in bands_y:
        median_y = np.median(result[band, 1])
        result[band, 1] = median_y

    return result


# ---------------------------------------------------------------------------
# Edge extraction and cycle detection
# ---------------------------------------------------------------------------

def edges_from_adjacency(
    edge_matrix: np.ndarray,
    num_corners: int,
) -> list[tuple[int, int]]:
    """
    Extract edge list from the binary adjacency matrix output by Stage 2.

    Args:
        edge_matrix: (MAX_CORNERS, MAX_CORNERS) binary adjacency (0/1)
        num_corners: number of real corners

    Returns:
        List of (i, j) edge tuples where i < j.
    """
    edges = set()
    for i in range(num_corners):
        for j in range(i + 1, num_corners):
            if edge_matrix[i, j] > 0.5:
                edges.add((i, j))
    return sorted(edges)


def extract_planar_cycles(
    corners: np.ndarray,
    edges: list[tuple[int, int]],
) -> list[list[int]]:
    """
    Extract planar face cycles from the corner graph.

    Uses a simplified planar face traversal: for each directed edge,
    turn left (counter-clockwise) at each vertex to trace room boundaries.

    Returns:
        List of cycles, each a list of corner indices forming a polygon.
    """
    if not edges or len(corners) == 0:
        return []

    # Build adjacency list
    adj: dict[int, list[int]] = defaultdict(list)
    for i, j in edges:
        adj[i].append(j)
        adj[j].append(i)

    # Sort neighbors by angle from each vertex
    for v in adj:
        neighbors = adj[v]
        angles = []
        for n in neighbors:
            dx = corners[n, 0] - corners[v, 0]
            dy = corners[n, 1] - corners[v, 1]
            angles.append(np.arctan2(dy, dx))
        sorted_idx = np.argsort(angles)
        adj[v] = [neighbors[i] for i in sorted_idx]

    # Trace all directed edge cycles using "next edge after turning left"
    used_edges: set[tuple[int, int]] = set()
    cycles: list[list[int]] = []

    for i, j in edges:
        for start, end in [(i, j), (j, i)]:
            if (start, end) in used_edges:
                continue

            cycle = [start]
            prev, curr = start, end
            max_steps = len(corners) + 2

            for _ in range(max_steps):
                cycle.append(curr)
                used_edges.add((prev, curr))

                if curr == start:
                    break

                # Find next: the neighbor of curr after prev in sorted order
                neighbors = adj[curr]
                if not neighbors:
                    break

                try:
                    idx = neighbors.index(prev)
                except ValueError:
                    break

                # Turn left: take the neighbor just before prev in angular order
                next_idx = (idx - 1) % len(neighbors)
                next_v = neighbors[next_idx]

                prev = curr
                curr = next_v
            else:
                continue  # Didn't close, skip

            if len(cycle) >= 4 and cycle[-1] == cycle[0]:
                # Valid closed cycle
                cycles.append(cycle[:-1])  # Remove closing duplicate

    # Deduplicate cycles (same vertices in different rotations)
    unique_cycles = []
    seen: set[frozenset[int]] = set()
    for cycle in cycles:
        key = frozenset(cycle)
        if key not in seen and len(cycle) >= 3:
            seen.add(key)
            unique_cycles.append(cycle)

    return unique_cycles


def assign_semantics_to_cycles(
    cycles: list[list[int]],
    corners: np.ndarray,
    corner_semantics: np.ndarray,
    num_rooms: int,
) -> list[int]:
    """
    Assign a semantic label to each cycle (room polygon).

    Uses majority vote of the semantic labels of corners in each cycle.

    Args:
        cycles: list of corner index lists
        corners: (N, 2) corner coordinates
        corner_semantics: (N, 7) semantic features per corner
        num_rooms: expected number of rooms

    Returns:
        List of semantic category indices, one per cycle.
    """
    labels = []
    for cycle in cycles:
        if not cycle:
            labels.append(6)  # other
            continue

        # Majority vote over corners in this cycle
        sem_sum = np.zeros(NUM_SEMANTIC_CLASSES)
        for idx in cycle:
            if idx < len(corner_semantics):
                sem_sum += corner_semantics[idx]

        labels.append(int(np.argmax(sem_sum)))

    return labels


# ---------------------------------------------------------------------------
# Main postprocessing pipeline
# ---------------------------------------------------------------------------

def gsdiff_output_to_polygons(
    corners: np.ndarray,
    semantics: np.ndarray,
    edge_matrix: np.ndarray,
    num_rooms: int,
    num_corners: int,
    buildable_w: float,
    buildable_d: float,
    offset_x: float = 0.0,
    offset_y: float = 0.0,
) -> dict:
    """
    Full GSDiff postprocessing: corners + edges → room polygons in meters.

    Args:
        corners: (MAX_CORNERS, 2) corner coordinates in [0, RESOLUTION] pixel space
        semantics: (MAX_CORNERS, 7) per-corner semantic features
        edge_matrix: (MAX_CORNERS, MAX_CORNERS) binary adjacency from Stage 2
        num_rooms: expected number of rooms
        num_corners: number of real (non-padding) corners
        buildable_w: buildable width in meters
        buildable_d: buildable depth in meters
        offset_x: X offset (left setback) in meters
        offset_y: Y offset (front setback) in meters

    Returns:
        dict with:
            - polygons: list of coordinate lists [[(x,y), ...], ...]
            - semantics: list of semantic category indices per polygon
            - num_polygons: int
    """
    # Clip to real corners only
    corners = corners[:num_corners].copy()
    semantics = semantics[:num_corners].copy()

    if len(corners) < 3:
        # Not enough corners for any polygon — return empty
        return {"polygons": [], "semantics": [], "num_polygons": 0}

    # Step 1: Merge nearby corners
    corners, merge_map = merge_nearby_corners(corners)

    # Update semantics for merged corners
    new_semantics = np.zeros((len(corners), NUM_SEMANTIC_CLASSES), dtype=np.float32)
    for old_idx, new_idx in merge_map.items():
        if old_idx < len(semantics):
            new_semantics[new_idx] += semantics[old_idx]
    semantics = new_semantics

    # Remap edge matrix through merge mapping
    new_n = len(corners)
    new_edge_matrix = np.zeros((new_n, new_n), dtype=np.float32)
    for i in range(num_corners):
        for j in range(i + 1, num_corners):
            if edge_matrix[i, j] > 0.5:
                ni = merge_map.get(i, i)
                nj = merge_map.get(j, j)
                if ni != nj and ni < new_n and nj < new_n:
                    new_edge_matrix[ni, nj] = 1.0
                    new_edge_matrix[nj, ni] = 1.0

    # Step 2: Align to grid
    corners = align_corners_to_grid(corners)

    # Step 3: Extract edges
    edges = edges_from_adjacency(new_edge_matrix, len(corners))

    # Step 4: Extract planar cycles
    cycles = extract_planar_cycles(corners, edges)

    # Filter out the outer boundary cycle (largest area)
    if len(cycles) > 1:
        areas = []
        for cycle in cycles:
            pts = corners[cycle]
            # Shoelace area
            n = len(pts)
            area = 0.0
            for k in range(n):
                k1 = (k + 1) % n
                area += pts[k, 0] * pts[k1, 1]
                area -= pts[k1, 0] * pts[k, 1]
            areas.append(abs(area) / 2.0)

        max_area_idx = np.argmax(areas)
        # Remove the outer boundary (biggest cycle) if it's significantly larger
        if areas[max_area_idx] > sum(areas) * 0.5:
            cycles = [c for i, c in enumerate(cycles) if i != max_area_idx]

    # Step 5: Assign semantics
    cycle_labels = assign_semantics_to_cycles(cycles, corners, semantics, num_rooms)

    # Step 6: Convert to meter coordinates
    scale_x = buildable_w / RESOLUTION
    scale_y = buildable_d / RESOLUTION

    polygons = []
    for cycle in cycles:
        pts_pixel = corners[cycle]
        pts_meters = []
        for px, py in pts_pixel:
            mx = float(px) * scale_x + offset_x
            my = float(py) * scale_y + offset_y
            pts_meters.append((round(mx, 4), round(my, 4)))
        # Close the polygon
        pts_meters.append(pts_meters[0])
        polygons.append(pts_meters)

    return {
        "polygons": polygons,
        "semantics": cycle_labels,
        "num_polygons": len(polygons),
    }


def map_polygons_to_spec_rooms(
    polygons: list[list[tuple[float, float]]],
    polygon_semantics: list[int],
    spec_dict: dict,
    room_indices: list[int] | None = None,
) -> list[list[tuple[float, float]]]:
    """
    Match GSDiff output polygons to spec.rooms by semantic type + area proximity.

    GSDiff may produce a different number of polygons than rooms requested.
    This function performs a best-effort matching:
      - For each spec room, find the best matching polygon by semantic compatibility + area
      - Unmatched rooms get rectangle fallbacks
      - Surplus polygons are discarded

    Args:
        polygons: list of polygon coordinate lists from GSDiff
        polygon_semantics: semantic category per polygon
        spec_dict: full FloorPlanSpec dict
        room_indices: if rooms were split, original indices of the rooms sent to GSDiff

    Returns:
        List of polygon coordinate lists, one per room in spec_dict order.
        Length matches len(spec_dict["rooms"]).
    """
    from .gsdiff_preprocessing import get_gsdiff_semantic

    rooms = spec_dict["rooms"]
    num_rooms = len(rooms)
    plot = spec_dict["plot"]

    buildable_w = plot.get("buildable_width_m") or 9.0
    buildable_d = plot.get("buildable_depth_m") or 8.0
    offset_x = plot.get("setbacks", {}).get("left_m", 1.5)
    offset_y = plot.get("setbacks", {}).get("front_m", 4.5)

    # If room_indices provided, we only have polygons for a subset of rooms
    if room_indices is not None:
        target_rooms = [rooms[i] for i in room_indices]
    else:
        target_rooms = rooms

    # Compute polygon areas
    poly_areas = []
    for poly in polygons:
        coords = poly[:-1] if poly and poly[0] == poly[-1] else poly
        n = len(coords)
        area = 0.0
        for k in range(n):
            k1 = (k + 1) % n
            area += coords[k][0] * coords[k1][1]
            area -= coords[k1][0] * coords[k][1]
        poly_areas.append(abs(area) / 2.0)

    # Match rooms to polygons using greedy assignment
    used_polys: set[int] = set()
    result: list[list[tuple[float, float]] | None] = [None] * num_rooms

    # Build indices for matching
    match_indices = room_indices if room_indices is not None else list(range(num_rooms))

    for room_list_idx, room_idx in enumerate(match_indices):
        room = rooms[room_idx]
        room_semantic = get_gsdiff_semantic(room["type"])
        target_area = room["target_area_sqm"]

        best_poly = None
        best_score = float("inf")

        for p_idx in range(len(polygons)):
            if p_idx in used_polys:
                continue

            # Score: semantic mismatch penalty + area difference ratio
            sem_penalty = 0.0 if polygon_semantics[p_idx] == room_semantic else 10.0
            area_diff = abs(poly_areas[p_idx] - target_area) / max(target_area, 1.0)
            score = sem_penalty + area_diff

            if score < best_score:
                best_score = score
                best_poly = p_idx

        if best_poly is not None and best_score < 20.0:
            result[room_idx] = polygons[best_poly]
            used_polys.add(best_poly)

    # Fill unmatched rooms with rectangle fallbacks
    for i in range(num_rooms):
        if result[i] is None:
            target = rooms[i]["target_area_sqm"]
            aspect = 1.2
            h = (target / aspect) ** 0.5
            w = target / h if h > 0 else 3.0
            w = min(w, buildable_w * 0.8)
            h = min(h, buildable_d * 0.8)

            # Simple grid placement for fallbacks
            cols = max(1, int(buildable_w / w))
            row = i // cols
            col = i % cols
            x0 = offset_x + col * w
            y0 = offset_y + row * h
            x0 = min(x0, offset_x + buildable_w - w)
            y0 = min(y0, offset_y + buildable_d - h)

            result[i] = [
                (round(x0, 4), round(y0, 4)),
                (round(x0 + w, 4), round(y0, 4)),
                (round(x0 + w, 4), round(y0 + h, 4)),
                (round(x0, 4), round(y0 + h, 4)),
                (round(x0, 4), round(y0, 4)),
            ]

    return result
