"""Tests for ChatHouseDiffusion integration: preprocessing, interface, and pipeline."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

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
from app.services.generation.interface import (
    EditableFloorPlanGenerator,
    FloorPlanGenerator,
)

# Add infra/modal to path for preprocessing imports
_MODAL_PATH = str(Path(__file__).resolve().parents[2] / "infra" / "modal")
if _MODAL_PATH not in sys.path:
    sys.path.insert(0, _MODAL_PATH)

from archigen_modal.chathousediff_preprocessing import (
    ARCHIGEN_TO_CHATHD_LABEL,
    ARCHIGEN_TO_CHATHD_TYPE,
    ENCODED_DIM,
    MAX_ROOMS,
    RESOLUTION,
    T5_EMBED_DIM,
    build_graphormer_dict,
    get_t5_embedding,
    spec_to_chathd_edit_input,
    spec_to_chathd_input,
)


# --- Fixtures ---

def _sample_spec_dict() -> dict:
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
        ],
        "plot": {
            "width_m": 12.0, "depth_m": 20.0,
            "setbacks": {"front_m": 4.5, "rear_m": 7.5,
                         "left_m": 1.5, "right_m": 1.5},
            "buildable_width_m": 9.0, "buildable_depth_m": 8.0,
            "max_lot_coverage": 0.6,
        },
    }


def _mock_t5_features() -> dict:
    """Create mock T5 features for testing.

    Uses CamelCase keys matching the actual t5_feature.pkl format,
    plus location and size keys for graphormer_dict construction.
    """
    features = {}
    # Room category keys (CamelCase, from graph_encoder.py)
    room_keys = [
        "Unknown", "LivingRoom", "MasterRoom", "Kitchen",
        "Bathroom", "DiningRoom", "ChildRoom", "StudyRoom",
        "SecondRoom", "GuestRoom", "Balcony", "Entrance", "Storage",
    ]
    # Location keys
    location_keys = [
        "north", "south", "east", "west",
        "northeast", "northwest", "southeast", "southwest",
        "center", "Unknown",
    ]
    # Size keys
    size_keys = ["XS", "S", "M", "L", "XL", "Unknown"]

    for key in room_keys + location_keys + size_keys:
        features[key] = np.random.randn(T5_EMBED_DIM).astype(np.float32)
    return features


# --- T5 Feature Lookup Tests ---

class TestT5Features:
    def test_all_room_types_have_labels(self):
        """Every ArchiGen room type should map to a ChatHD label."""
        archigen_types = [
            "living", "kitchen", "dining", "bedroom", "bathroom",
            "half_bath", "master_bedroom", "master_bathroom", "office",
            "laundry", "garage", "hallway", "closet", "walkin_closet",
            "pantry", "mudroom", "foyer", "staircase", "storage",
            "balcony", "porch", "utility",
        ]
        for rtype in archigen_types:
            label = ARCHIGEN_TO_CHATHD_LABEL.get(rtype)
            assert label is not None, f"No ChatHD label for '{rtype}'"

    def test_t5_embedding_found(self):
        """T5 embedding should be found for known keys."""
        t5 = _mock_t5_features()
        embed = get_t5_embedding("LivingRoom", t5)
        assert embed.shape == (T5_EMBED_DIM,)
        assert embed.sum() != 0.0  # should not be zero vector

    def test_t5_embedding_fallback(self):
        """Unknown key should return zero vector."""
        t5 = _mock_t5_features()
        embed = get_t5_embedding("NonexistentKey", t5)
        assert embed.shape == (T5_EMBED_DIM,)
        assert embed.sum() == 0.0

    def test_t5_embedding_empty_features(self):
        """With empty features dict, should return zero vector."""
        embed = get_t5_embedding("LivingRoom", {})
        assert embed.shape == (T5_EMBED_DIM,)
        assert embed.sum() == 0.0

    def test_t5_embed_dim_is_768(self):
        """T5 embedding dimension should be 768 (ENCODED_DIM)."""
        assert T5_EMBED_DIM == 768
        assert ENCODED_DIM == 768


# --- Preprocessing Tests ---

class TestChatHDPreprocessing:
    def test_basic_shapes(self):
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        inputs = spec_to_chathd_input(spec, t5)

        assert inputs["room_types"].shape == (4,)
        assert inputs["t5_embeddings"].shape == (4, T5_EMBED_DIM)
        assert inputs["adjacency_matrix"].shape == (4, 4)
        assert inputs["room_areas"].shape == (4,)
        assert inputs["num_rooms"] == 4

    def test_graphormer_dict_shapes(self):
        """graphormer_dict should have correct shapes for the model."""
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        inputs = spec_to_chathd_input(spec, t5)

        gd = inputs["graphormer_dict"]
        assert gd["node_feat"].shape == (1, MAX_ROOMS, ENCODED_DIM * 3)
        assert gd["in_degree"].shape == (1, MAX_ROOMS)
        assert gd["out_degree"].shape == (1, MAX_ROOMS)
        assert gd["path_data"].shape == (1, MAX_ROOMS, MAX_ROOMS, 4, 1)
        assert gd["dist"].shape == (1, MAX_ROOMS, MAX_ROOMS)
        assert gd["attn_mask"].shape == (1, MAX_ROOMS + 1, MAX_ROOMS + 1)

    def test_room_type_indices(self):
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        inputs = spec_to_chathd_input(spec, t5)

        types = inputs["room_types"]
        assert types[0] == ARCHIGEN_TO_CHATHD_TYPE["living"]  # 1
        assert types[1] == ARCHIGEN_TO_CHATHD_TYPE["kitchen"]  # 3
        assert types[2] == ARCHIGEN_TO_CHATHD_TYPE["bedroom"]  # 6
        assert types[3] == ARCHIGEN_TO_CHATHD_TYPE["bathroom"]  # 4

    def test_adjacency_matrix(self):
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        inputs = spec_to_chathd_input(spec, t5)

        adj = inputs["adjacency_matrix"]
        # living(0) ↔ kitchen(1)
        assert adj[0, 1] == 1.0
        assert adj[1, 0] == 1.0
        # No other explicit connections
        assert adj[0, 2] == 0.0
        assert adj[2, 3] == 0.0

    def test_room_areas_normalized(self):
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        inputs = spec_to_chathd_input(spec, t5)

        areas = inputs["room_areas"]
        # Should sum to 1.0 (normalized)
        assert abs(areas.sum() - 1.0) < 1e-5

    def test_buildable_dimensions(self):
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        inputs = spec_to_chathd_input(spec, t5)

        assert inputs["buildable_width"] == 9.0
        assert inputs["buildable_depth"] == 8.0

    def test_max_rooms_capped(self):
        """Specs with >MAX_ROOMS rooms should be capped."""
        spec = _sample_spec_dict()
        # Add more rooms to exceed MAX_ROOMS
        for i in range(12):
            spec["rooms"].append({
                "id": f"extra_{i}", "type": "bedroom",
                "label": f"Extra {i}", "target_area_sqm": 10.0, "floor": 0,
            })

        t5 = _mock_t5_features()
        inputs = spec_to_chathd_input(spec, t5)
        assert inputs["num_rooms"] <= MAX_ROOMS

    def test_node_feat_has_t5_embeddings(self):
        """node_feat should be non-zero for real rooms."""
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        inputs = spec_to_chathd_input(spec, t5)

        gd = inputs["graphormer_dict"]
        # First 4 rooms should have non-zero features
        for i in range(4):
            assert np.abs(gd["node_feat"][0, i]).sum() > 0
        # Padded rooms should be zero
        for i in range(4, MAX_ROOMS):
            assert np.abs(gd["node_feat"][0, i]).sum() == 0

    def test_degrees_correct(self):
        """in_degree/out_degree should reflect adjacency."""
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        inputs = spec_to_chathd_input(spec, t5)

        gd = inputs["graphormer_dict"]
        # living(0) has 1 neighbor (kitchen) → degree 1+1=2
        assert gd["in_degree"][0, 0] == 2
        # kitchen(1) has 1 neighbor (living) → degree 1+1=2
        assert gd["in_degree"][0, 1] == 2
        # bedroom(2) has 0 neighbors → degree 0+1=1
        assert gd["in_degree"][0, 2] == 1
        # padded rooms should have degree 0
        assert gd["in_degree"][0, 5] == 0

    def test_dist_matrix_correct(self):
        """dist should have correct shortest path distances."""
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        inputs = spec_to_chathd_input(spec, t5)

        gd = inputs["graphormer_dict"]
        # living(0) ↔ kitchen(1): distance 1
        assert gd["dist"][0, 0, 1] == 1
        assert gd["dist"][0, 1, 0] == 1
        # self-distance should be 0
        assert gd["dist"][0, 0, 0] == 0
        # bedroom(2) ↔ bathroom(3): no connection, distance -1
        assert gd["dist"][0, 2, 3] == -1


# --- Graphormer Dict Builder Tests ---

class TestGraphormerDict:
    def test_build_basic(self):
        rooms = [
            {"id": "r1", "type": "living", "target_area_sqm": 20},
            {"id": "r2", "type": "kitchen", "target_area_sqm": 12},
        ]
        adj = [{"room_a_id": "r1", "room_b_id": "r2"}]
        t5 = _mock_t5_features()

        gd = build_graphormer_dict(rooms, adj, t5)
        assert gd["node_feat"].shape == (1, MAX_ROOMS, ENCODED_DIM * 3)
        assert gd["dist"][0, 0, 1] == 1

    def test_attn_mask_masks_padding(self):
        rooms = [
            {"id": "r1", "type": "living", "target_area_sqm": 20},
        ]
        t5 = _mock_t5_features()

        gd = build_graphormer_dict(rooms, [], t5)
        # Position 0 = virtual token, position 1 = room 1
        # Positions 2..10 should be masked
        assert gd["attn_mask"][0, 0, 2] == 1.0
        assert gd["attn_mask"][0, 0, 0] == 0.0


# --- Edit Input Tests ---

class TestChatHDEditInput:
    def test_edit_input_has_previous_plan(self):
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        prev_polygons = [
            [(1.5, 4.5), (6.0, 4.5), (6.0, 8.5), (1.5, 8.5), (1.5, 4.5)],
            [(6.0, 4.5), (9.0, 4.5), (9.0, 8.5), (6.0, 8.5), (6.0, 4.5)],
            [(1.5, 8.5), (5.0, 8.5), (5.0, 12.0), (1.5, 12.0), (1.5, 8.5)],
            [(5.0, 8.5), (7.0, 8.5), (7.0, 11.0), (5.0, 11.0), (5.0, 8.5)],
        ]

        inputs = spec_to_chathd_edit_input(spec, prev_polygons, 0.7, t5)

        assert "previous_plan" in inputs
        assert inputs["previous_plan"].shape == (RESOLUTION, RESOLUTION)
        assert inputs["edit_strength"] == 0.7

    def test_edit_input_has_graphormer_dict(self):
        """Edit inputs should also include graphormer_dict."""
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        prev_polygons = [[] for _ in range(4)]

        inputs = spec_to_chathd_edit_input(spec, prev_polygons, 0.7, t5)
        assert "graphormer_dict" in inputs

    def test_edit_strength_preserved(self):
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        prev_polygons = [[] for _ in range(4)]

        for strength in [0.0, 0.3, 0.5, 0.7, 1.0]:
            inputs = spec_to_chathd_edit_input(spec, prev_polygons, strength, t5)
            assert inputs["edit_strength"] == strength

    def test_previous_plan_rasterized(self):
        """Previous plan with rooms should produce a non-zero raster image."""
        spec = _sample_spec_dict()
        t5 = _mock_t5_features()
        # Create actual polygon coords within the buildable area
        prev_polygons = [
            [(2.0, 5.0), (5.0, 5.0), (5.0, 8.0), (2.0, 8.0), (2.0, 5.0)],
            [(5.0, 5.0), (8.0, 5.0), (8.0, 8.0), (5.0, 8.0), (5.0, 5.0)],
            [(2.0, 8.0), (5.0, 8.0), (5.0, 11.0), (2.0, 11.0), (2.0, 8.0)],
            [(5.0, 8.0), (7.0, 8.0), (7.0, 10.0), (5.0, 10.0), (5.0, 8.0)],
        ]

        inputs = spec_to_chathd_edit_input(spec, prev_polygons, 0.7, t5)
        plan = inputs["previous_plan"]
        # Should have non-zero pixels where rooms were rasterized
        assert plan.sum() > 0.0


# --- Interface Tests ---

class TestEditableInterface:
    def test_editable_is_subclass(self):
        """EditableFloorPlanGenerator should be a subclass of FloorPlanGenerator."""
        assert issubclass(EditableFloorPlanGenerator, FloorPlanGenerator)

    def test_editable_supports_editing(self):
        """EditableFloorPlanGenerator.supports_editing should return True."""
        # Create a concrete implementation for testing
        class TestEditable(EditableFloorPlanGenerator):
            @property
            def name(self):
                return "test_editable"

            async def generate(self, spec):
                return []

            async def edit(self, spec, previous_polygons, edit_strength=0.7):
                return []

        gen = TestEditable()
        assert gen.supports_editing is True

    def test_base_generator_no_editing(self):
        """Base FloorPlanGenerator should not have supports_editing."""
        class TestBase(FloorPlanGenerator):
            @property
            def name(self):
                return "test_base"

            async def generate(self, spec):
                return []

        gen = TestBase()
        assert not hasattr(gen, "supports_editing") or not gen.supports_editing


# --- edit_strength Validation Tests ---

class TestEditStrengthValidation:
    @pytest.mark.asyncio
    async def test_edit_strength_out_of_range(self):
        """edit_strength outside [0, 1] should raise ValueError."""
        from app.services.generation.chathousediff_modal_client import (
            ModalChatHouseDiffusionGenerator,
        )

        gen = ModalChatHouseDiffusionGenerator()
        gen._checked = True
        gen._cls = MagicMock()

        spec = FloorPlanSpec(
            rooms=[
                Room(id="living_1", type=RoomType.LIVING, label="Living",
                     target_area_sqm=20),
            ],
            plot=PlotConstraints(width_m=10.0, depth_m=16.0),
        )

        with pytest.raises(ValueError, match="edit_strength"):
            await gen.edit(spec, [Polygon()], edit_strength=1.5)

        with pytest.raises(ValueError, match="edit_strength"):
            await gen.edit(spec, [Polygon()], edit_strength=-0.1)


# --- Pipeline Fallback Tests ---

class TestChatHDFallback:
    @pytest.mark.asyncio
    async def test_fallback_chain_includes_chathd(self):
        """Pipeline's fallback chain should include chathousediffusion."""
        from app.core.pipeline import GenerationPipeline
        pipeline = GenerationPipeline()
        assert "chathousediffusion" in pipeline.FALLBACK_CHAIN

    @pytest.mark.asyncio
    async def test_final_fallback_chain_order(self):
        """Verify the complete fallback chain order."""
        from app.core.pipeline import GenerationPipeline
        pipeline = GenerationPipeline()
        expected = [
            "housediffusion",
            "gsdiff",
            "chathousediffusion",
            "housediffusion_local",
            "gsdiff_local",
            "constraint_solver",
        ]
        assert pipeline.FALLBACK_CHAIN == expected

    @pytest.mark.asyncio
    async def test_chathd_fallback_succeeds(self):
        """When ChatHD is unavailable, pipeline falls back to another generator."""
        from app.core.pipeline import GenerationPipeline
        pipeline = GenerationPipeline()

        spec = FloorPlanSpec(
            rooms=[
                Room(id="living_1", type=RoomType.LIVING, label="Living Room",
                     target_area_sqm=20),
                Room(id="kitchen_1", type=RoomType.KITCHEN, label="Kitchen",
                     target_area_sqm=12),
            ],
            plot=PlotConstraints(width_m=10.0, depth_m=16.0),
        )

        result = await pipeline.generate_from_spec(
            spec, generator="chathousediffusion"
        )
        assert result["floor_plan"] is not None
        # Should fallback to some available generator (not chathousediffusion)
        assert result["floor_plan"].generator_model != "chathousediffusion_modal_v1"
