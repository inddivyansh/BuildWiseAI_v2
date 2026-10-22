"""
Constraint-based floor plan layout solver.

Generates room polygons using a treemap-style recursive partitioning algorithm
with adjacency-aware placement. No GPU required — runs on pure CPU.

Algorithm:
1. Compute buildable envelope from plot constraints
2. Sort rooms by adjacency priority (most-connected first)
3. Recursively partition the buildable rectangle:
   - Split direction alternates H/V or follows aspect ratio
   - Split ratio proportional to room area targets
4. Assign rooms to partitions respecting adjacency preferences
5. Fine-tune positions with a simple force-directed pass
"""

import math
import random
from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import Polygon, box
from shapely.validation import make_valid

from app.schemas.floor_plan import FloorPlanSpec, Room, Relationship
from app.services.generation.interface import FloorPlanGenerator

import structlog

logger = structlog.get_logger()


@dataclass
class RoomSlot:
    """A room assigned to a rectangular region."""
    room_idx: int
    room: Room
    x: float
    y: float
    w: float
    h: float

    @property
    def area(self) -> float:
        return self.w * self.h

    @property
    def polygon(self) -> Polygon:
        return box(self.x, self.y, self.x + self.w, self.y + self.h)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.w / 2, self.y + self.h / 2)


class ConstraintSolver(FloorPlanGenerator):
    """CPU-based floor plan generator using recursive partitioning."""

    @property
    def name(self) -> str:
        return "constraint_solver_v1"

    async def generate(self, spec: FloorPlanSpec) -> list[Polygon]:
        return solve_layout(spec)


def solve_layout(spec: FloorPlanSpec) -> list[Polygon]:
    """
    Main entry point: FloorPlanSpec → list of room Polygons.
    Returns polygons in the same order as spec.rooms.
    """
    plot = spec.plot
    bx = plot.setbacks.left_m
    by = plot.setbacks.front_m
    bw = plot.buildable_width_m
    bh = plot.buildable_depth_m

    if bw <= 0 or bh <= 0:
        raise ValueError(f"No buildable area: {bw}m x {bh}m after setbacks")

    # Filter to rooms on floor 0 (single-floor for now)
    floor_rooms = [(i, r) for i, r in enumerate(spec.rooms) if r.floor == 0]
    if not floor_rooms:
        floor_rooms = [(i, r) for i, r in enumerate(spec.rooms)]

    # Build adjacency lookup
    adj_map = _build_adjacency_map(spec)

    # Sort rooms: most connected first, then by area (largest first)
    priority = _compute_room_priority(floor_rooms, adj_map)
    sorted_rooms = sorted(floor_rooms, key=lambda ir: -priority[ir[0]])

    # Compute area targets scaled to fit buildable area
    total_target = sum(r.target_area_sqm for _, r in sorted_rooms)
    max_footprint = bw * bh * plot.max_lot_coverage
    scale = min(1.0, max_footprint / total_target) if total_target > 0 else 1.0
    areas = [r.target_area_sqm * scale for _, r in sorted_rooms]

    # Recursive partition
    slots = _partition_rectangle(
        sorted_rooms, areas, bx, by, bw, bh, horizontal=bw >= bh
    )

    # Adjacency-aware swap optimization
    slots = _optimize_adjacency(slots, adj_map, iterations=200)

    # Build final polygon list in original room order
    result = [Polygon()] * len(spec.rooms)
    for slot in slots:
        poly = slot.polygon
        if not poly.is_valid:
            poly = make_valid(poly)
        if isinstance(poly, Polygon):
            result[slot.room_idx] = poly
        else:
            result[slot.room_idx] = box(slot.x, slot.y, slot.x + slot.w, slot.y + slot.h)

    return result


def _build_adjacency_map(spec: FloorPlanSpec) -> dict[str, list[tuple[str, str]]]:
    """Build room_id → [(neighbor_id, relationship)] map."""
    adj: dict[str, list[tuple[str, str]]] = {}
    for req in spec.adjacency:
        adj.setdefault(req.room_a_id, []).append((req.room_b_id, req.relationship.value))
        adj.setdefault(req.room_b_id, []).append((req.room_a_id, req.relationship.value))
    return adj


def _compute_room_priority(
    rooms: list[tuple[int, Room]],
    adj_map: dict[str, list[tuple[str, str]]],
) -> dict[int, float]:
    """Prioritize rooms by connectivity + area (hub rooms placed first)."""
    priority = {}
    for idx, room in rooms:
        connections = len(adj_map.get(room.id, []))
        priority[idx] = connections * 100 + room.target_area_sqm
    return priority


