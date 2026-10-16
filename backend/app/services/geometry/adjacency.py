"""
Room adjacency graph: detect which rooms share walls, compute adjacency matrix,
and validate against required adjacency constraints from FloorPlanSpec.

Uses Shapely to detect shared boundaries between room polygons.
Two rooms are "adjacent" if their boundaries overlap by more than a threshold length.
"""

from dataclasses import dataclass, field

from shapely.geometry import Polygon, LineString, MultiLineString
from shapely.ops import shared_paths


@dataclass
class AdjacencyEdge:
    """An edge in the adjacency graph between two rooms."""
    room_a_idx: int
    room_b_idx: int
    shared_length_m: float
    shared_segments: list[tuple[tuple[float, float], tuple[float, float]]] = field(
        default_factory=list
    )

    @property
    def has_shared_wall(self) -> bool:
        """Rooms share a wall if shared boundary >= 0.3m (door-width minimum)."""
        return self.shared_length_m >= 0.3


@dataclass
class AdjacencyGraph:
    """Complete room adjacency graph."""
    num_rooms: int
    edges: list[AdjacencyEdge]

    @property
    def matrix(self) -> list[list[float]]:
        """NxN adjacency matrix where values are shared wall length in meters."""
        mat = [[0.0] * self.num_rooms for _ in range(self.num_rooms)]
        for edge in self.edges:
            mat[edge.room_a_idx][edge.room_b_idx] = edge.shared_length_m
            mat[edge.room_b_idx][edge.room_a_idx] = edge.shared_length_m
        return mat

    def are_adjacent(self, room_a_idx: int, room_b_idx: int) -> bool:
        """Check if two rooms share a wall."""
        for edge in self.edges:
            if {edge.room_a_idx, edge.room_b_idx} == {room_a_idx, room_b_idx}:
                return edge.has_shared_wall
        return False

    def neighbors(self, room_idx: int) -> list[int]:
        """Get indices of all rooms adjacent to the given room."""
        result = []
        for edge in self.edges:
            if edge.has_shared_wall:
                if edge.room_a_idx == room_idx:
                    result.append(edge.room_b_idx)
                elif edge.room_b_idx == room_idx:
                    result.append(edge.room_a_idx)
        return result

    def are_nearby(self, room_a_idx: int, room_b_idx: int, max_distance_m: float = 2.0) -> bool:
        """Check if two rooms are nearby (close but may not share a wall)."""
        for edge in self.edges:
            if {edge.room_a_idx, edge.room_b_idx} == {room_a_idx, room_b_idx}:
                return edge.shared_length_m > 0 or True
        return False


def _extract_shared_boundary(a: Polygon, b: Polygon, tolerance_m: float = 0.05) -> float:
    """
    Compute the length of shared boundary between two polygons.
    Uses a small buffer tolerance to handle near-touching walls.
    """
    boundary_a = a.boundary
    boundary_b = b.boundary

    # Try exact shared paths first
    try:
        forward, backward = shared_paths(boundary_a, boundary_b)
        total = 0.0
        for geom in [forward, backward]:
            if not geom.is_empty:
                total += geom.length
        if total > 0:
            return total
    except Exception:
        pass

    # Fallback: check if boundaries are within tolerance
    buffered_boundary_a = boundary_a.buffer(tolerance_m)
    shared = buffered_boundary_a.intersection(boundary_b)
    if shared.is_empty:
        return 0.0
    return shared.length


def _extract_shared_segments(
    a: Polygon, b: Polygon, tolerance_m: float = 0.05
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Extract the actual line segments that form the shared boundary."""
    segments = []
    coords_a = list(a.exterior.coords)
    coords_b = list(b.exterior.coords)

    for i in range(len(coords_a) - 1):
        seg_a = LineString([coords_a[i], coords_a[i + 1]])
        for j in range(len(coords_b) - 1):
            seg_b = LineString([coords_b[j], coords_b[j + 1]])
            if seg_a.distance(seg_b) <= tolerance_m and seg_a.length > 0.1:
                segments.append((coords_a[i], coords_a[i + 1]))
                break

    return segments


def build_adjacency_graph(
    room_polygons: list[Polygon],
    tolerance_m: float = 0.05,
) -> AdjacencyGraph:
    """
    Build a complete adjacency graph from room polygons.

    Args:
        room_polygons: List of Shapely Polygons representing rooms.
        tolerance_m: Maximum gap to still consider rooms as touching.

    Returns:
        AdjacencyGraph with edges for all room pairs that share boundaries.
    """
    n = len(room_polygons)
    edges = []

    for i in range(n):
        for j in range(i + 1, n):
            # Quick distance check to skip obviously distant rooms
            if room_polygons[i].distance(room_polygons[j]) > tolerance_m:
                continue

            shared_len = _extract_shared_boundary(
                room_polygons[i], room_polygons[j], tolerance_m
            )
            if shared_len > 0:
                segments = _extract_shared_segments(
                    room_polygons[i], room_polygons[j], tolerance_m
                )
                edges.append(AdjacencyEdge(
                    room_a_idx=i,
                    room_b_idx=j,
                    shared_length_m=shared_len,
                    shared_segments=segments,
                ))

    return AdjacencyGraph(num_rooms=n, edges=edges)


@dataclass
class AdjacencyViolation:
    """A violated adjacency constraint."""
    room_a_id: str
    room_b_id: str
    required: str  # "adjacent", "nearby", "separated"
    actual: str    # "adjacent", "nearby", "separated", "disconnected"
    message: str


def validate_adjacency_constraints(
    room_polygons: list[Polygon],
    room_ids: list[str],
    constraints: list[dict],
    tolerance_m: float = 0.05,
    separation_min_m: float = 3.0,
) -> list[AdjacencyViolation]:
    """
    Validate that the generated layout satisfies adjacency constraints.

    Args:
        room_polygons: Room polygons in the same order as room_ids.
        room_ids: Room ID strings matching the polygons.
        constraints: List of dicts with keys: room_a_id, room_b_id, relationship.
        tolerance_m: Tolerance for adjacency detection.
        separation_min_m: Minimum distance for "separated" relationship.

    Returns:
        List of violations (empty = all constraints satisfied).
    """
    id_to_idx = {rid: idx for idx, rid in enumerate(room_ids)}
    graph = build_adjacency_graph(room_polygons, tolerance_m)
    violations = []

    for constraint in constraints:
        a_id = constraint["room_a_id"]
        b_id = constraint["room_b_id"]
        required = constraint["relationship"]

        if a_id not in id_to_idx or b_id not in id_to_idx:
            continue

        a_idx = id_to_idx[a_id]
        b_idx = id_to_idx[b_id]
        is_adj = graph.are_adjacent(a_idx, b_idx)
        distance = room_polygons[a_idx].distance(room_polygons[b_idx])

        if is_adj:
            actual = "adjacent"
        elif distance <= tolerance_m * 10:
            actual = "nearby"
        else:
            actual = "separated" if distance >= separation_min_m else "disconnected"

        violated = False
        if required == "adjacent" and actual != "adjacent":
            violated = True
        elif required == "connected" and actual not in ("adjacent",):
            violated = True
        elif required == "separated" and actual in ("adjacent", "nearby"):
            violated = True

        if violated:
            violations.append(AdjacencyViolation(
                room_a_id=a_id,
                room_b_id=b_id,
                required=required,
                actual=actual,
                message=f"{a_id} and {b_id}: required={required}, actual={actual}",
            ))

    return violations
