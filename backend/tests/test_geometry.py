"""Tests for the geometry cleanup pipeline — no ML dependencies needed."""

import pytest
from shapely.geometry import Polygon, box

from app.services.geometry.cleanup import (
    align_walls,
    check_containment,
    cleanup_pipeline,
    close_gaps,
    ensure_minimum_area,
    extract_wall_segments,
    remove_overlaps,
    snap_to_grid,
)
from app.services.geometry.operations import (
    boolean_difference,
    boolean_intersection,
    boolean_union,
    buffer_polygon,
    clip_to_boundary,
    compute_area_deviation,
    compute_buildable_envelope,
    merge_rooms,
    split_polygon_horizontal,
    split_polygon_vertical,
    wall_centerline_to_polygon,
)
from app.services.geometry.adjacency import (
    build_adjacency_graph,
    validate_adjacency_constraints,
)


# ============================================================
# Cleanup pipeline tests
# ============================================================

class TestSnapToGrid:
    def test_snaps_coordinates(self):
        poly = Polygon([(0.03, 0.07), (5.02, 0.07), (5.02, 3.98), (0.03, 3.98)])
        snapped = snap_to_grid(poly, grid_size_m=0.05)
        coords = list(snapped.exterior.coords)
        assert coords[0] == (0.05, 0.05)
        assert coords[1] == (5.0, 0.05)

    def test_preserves_valid_polygon(self):
        poly = Polygon([(0, 0), (5, 0), (5, 4), (0, 4)])
        snapped = snap_to_grid(poly, 0.05)
        assert snapped.is_valid
        assert abs(snapped.area - 20.0) < 0.1

    def test_different_grid_sizes(self):
        poly = Polygon([(0.12, 0.18), (4.87, 0.18), (4.87, 3.92), (0.12, 3.92)])
        snapped_fine = snap_to_grid(poly, 0.01)
        snapped_coarse = snap_to_grid(poly, 0.1)
        assert snapped_fine.is_valid
        assert snapped_coarse.is_valid


class TestAlignWalls:
    def test_snaps_near_horizontal(self):
        poly = Polygon([(0, 0), (5, 0.15), (5, 4), (0, 4)])
        aligned = align_walls(poly, angle_threshold_deg=5.0)
        coords = list(aligned.exterior.coords)
        assert abs(coords[1][1] - coords[0][1]) < 0.01

    def test_preserves_diagonal(self):
        poly = Polygon([(0, 0), (5, 5), (5, 10), (0, 10)])
        aligned = align_walls(poly, angle_threshold_deg=5.0)
        # 45-degree wall should NOT snap (> 5° from any axis)
        assert aligned.is_valid


class TestEnsureMinimumArea:
    def test_scales_up_small_room(self):
        poly = box(0, 0, 2, 2)  # 4 sqm
        result = ensure_minimum_area(poly, 9.0)
        assert result.area >= 9.0

    def test_leaves_large_room_unchanged(self):
        poly = box(0, 0, 5, 4)  # 20 sqm
        result = ensure_minimum_area(poly, 9.0)
        assert abs(result.area - 20.0) < 0.1


class TestRemoveOverlaps:
    def test_removes_simple_overlap(self):
        room_a = box(0, 0, 5, 4)
        room_b = box(4, 0, 8, 4)
        result = remove_overlaps([room_a, room_b])
        assert len(result) == 2
        assert result[0].intersection(result[1]).area < 0.01

    def test_no_overlap_unchanged(self):
        room_a = box(0, 0, 5, 4)
        room_b = box(5, 0, 10, 4)
        result = remove_overlaps([room_a, room_b])
        assert abs(result[0].area - 20) < 0.1
        assert abs(result[1].area - 20) < 0.1

    def test_three_room_overlap(self):
        rooms = [box(0, 0, 5, 4), box(4, 0, 8, 4), box(3, 3, 6, 7)]
        result = remove_overlaps(rooms)
        # No pair should overlap
        for i in range(len(result)):
            for j in range(i + 1, len(result)):
                assert result[i].intersection(result[j]).area < 0.1


class TestCheckContainment:
    def test_rooms_inside_boundary(self):
        boundary = box(0, 0, 10, 10)
        rooms = [box(1, 1, 4, 4), box(5, 1, 9, 4)]
        result = check_containment(rooms, boundary)
        assert all(result)

    def test_room_outside_boundary(self):
        boundary = box(0, 0, 10, 10)
        rooms = [box(8, 8, 12, 12)]
        result = check_containment(rooms, boundary)
        assert not result[0]

    def test_room_exactly_at_boundary(self):
        boundary = box(0, 0, 10, 10)
        rooms = [box(0, 0, 10, 10)]
        result = check_containment(rooms, boundary)
        assert result[0]