def _partition_rectangle(
    rooms: list[tuple[int, Room]],
    areas: list[float],
    x: float,
    y: float,
    w: float,
    h: float,
    horizontal: bool,
) -> list[RoomSlot]:
    """
    Recursively partition a rectangle into room slots.

    Uses a squarified treemap approach: split the rectangle proportionally
    to room areas, alternating split direction.
    """
    if len(rooms) == 0:
        return []

    if len(rooms) == 1:
        idx, room = rooms[0]
        return [RoomSlot(room_idx=idx, room=room, x=x, y=y, w=w, h=h)]

    # Split into two groups
    total_area = sum(areas)
    if total_area <= 0:
        total_area = 1.0

    # Find split point that best divides area in half
    half = total_area / 2
    cumulative = 0.0
    split_at = 1
    for i, a in enumerate(areas):
        cumulative += a
        if cumulative >= half:
            split_at = i + 1
            break
    split_at = max(1, min(split_at, len(rooms) - 1))

    group_a = rooms[:split_at]
    group_b = rooms[split_at:]
    areas_a = areas[:split_at]
    areas_b = areas[split_at:]

    ratio_a = sum(areas_a) / total_area if total_area > 0 else 0.5
    ratio_a = max(0.2, min(0.8, ratio_a))  # Clamp to avoid degenerate slices

    if horizontal:
        # Split vertically (left-right)
        w_a = w * ratio_a
        w_b = w - w_a
        slots_a = _partition_rectangle(group_a, areas_a, x, y, w_a, h, not horizontal)
        slots_b = _partition_rectangle(group_b, areas_b, x + w_a, y, w_b, h, not horizontal)
    else:
        # Split horizontally (bottom-top)
        h_a = h * ratio_a
        h_b = h - h_a
        slots_a = _partition_rectangle(group_a, areas_a, x, y, w, h_a, not horizontal)
        slots_b = _partition_rectangle(group_b, areas_b, x, y + h_a, w, h_b, not horizontal)

    return slots_a + slots_b


def _optimize_adjacency(
    slots: list[RoomSlot],
    adj_map: dict[str, list[tuple[str, str]]],
    iterations: int = 200,
) -> list[RoomSlot]:
    """
    Optimize room placement by swapping rooms to improve adjacency satisfaction.
    Only allows swapping slots of similar target area (within 35%) so that
    functional proportions (e.g. Living room vs Bathroom) are never corrupted.
    Uses simulated annealing-style random swaps.
    """
    if len(slots) <= 2:
        return slots

    best_slots = list(slots)
    best_score = _adjacency_score(best_slots, adj_map)

    rng = random.Random(42)

    for i in range(iterations):
        # Pick two random slots
        a, b = rng.sample(range(len(best_slots)), 2)

        # Spatial sanity guard: Never swap slots between rooms with disproportionate target areas!
        # (e.g. Swapping a 20m² living room with a 5m² bathroom causes severe distortion)
        area_a = best_slots[a].room.target_area_sqm
        area_b = best_slots[b].room.target_area_sqm
        if max(area_a, area_b) / max(min(area_a, area_b), 0.1) > 1.35:
            continue

        trial = list(best_slots)

        # Swap positions between compatible rooms
        trial[a] = RoomSlot(
            room_idx=best_slots[a].room_idx,
            room=best_slots[a].room,
            x=best_slots[b].x, y=best_slots[b].y,
            w=best_slots[b].w, h=best_slots[b].h,
        )
        trial[b] = RoomSlot(
            room_idx=best_slots[b].room_idx,
            room=best_slots[b].room,
            x=best_slots[a].x, y=best_slots[a].y,
            w=best_slots[a].w, h=best_slots[a].h,
        )

        score = _adjacency_score(trial, adj_map)
        # Accept improvement, or accept worse with decreasing probability
        temp = 1.0 - i / iterations
        if score > best_score or (temp > 0.1 and rng.random() < temp * 0.3):
            best_slots = trial
            best_score = score

    return best_slots


def _adjacency_score(
    slots: list[RoomSlot],
    adj_map: dict[str, list[tuple[str, str]]],
) -> float:
    """
    Score how well slots satisfy adjacency constraints while maintaining area fidelity.
    Higher = better. +10 for each satisfied adjacent, -5 for violated separated,
    heavy penalty for deviating from target room area.
    """
    id_to_slot = {s.room.id: s for s in slots}
    score = 0.0

    for slot in slots:
        # Area fidelity: penalize slots that stray from target room area
        target = slot.room.target_area_sqm
        if target > 0:
            deviation = abs(slot.area - target) / target
            if deviation > 0.15:
                score -= deviation * 25.0

        for neighbor_id, rel in adj_map.get(slot.room.id, []):
            if neighbor_id not in id_to_slot:
                continue
            neighbor = id_to_slot[neighbor_id]

            # Check if polygons touch (distance ~0)
            dist = slot.polygon.distance(neighbor.polygon)

            if rel in ("adjacent", "connected"):
                if dist < 0.1:
                    score += 10  # Touching — great
                elif dist < 1.0:
                    score += 3   # Close
                else:
                    score -= 2   # Too far
            elif rel == "nearby":
                if dist < 2.0:
                    score += 5
            elif rel == "separated":
                if dist >= 3.0:
                    score += 5
                elif dist < 0.5:
                    score -= 5  # Too close

    return score
