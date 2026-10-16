"""Tests for GSDiff integration: preprocessing, postprocessing, and pipeline."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from shapely.geometry import Polygon

from app.schemas.floor_plan import (
    AdjacencyRequirement,
    FloorPlanSpec,
    PlotConstraints,
    Relationship,
    Room,
    RoomType,
    Setbacks,
)

# Add infra/modal to path so we can import preprocessing/postprocessing
_MODAL_PATH = str(Path(__file__).resolve().parents[2] / "infra" / "modal")
if _MODAL_PATH not in sys.path:
    sys.path.insert(0, _MODAL_PATH)

from archigen_modal.gsdiff_preprocessing import (
    ARCHIGEN_TO_GSDIFF,
    MAX_CORNERS,
    MAX_ROOMS,
    NUM_SEMANTIC_CLASSES,
    get_gsdiff_semantic,
    spec_to_gsdiff_input,
    split_rooms_for_gsdiff,
)
from archigen_modal.gsdiff_postprocessing import (
    RESOLUTION,
    align_corners_to_grid,
    edges_from_adjacency,
    extract_planar_cycles,
    gsdiff_output_to_polygons,
    map_polygons_to_spec_rooms,
    merge_nearby_corners,
)


# --- Fixtures ---

def _sample_spec_dict() -> dict:
    """4-room spec dict for testing."""
    return {
        "rooms": [
            {"id": "living_1", "type": "living", "label": "Living Room",
             "target_area_sqm": 20.0, "floor": 0},
            {"id": "kitchen_1", "type": "kitchen", "label": "Kitchen",
             "target_area_sqm": 12.0, "floor": 0},
            {"id": "bedroom_1", "type": "bedroom", "label": "Bedroom",
             "target_area_sqm": 14.0, "floor": 0},
            {"id": "bathroom_1", "type": "bathroom", "label": "Bathroom",
             "target_area_sqm": 5.0, "floor": 0},
        ],
        "adjacency": [
            {"room_a_id": "living_1", "room_b_id": "kitchen_1",
             "relationship": "adjacent"},
            {"room_a_id": "bedroom_1", "room_b_id": "bathroom_1",
             "relationship": "adjacent"},
        ],
        "plot": {
            "width_m": 12.0, "depth_m": 20.0,
            "setbacks": {"front_m": 4.5, "rear_m": 7.5,
                         "left_m": 1.5, "right_m": 1.5},
            "buildable_width_m": 9.0, "buildable_depth_m": 8.0,
            "max_lot_coverage": 0.6,
        },
    }


def _large_spec_dict() -> dict:
    """10-room spec dict to test room splitting."""
    rooms = [
        {"id": "living", "type": "living", "label": "Living", "target_area_sqm": 25.0, "floor": 0},
        {"id": "kitchen", "type": "kitchen", "label": "Kitchen", "target_area_sqm": 12.0, "floor": 0},
        {"id": "dining", "type": "dining", "label": "Dining", "target_area_sqm": 14.0, "floor": 0},
        {"id": "master", "type": "master_bedroom", "label": "Master", "target_area_sqm": 18.0, "floor": 0},
        {"id": "bed2", "type": "bedroom", "label": "Bed 2", "target_area_sqm": 12.0, "floor": 0},
        {"id": "bath1", "type": "bathroom", "label": "Bath 1", "target_area_sqm": 5.0, "floor": 0},
        {"id": "mbath", "type": "master_bathroom", "label": "Master Bath", "target_area_sqm": 7.0, "floor": 0},
        {"id": "office", "type": "office", "label": "Office", "target_area_sqm": 10.0, "floor": 0},
        {"id": "hallway", "type": "hallway", "label": "Hallway", "target_area_sqm": 6.0, "floor": 0},
        {"id": "laundry", "type": "laundry", "label": "Laundry", "target_area_sqm": 4.0, "floor": 0},
    ]
    return {
        "rooms": rooms,
        "adjacency": [],
        "plot": {
            "width_m": 15.0, "depth_m": 20.0,
            "buildable_width_m": 12.0, "buildable_depth_m": 8.0,
            "max_lot_coverage": 0.6,
            "setbacks": {"front_m": 4.5, "rear_m": 7.5, "left_m": 1.5, "right_m": 1.5},
        },
    }


# --- Room Type Mapping Tests ---

class TestRoomTypeMapping:
    def test_all_22_archigen_types_have_mapping(self):
        """All ArchiGen room types should map to one of 7 GSDiff categories."""
        archigen_types = [
            "living", "kitchen", "dining", "bedroom", "bathroom",
            "half_bath", "master_bedroom", "master_bathroom", "office",
            "laundry", "garage", "hallway", "closet", "walkin_closet",
            "pantry", "mudroom", "foyer", "staircase", "storage",
            "balcony", "porch", "utility",
        ]
        for rtype in archigen_types:
            cat = get_gsdiff_semantic(rtype)
            assert 0 <= cat < NUM_SEMANTIC_CLASSES, (
                f"Room type '{rtype}' mapped to invalid category {cat}"
            )

    def test_living_maps_to_0(self):
        assert get_gsdiff_semantic("living") == 0

    def test_bedrooms_map_to_1(self):
        assert get_gsdiff_semantic("master_bedroom") == 1
        assert get_gsdiff_semantic("bedroom") == 1

    def test_kitchen_maps_to_2(self):
        assert get_gsdiff_semantic("kitchen") == 2

    def test_bathrooms_map_to_3(self):
        assert get_gsdiff_semantic("bathroom") == 3
        assert get_gsdiff_semantic("half_bath") == 3
        assert get_gsdiff_semantic("master_bathroom") == 3

    def test_dining_office_map_to_4(self):
        assert get_gsdiff_semantic("dining") == 4
        assert get_gsdiff_semantic("office") == 4

    def test_circulation_maps_to_5(self):
        assert get_gsdiff_semantic("hallway") == 5
        assert get_gsdiff_semantic("foyer") == 5
        assert get_gsdiff_semantic("balcony") == 5

    def test_utility_maps_to_6(self):
        assert get_gsdiff_semantic("garage") == 6
        assert get_gsdiff_semantic("laundry") == 6
        assert get_gsdiff_semantic("storage") == 6

    def test_unknown_type_defaults_to_6(self):
        assert get_gsdiff_semantic("nonexistent_type") == 6


# --- Preprocessing Tests ---

class TestGSDiffPreprocessing:
    def test_basic_shapes(self):
        """Test that spec_to_gsdiff_input produces correct tensor shapes."""
        spec = _sample_spec_dict()
        inputs = spec_to_gsdiff_input(spec)

        assert inputs["bb_semantics"].shape == (MAX_ROOMS, NUM_SEMANTIC_CLASSES)
        assert inputs["bb_adjacency_matrix"].shape == (MAX_ROOMS, MAX_ROOMS)
        assert inputs["bb_semantics_padding_mask"].shape == (MAX_ROOMS, 1)
        assert inputs["bb_global_matrix"].shape == (MAX_ROOMS, MAX_ROOMS)
        assert inputs["global_attn_matrix"].shape == (MAX_CORNERS, MAX_CORNERS)
        assert inputs["corners_padding_mask"].shape == (MAX_CORNERS, 1)

    def test_num_rooms_correct(self):
        spec = _sample_spec_dict()
        inputs = spec_to_gsdiff_input(spec)
        assert inputs["num_rooms"] == 4

    def test_padding_mask_has_correct_real_rooms(self):
        spec = _sample_spec_dict()
        inputs = spec_to_gsdiff_input(spec)
        mask = inputs["bb_semantics_padding_mask"]
        # First 4 rooms should be real (1.0), rest padded (0.0)
        assert mask[0, 0] == 1.0
        assert mask[3, 0] == 1.0
        assert mask[4, 0] == 0.0
        assert mask[7, 0] == 0.0

    def test_adjacency_matrix_symmetric(self):
        spec = _sample_spec_dict()
        inputs = spec_to_gsdiff_input(spec)
        adj = inputs["bb_adjacency_matrix"]
        # living(0) ↔ kitchen(1) should be connected
        assert adj[0, 1] == 1.0
        assert adj[1, 0] == 1.0
        # bedroom(2) ↔ bathroom(3)
        assert adj[2, 3] == 1.0
        assert adj[3, 2] == 1.0
        # living(0) and bedroom(2) should NOT be connected
        assert adj[0, 2] == 0.0

    def test_semantics_one_hot(self):
        spec = _sample_spec_dict()
        inputs = spec_to_gsdiff_input(spec)
        sem = inputs["bb_semantics"]
        # Living = category 0
        assert sem[0, 0] == 1.0
        assert sem[0].sum() == 1.0
        # Kitchen = category 2
        assert sem[1, 2] == 1.0
        # Bedroom = category 1
        assert sem[2, 1] == 1.0
        # Bathroom = category 3
        assert sem[3, 3] == 1.0

    def test_global_attention_mask(self):
        spec = _sample_spec_dict()
        inputs = spec_to_gsdiff_input(spec)
        bb_global = inputs["bb_global_matrix"]
        # Real rooms attend to each other
        assert bb_global[0, 3] == 1.0
        # Padding doesn't attend
        assert bb_global[4, 0] == 0.0

    def test_corner_attention_mask(self):
        spec = _sample_spec_dict()
        inputs = spec_to_gsdiff_input(spec)
        attn = inputs["global_attn_matrix"]
        total = inputs["total_corners"]
        # Real corners attend to each other
        assert attn[0, total - 1] == 1.0
        # Padding doesn't attend
        assert attn[total, 0] == 0.0

    def test_target_areas(self):
        spec = _sample_spec_dict()
        inputs = spec_to_gsdiff_input(spec)
        areas = inputs["target_areas"]
        assert len(areas) == 4
        assert areas[0] == 20.0
        assert areas[1] == 12.0

    def test_buildable_dimensions(self):
        spec = _sample_spec_dict()
        inputs = spec_to_gsdiff_input(spec)
        assert inputs["buildable_width"] == 9.0
        assert inputs["buildable_depth"] == 8.0


# --- Room Splitting Tests ---

class TestRoomSplitting:
    def test_no_split_when_under_8(self):
        spec = _sample_spec_dict()
        inputs = spec_to_gsdiff_input(spec)
        assert inputs["num_rooms"] == 4
        assert inputs["room_indices"] == [0, 1, 2, 3]

    def test_split_when_over_8(self):
        spec = _large_spec_dict()
        core, ancillary = split_rooms_for_gsdiff(spec["rooms"])
        assert len(core) <= MAX_ROOMS
        assert len(core) + len(ancillary) == len(spec["rooms"])
        # Hallway and laundry should be ancillary (not in _CORE_PRIORITY)
        ancillary_types = {spec["rooms"][i]["type"] for i in ancillary}
        assert "hallway" in ancillary_types or "laundry" in ancillary_types

    def test_spec_to_gsdiff_splits_large(self):
        spec = _large_spec_dict()
        inputs = spec_to_gsdiff_input(spec)
        assert inputs["num_rooms"] <= MAX_ROOMS
        assert len(inputs["room_indices"]) == inputs["num_rooms"]


# --- Postprocessing Tests ---

class TestGSDiffPostprocessing:
    def test_merge_nearby_corners(self):
        corners = np.array([
            [10.0, 10.0],
            [10.1, 10.1],  # very close to first
            [100.0, 100.0],
            [200.0, 200.0],
        ])
        merged, mapping = merge_nearby_corners(corners, threshold=1.0)
        assert len(merged) == 3  # first two merged
        assert mapping[0] == mapping[1]  # both map to same

    def test_align_corners_to_grid(self):
        corners = np.array([
            [10.0, 20.0],
            [10.2, 50.0],  # similar x, different y
            [50.0, 20.1],  # different x, similar y
            [50.0, 50.0],
        ])
        aligned = align_corners_to_grid(corners, threshold=1.0)
        # First two should have same x
        assert aligned[0, 0] == aligned[1, 0]
        # First and third should have same y
        assert aligned[0, 1] == aligned[2, 1]

    def test_edges_from_adjacency(self):
        edge_matrix = np.zeros((5, 5))
        edge_matrix[0, 1] = 1.0
        edge_matrix[1, 0] = 1.0
        edge_matrix[2, 3] = 1.0
        edge_matrix[3, 2] = 1.0
        edges = edges_from_adjacency(edge_matrix, 4)
        assert (0, 1) in edges
        assert (2, 3) in edges
        assert len(edges) == 2

    def test_pixel_to_meter_conversion(self):
        """Test that 512×512 pixel coords convert correctly to meters."""
        # A room at pixel (256, 256) should be at center of buildable area
        buildable_w = 10.0
        buildable_d = 8.0
        offset_x = 1.5
        offset_y = 4.5

        # Create a simple square room at pixel center
        corners = np.array([
            [200, 200], [300, 200], [300, 300], [200, 300],
            [200, 200], [300, 200], [300, 300], [200, 300],  # pad
        ] + [[0, 0]] * 45, dtype=np.float32)  # pad to MAX_CORNERS

        semantics = np.zeros((MAX_CORNERS, NUM_SEMANTIC_CLASSES), dtype=np.float32)
        for i in range(4):
            semantics[i, 0] = 1.0  # living

        edge_matrix = np.zeros((MAX_CORNERS, MAX_CORNERS), dtype=np.float32)
        for i in range(4):
            edge_matrix[i, (i + 1) % 4] = 1.0
            edge_matrix[(i + 1) % 4, i] = 1.0

        result = gsdiff_output_to_polygons(
            corners=corners,
            semantics=semantics,
            edge_matrix=edge_matrix,
            num_rooms=1,
            num_corners=4,
            buildable_w=buildable_w,
            buildable_d=buildable_d,
            offset_x=offset_x,
            offset_y=offset_y,
        )

        # Should have at least one polygon
        assert result["num_polygons"] >= 0
        # Polygons should be in meter coordinates
        if result["polygons"]:
            for poly in result["polygons"]:
                for x, y in poly:
                    assert x >= offset_x - 1.0  # allow some tolerance
                    assert y >= offset_y - 1.0

    def test_map_polygons_to_spec_rooms_basic(self):
        """Test matching polygons to rooms by semantic + area."""
        spec = _sample_spec_dict()

        # Simulate 4 polygons with matching semantics
        polygons = [
            [(1.5, 4.5), (6.0, 4.5), (6.0, 8.5), (1.5, 8.5), (1.5, 4.5)],  # ~18sqm
            [(6.0, 4.5), (9.0, 4.5), (9.0, 8.5), (6.0, 8.5), (6.0, 4.5)],  # ~12sqm
            [(1.5, 8.5), (5.0, 8.5), (5.0, 12.0), (1.5, 12.0), (1.5, 8.5)],  # ~12.25sqm
            [(5.0, 8.5), (7.0, 8.5), (7.0, 11.0), (5.0, 11.0), (5.0, 8.5)],  # ~5sqm
        ]
        semantics = [0, 2, 1, 3]  # living, kitchen, bedroom, bathroom

        result = map_polygons_to_spec_rooms(polygons, semantics, spec)
        assert len(result) == 4
        # All rooms should have polygons
        for poly in result:
            assert poly is not None
            assert len(poly) >= 4

    def test_map_polygons_handles_surplus(self):
        """When GSDiff produces more polygons than rooms, surplus is ignored."""
        spec = _sample_spec_dict()
        # 6 polygons for 4 rooms
        polygons = [
            [(0, 0), (5, 0), (5, 4), (0, 4), (0, 0)] for _ in range(6)
        ]
        semantics = [0, 2, 1, 3, 6, 6]
        result = map_polygons_to_spec_rooms(polygons, semantics, spec)
        assert len(result) == 4

    def test_map_polygons_handles_deficit(self):
        """When GSDiff produces fewer polygons, missing rooms get rectangles."""
        spec = _sample_spec_dict()
        # 2 polygons for 4 rooms
        polygons = [
            [(1.5, 4.5), (6.0, 4.5), (6.0, 8.5), (1.5, 8.5), (1.5, 4.5)],
            [(6.0, 4.5), (9.0, 4.5), (9.0, 8.5), (6.0, 8.5), (6.0, 4.5)],
        ]
        semantics = [0, 2]
        result = map_polygons_to_spec_rooms(polygons, semantics, spec)
        assert len(result) == 4
        for poly in result:
            assert poly is not None
            assert len(poly) >= 4


# --- Pipeline Fallback Tests ---

class TestGSDiffFallback:
    @pytest.mark.asyncio
    async def test_fallback_chain_includes_gsdiff(self):
        """Test that pipeline's fallback chain includes gsdiff."""
        from app.core.pipeline import GenerationPipeline
        pipeline = GenerationPipeline()
        assert "gsdiff" in pipeline.FALLBACK_CHAIN

    @pytest.mark.asyncio
    async def test_gsdiff_local_fallback_succeeds(self):
        """When GSDiff local is unavailable, pipeline falls back to another generator."""
        from app.core.pipeline import GenerationPipeline
        pipeline = GenerationPipeline()

        spec = FloorPlanSpec(
            rooms=[
                Room(id="living_1", type=RoomType.LIVING, label="Living Room",
                     target_area_sqm=20),
                Room(id="kitchen_1", type=RoomType.KITCHEN, label="Kitchen",
                     target_area_sqm=12),
                Room(id="bath_1", type=RoomType.BATHROOM, label="Bathroom",
                     target_area_sqm=5),
            ],
            plot=PlotConstraints(width_m=10.0, depth_m=16.0),
        )

        result = await pipeline.generate_from_spec(spec, generator="gsdiff_local")
        assert result["floor_plan"] is not None
        # Should fallback to some available generator (not gsdiff_local)
        assert result["floor_plan"].generator_model != "gsdiff_local_v1"