class TestExtractWallSegments:
    def test_shared_wall_is_interior(self):
        room_a = box(0, 0, 5, 4)
        room_b = box(5, 0, 10, 4)
        walls = extract_wall_segments([room_a, room_b])
        shared = [w for w in walls if not w["is_exterior"]]
        assert len(shared) >= 1

    def test_single_room_all_exterior(self):
        room = box(0, 0, 5, 4)
        walls = extract_wall_segments([room])
        assert all(w["is_exterior"] for w in walls)
        assert len(walls) == 4

    def test_wall_has_coordinates(self):
        room = box(0, 0, 5, 4)
        walls = extract_wall_segments([room])
        for w in walls:
            assert "x" in w["start"]
            assert "y" in w["start"]
            assert "x" in w["end"]
            assert "y" in w["end"]


class TestCleanupPipeline:
    def test_full_pipeline(self):
        boundary = box(0, 0, 9, 8)
        raw_rooms = [
            Polygon([(0.03, 0.02), (4.51, 0.02), (4.51, 3.98), (0.03, 3.98)]),
            Polygon([(4.49, 0.02), (8.97, 0.02), (8.97, 3.98), (4.49, 3.98)]),
            Polygon([(0.03, 4.02), (8.97, 4.02), (8.97, 7.98), (0.03, 7.98)]),
        ]
        result = cleanup_pipeline(raw_rooms, boundary, grid_size_m=0.05)

        assert len(result["rooms"]) == 3
        assert len(result["walls"]) > 0
        for room in result["rooms"]:
            assert room.is_valid
            assert room.area > 0

    def test_pipeline_with_min_areas(self):
        boundary = box(0, 0, 15, 10)
        raw_rooms = [box(0, 0, 3, 3), box(4, 0, 7, 3)]
        result = cleanup_pipeline(raw_rooms, boundary, min_areas=[12.0, 12.0])
        for room in result["rooms"]:
            assert room.area >= 11.5  # Allow small tolerance

    def test_pipeline_reports_boundary_violations(self):
        boundary = box(0, 0, 5, 5)
        raw_rooms = [box(0, 0, 10, 10)]
        result = cleanup_pipeline(raw_rooms, boundary)
        assert len(result["issues"]) > 0
        assert not result["is_valid"]


# ============================================================
# Operations tests
# ============================================================

class TestBooleanOps:
    def test_union(self):
        result = boolean_union([box(0, 0, 5, 4), box(3, 0, 8, 4)])
        assert abs(result.area - 32.0) < 0.1

    def test_intersection(self):
        result = boolean_intersection(box(0, 0, 5, 4), box(3, 0, 8, 4))
        assert result is not None
        assert abs(result.area - 8.0) < 0.1

    def test_intersection_no_overlap(self):
        result = boolean_intersection(box(0, 0, 5, 4), box(6, 0, 10, 4))
        assert result is None

    def test_difference(self):
        result = boolean_difference(box(0, 0, 5, 4), box(3, 0, 8, 4))
        assert result is not None
        assert abs(result.area - 12.0) < 0.1

    def test_difference_complete_subtraction(self):
        result = boolean_difference(box(1, 1, 3, 3), box(0, 0, 5, 5))
        assert result is None


class TestBufferAndClip:
    def test_buffer_expand(self):
        poly = box(0, 0, 5, 4)
        result = buffer_polygon(poly, 0.5)
        assert result.area > poly.area

    def test_buffer_shrink(self):
        poly = box(0, 0, 5, 4)
        result = buffer_polygon(poly, -0.5)
        assert result.area < poly.area

    def test_clip_to_boundary(self):
        room = box(0, 0, 10, 10)
        boundary = box(0, 0, 5, 5)
        result = clip_to_boundary(room, boundary)
        assert abs(result.area - 25.0) < 0.1

    def test_wall_centerline(self):
        wall = wall_centerline_to_polygon((0, 0), (5, 0), thickness_m=0.15)
        assert abs(wall.area - 5 * 0.15) < 0.01


class TestBuildableEnvelope:
    def test_default_setbacks(self):
        env = compute_buildable_envelope(12.0, 20.0)
        assert abs(env.bounds[0] - 1.5) < 0.01   # left
        assert abs(env.bounds[1] - 4.5) < 0.01   # front
        assert abs(env.bounds[2] - 10.5) < 0.01  # right = 12 - 1.5
        assert abs(env.bounds[3] - 12.5) < 0.01  # rear = 20 - 7.5
        assert abs(env.area - 72.0) < 0.1

    def test_custom_setbacks(self):
        env = compute_buildable_envelope(15.0, 25.0, 6, 6, 2, 2)
        assert abs(env.area - 11.0 * 13.0) < 0.1


class TestSplitAndMerge:
    def test_split_horizontal(self):
        poly = box(0, 0, 10, 10)
        bottom, top = split_polygon_horizontal(poly, 0.5)
        assert abs(bottom.area - 50.0) < 0.1
        assert abs(top.area - 50.0) < 0.1

    def test_split_vertical(self):
        poly = box(0, 0, 10, 10)
        left, right = split_polygon_vertical(poly, 0.5)
        assert abs(left.area - 50.0) < 0.1
        assert abs(right.area - 50.0) < 0.1

    def test_split_unequal(self):
        poly = box(0, 0, 10, 10)
        left, right = split_polygon_vertical(poly, 0.3)
        assert left.area < right.area

    def test_merge_rooms(self):
        a = box(0, 0, 5, 4)
        b = box(5, 0, 10, 4)
        merged = merge_rooms(a, b)
        assert abs(merged.area - 40.0) < 0.1


class TestAreaDeviation:
    def test_zero_deviation(self):
        assert compute_area_deviation([10, 20, 15], [10, 20, 15]) == 0.0

    def test_nonzero_deviation(self):
        dev = compute_area_deviation([12, 18], [10, 20])
        assert dev > 0

    def test_empty_lists(self):
        assert compute_area_deviation([], []) == 0.0


# ============================================================
# Adjacency tests
# ============================================================

class TestAdjacencyGraph:
    def test_two_adjacent_rooms(self):
        rooms = [box(0, 0, 5, 4), box(5, 0, 10, 4)]
        graph = build_adjacency_graph(rooms)
        assert graph.are_adjacent(0, 1)
        assert graph.neighbors(0) == [1]
        assert graph.neighbors(1) == [0]

    def test_two_separated_rooms(self):
        rooms = [box(0, 0, 5, 4), box(8, 0, 13, 4)]
        graph = build_adjacency_graph(rooms)
        assert not graph.are_adjacent(0, 1)
        assert graph.neighbors(0) == []

    def test_three_room_adjacency(self):
        rooms = [
            box(0, 0, 5, 4),   # Room 0
            box(5, 0, 10, 4),  # Room 1 — adjacent to 0
            box(0, 4, 10, 8),  # Room 2 — adjacent to 0 and 1
        ]
        graph = build_adjacency_graph(rooms)
        assert graph.are_adjacent(0, 1)
        assert graph.are_adjacent(0, 2)
        assert graph.are_adjacent(1, 2)

    def test_adjacency_matrix(self):
        rooms = [box(0, 0, 5, 4), box(5, 0, 10, 4)]
        graph = build_adjacency_graph(rooms)
        mat = graph.matrix
        assert mat[0][1] > 0
        assert mat[1][0] > 0
        assert mat[0][0] == 0


class TestAdjacencyValidation:
    def test_satisfied_constraint(self):
        rooms = [box(0, 0, 5, 4), box(5, 0, 10, 4)]
        violations = validate_adjacency_constraints(
            rooms, ["kitchen", "dining"],
            [{"room_a_id": "kitchen", "room_b_id": "dining", "relationship": "adjacent"}],
        )
        assert len(violations) == 0

    def test_violated_adjacent_constraint(self):
        rooms = [box(0, 0, 5, 4), box(8, 0, 13, 4)]
        violations = validate_adjacency_constraints(
            rooms, ["kitchen", "dining"],
            [{"room_a_id": "kitchen", "room_b_id": "dining", "relationship": "adjacent"}],
        )
        assert len(violations) == 1
        assert violations[0].required == "adjacent"

    def test_separation_constraint(self):
        rooms = [box(0, 0, 5, 4), box(5, 0, 10, 4)]
        violations = validate_adjacency_constraints(
            rooms, ["bedroom", "garage"],
            [{"room_a_id": "bedroom", "room_b_id": "garage", "relationship": "separated"}],
        )
        assert len(violations) == 1
        assert violations[0].required == "separated"
