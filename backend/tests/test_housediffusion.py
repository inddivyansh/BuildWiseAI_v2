"""
Tests for HouseDiffusion integration: preprocessing, postprocessing, and fallback chain.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from shapely.geometry import Polygon

# Add archigen_modal to path for direct import
_modal_pkg = Path(__file__).resolve().parents[2] / "infra" / "modal"
if str(_modal_pkg) not in sys.path:
    sys.path.insert(0, str(_modal_pkg))

from archigen_modal.preprocessing import (
    ARCHIGEN_TO_RPLAN,
    MAX_NUM_POINTS,
    NUM_ROOM_CLASSES,
    build_room_type_onehot,
    get_rplan_index,
    spec_to_housediff_input,
)
from archigen_modal.postprocessing import (
    housediff_output_to_polygons,
    decode_analog_bit,
)
from app.schemas.floor_plan import (
    AdjacencyRequirement,
    FloorPlanSpec,
    PlotConstraints,
    Room,
    RoomType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_spec() -> FloorPlanSpec:
    """Standard 4-room spec for testing."""
    return FloorPlanSpec(
        rooms=[
            Room(id="living_1", type=RoomType.LIVING, label="Living Room",
                 target_area_sqm=20.0, floor=0),
            Room(id="kitchen_1", type=RoomType.KITCHEN, label="Kitchen",
                 target_area_sqm=12.0, floor=0),
            Room(id="bedroom_1", type=RoomType.BEDROOM, label="Bedroom",
                 target_area_sqm=14.0, floor=0),
            Room(id="bathroom_1", type=RoomType.BATHROOM, label="Bathroom",
                 target_area_sqm=5.0, floor=0),
        ],
        adjacency=[
            AdjacencyRequirement(room_a_id="living_1", room_b_id="kitchen_1"),
            AdjacencyRequirement(room_a_id="living_1", room_b_id="bedroom_1"),
            AdjacencyRequirement(room_a_id="bedroom_1", room_b_id="bathroom_1"),
        ],
        plot=PlotConstraints(width_m=12.0, depth_m=20.0),
    )


@pytest.fixture
def sample_spec_dict(sample_spec: FloorPlanSpec) -> dict:
    """Serialized spec dict."""
    return sample_spec.model_dump()


# ---------------------------------------------------------------------------
# Preprocessing tests
# ---------------------------------------------------------------------------

class TestRoomTypeMapping:
    """Test ArchiGen → RPLAN room type mapping."""

    def test_all_archigen_types_mapped(self):
        """Every ArchiGen RoomType should have a mapping (except OTHER has a default)."""
        for rt in RoomType:
            idx = get_rplan_index(rt.value)
            assert 0 <= idx < 17, f"Room type {rt.value} mapped to invalid index {idx}"

    def test_known_mappings(self):
        assert get_rplan_index("living") == 1
        assert get_rplan_index("kitchen") == 3
        assert get_rplan_index("bathroom") == 4
        assert get_rplan_index("master_bedroom") == 2
        assert get_rplan_index("bedroom") == 6
        assert get_rplan_index("office") == 7
        assert get_rplan_index("foyer") == 11

    def test_unknown_type_defaults(self):
        """Unknown types should fall back to guest_room (9)."""
        assert get_rplan_index("nonexistent_room") == 9

    def test_mapping_completeness(self):
        """All ArchiGen room types (from the enum) should appear in the mapping."""
        archigen_types = {rt.value for rt in RoomType}
        mapped_types = set(ARCHIGEN_TO_RPLAN.keys())
        # Every type except OTHER should be explicitly mapped
        missing = archigen_types - mapped_types - {"other"}
        assert not missing, f"Unmapped room types: {missing}"


class TestSpecSerialization:
    """Test spec → dict → preprocessing roundtrip."""

    def test_roundtrip_preserves_room_count(self, sample_spec: FloorPlanSpec):
        spec_dict = sample_spec.model_dump()
        result = spec_to_housediff_input(spec_dict)
        assert result["num_rooms"] == len(sample_spec.rooms)

    def test_room_types_shape(self, sample_spec_dict: dict):
        result = spec_to_housediff_input(sample_spec_dict)
        # room_types is per-corner, padded to MAX_NUM_POINTS
        assert result["room_types"].shape == (MAX_NUM_POINTS, NUM_ROOM_CLASSES)

    def test_num_corners_per_room(self, sample_spec_dict: dict):
        """All rooms should have 4 corners (rectangular output)."""
        result = spec_to_housediff_input(sample_spec_dict)
        assert result["num_corners_per_room"] == [4, 4, 4, 4]
        assert result["total_corners"] == 16

    def test_attention_masks_shape(self, sample_spec_dict: dict):
        """Attention masks should be MAX_NUM_POINTS × MAX_NUM_POINTS."""
        result = spec_to_housediff_input(sample_spec_dict)
        assert result["door_mask"].shape == (MAX_NUM_POINTS, MAX_NUM_POINTS)
        assert result["self_mask"].shape == (MAX_NUM_POINTS, MAX_NUM_POINTS)
        assert result["gen_mask"].shape == (MAX_NUM_POINTS, MAX_NUM_POINTS)

    def test_target_areas_preserved(self, sample_spec_dict: dict):
        result = spec_to_housediff_input(sample_spec_dict)
        np.testing.assert_allclose(
            result["target_areas"],
            [20.0, 12.0, 14.0, 5.0],
        )

    def test_buildable_dimensions(self, sample_spec_dict: dict):
        result = spec_to_housediff_input(sample_spec_dict)
        # 12 - 1.5 - 1.5 = 9.0
        assert result["buildable_width"] == pytest.approx(9.0)
        # 20 - 4.5 - 7.5 = 8.0
        assert result["buildable_depth"] == pytest.approx(8.0)

    def test_gen_mask_real_corners_unmasked(self, sample_spec_dict: dict):
        """Real corner positions should be unmasked (0) in gen_mask."""
        result = spec_to_housediff_input(sample_spec_dict)
        tc = result["total_corners"]  # 16
        # Real corners: gen_mask[i,j] = 0 for i,j < total_corners
        assert (result["gen_mask"][:tc, :tc] == 0).all()
        # Padding: gen_mask[i,j] = 1 for i >= total_corners
        assert (result["gen_mask"][tc:, :] == 1).all()

    def test_self_mask_same_room_unmasked(self, sample_spec_dict: dict):
        """Corners belonging to same room should be unmasked (0) in self_mask."""
        result = spec_to_housediff_input(sample_spec_dict)
        # Room 0 has corners 0-3, room 1 has 4-7, etc.
        # self_mask[0:4, 0:4] should be all 0
        assert (result["self_mask"][0:4, 0:4] == 0).all()
        assert (result["self_mask"][4:8, 4:8] == 0).all()
        # Cross-room should be 1
        assert (result["self_mask"][0:4, 4:8] == 1).all()

    def test_no_adjacency_creates_fully_connected(self):
        """If no adjacency is specified, all rooms should be door-connected."""
        spec_dict = {
            "rooms": [
                {"id": "r1", "type": "living", "target_area_sqm": 10.0, "floor": 0},
                {"id": "r2", "type": "kitchen", "target_area_sqm": 8.0, "floor": 0},
                {"id": "r3", "type": "bedroom", "target_area_sqm": 12.0, "floor": 0},
            ],
            "adjacency": [],
            "plot": {"width_m": 10.0, "depth_m": 15.0,
                     "buildable_width_m": 7.0, "buildable_depth_m": 3.0},
        }
        result = spec_to_housediff_input(spec_dict)
        # All pairs should be connected in door_mask
        # Room 0 (corners 0-3) to Room 1 (corners 4-7): door_mask should be 0
        assert (result["door_mask"][0:4, 4:8] == 0).all()


class TestOneHotEncoding:
    """Test room type one-hot encoding."""

    def test_shape(self):
        room_types = np.array([0, 2, 5, 3])
        onehot = build_room_type_onehot(room_types)
        assert onehot.shape == (4, NUM_ROOM_CLASSES)

    def test_values(self):
        room_types = np.array([0, 2])
        onehot = build_room_type_onehot(room_types)
        assert onehot[0, 0] == 1.0  # living_room
        assert onehot[1, 2] == 1.0  # kitchen
        assert onehot.sum() == 2.0  # exactly 2 ones

    def test_out_of_range_clamped(self):
        """Room types beyond NUM_ROOM_CLASSES should be clamped."""
        room_types = np.array([99])
        onehot = build_room_type_onehot(room_types)
        assert onehot[0, NUM_ROOM_CLASSES - 1] == 1.0


# ---------------------------------------------------------------------------
# Postprocessing tests
# ---------------------------------------------------------------------------

class TestPostprocessing:
    """Test tensor → polygon conversion."""

    def test_basic_four_room_output(self):
        """4 rooms × 4 corners each = 16 corners → 4 valid polygons."""
        # Simulate model output: 16 corners in pixel256 space
        corners = np.array([
            # Room 0: rectangle at top-left
            [10, 10], [60, 10], [60, 60], [10, 60],
            # Room 1: rectangle at top-right
            [70, 10], [120, 10], [120, 60], [70, 60],
            # Room 2: rectangle at bottom-left
            [10, 70], [60, 70], [60, 120], [10, 120],
            # Room 3: rectangle at bottom-right
            [70, 70], [120, 70], [120, 120], [70, 120],
        ], dtype=np.float64)

        num_coords = np.array([4, 4, 4, 4])

        polygons = housediff_output_to_polygons(
            corners=corners,
            num_coords=num_coords,
            buildable_width=9.0,
            buildable_depth=8.0,
            offset_x=1.5,
            offset_y=4.5,
            coord_space="pixel256",
        )

        assert len(polygons) == 4
        for poly_coords in polygons:
            assert len(poly_coords) == 5  # closed polygon (first == last)
            assert poly_coords[0] == poly_coords[-1]
            # Convert to Shapely to check validity
            p = Polygon(poly_coords)
            assert p.is_valid
            assert p.area > 0

    def test_pixel256_scaling(self):
        """Verify pixel256 → meter coordinate conversion."""
        corners = np.array([
            [0, 0], [128, 0], [128, 128], [0, 128],
        ], dtype=np.float64)
        num_coords = np.array([4])

        polygons = housediff_output_to_polygons(
            corners=corners,
            num_coords=num_coords,
            buildable_width=10.0,
            buildable_depth=8.0,
            offset_x=0.0,
            offset_y=0.0,
            coord_space="pixel256",
        )

        p = Polygon(polygons[0])
        # 128/256 * 10 = 5.0, 128/256 * 8 = 4.0 → area = 20.0
        assert p.area == pytest.approx(20.0, rel=0.01)

    def test_normalized_scaling(self):
        """Verify [-1,1] → meter coordinate conversion."""
        corners = np.array([
            [-1, -1], [0, -1], [0, 0], [-1, 0],
        ], dtype=np.float64)
        num_coords = np.array([4])

        polygons = housediff_output_to_polygons(
            corners=corners,
            num_coords=num_coords,
            buildable_width=10.0,
            buildable_depth=8.0,
            offset_x=0.0,
            offset_y=0.0,
            coord_space="normalized",
        )

        p = Polygon(polygons[0])
        # (-1→0)/2 * 10 = 0→5, (-1→0)/2 * 8 = 0→4 → area = 20.0
        assert p.area == pytest.approx(20.0, rel=0.01)

    def test_degenerate_room_falls_back_to_rectangle(self):
        """Collinear corners should trigger rectangle fallback."""
        corners = np.array([
            [10, 10], [20, 10], [30, 10], [40, 10],  # all on same line
        ], dtype=np.float64)
        num_coords = np.array([4])

        polygons = housediff_output_to_polygons(
            corners=corners,
            num_coords=num_coords,
            buildable_width=9.0,
            buildable_depth=8.0,
            target_areas=np.array([12.0]),
            offset_x=1.5,
            offset_y=4.5,
            coord_space="pixel256",
        )

        assert len(polygons) == 1
        p = Polygon(polygons[0])
        assert p.is_valid
        assert p.area > 0  # fallback rectangle has positive area

    def test_offset_applied(self):
        """Coordinates should be shifted by offset (setback values)."""
        corners = np.array([
            [0, 0], [256, 0], [256, 256], [0, 256],
        ], dtype=np.float64)
        num_coords = np.array([4])

        polygons = housediff_output_to_polygons(
            corners=corners,
            num_coords=num_coords,
            buildable_width=9.0,
            buildable_depth=8.0,
            offset_x=1.5,
            offset_y=4.5,
            coord_space="pixel256",
        )

        p = Polygon(polygons[0])
        minx, miny, _, _ = p.bounds
        assert minx == pytest.approx(1.5, abs=0.01)
        assert miny == pytest.approx(4.5, abs=0.01)


class TestAnalogBitDecoding:
    """Test analog bit decoding utility."""

    def test_filters_invalid_corners(self):
        corners = np.array([
            [10, 20, 1.0],   # valid
            [30, 40, -0.5],  # invalid
            [50, 60, 0.5],   # valid
        ])
        result = decode_analog_bit(corners, threshold=0.0)
        assert result.shape == (2, 2)
        np.testing.assert_array_equal(result[0], [10, 20])
        np.testing.assert_array_equal(result[1], [50, 60])

    def test_no_analog_bit_passthrough(self):
        corners = np.array([[10, 20], [30, 40]])
        result = decode_analog_bit(corners)
        assert result.shape == (2, 2)


# ---------------------------------------------------------------------------
# Fallback chain tests
# ---------------------------------------------------------------------------

class TestFallbackChain:
    """Test the generator fallback chain in the pipeline."""

    @pytest.fixture
    def pipeline(self):
        """Create a pipeline with mocked generators."""
        # Avoid importing full pipeline (needs LLMClient etc.)
        # Instead, test the fallback logic directly
        from app.core.pipeline import GenerationPipeline

        with patch("app.core.pipeline.LLMClient"):
            p = GenerationPipeline()
        return p

    def test_constraint_solver_always_registered(self, pipeline):
        assert "constraint_solver" in pipeline.generators

    def test_fallback_chain_defined(self, pipeline):
        assert len(pipeline.FALLBACK_CHAIN) >= 2
        assert "constraint_solver" in pipeline.FALLBACK_CHAIN

    @pytest.mark.asyncio
    async def test_falls_back_on_unavailable(self, sample_spec: FloorPlanSpec):
        """If Modal is unavailable, should fall back to constraint_solver."""
        from app.core.pipeline import GenerationPipeline

        with patch("app.core.pipeline.LLMClient"):
            pipeline = GenerationPipeline()

        # Mock Modal generator as unavailable
        mock_modal = AsyncMock()
        mock_modal.is_available = AsyncMock(return_value=False)
        mock_modal.name = "housediffusion_modal_v1"
        pipeline.generators["housediffusion"] = mock_modal

        # Mock local generator as unavailable
        mock_local = AsyncMock()
        mock_local.is_available = AsyncMock(return_value=False)
        mock_local.name = "housediffusion_local_v1"
        pipeline.generators["housediffusion_local"] = mock_local

        # constraint_solver should work and produce polygons
        result = await pipeline.generate_from_spec(
            spec=sample_spec,
            generator="housediffusion",
        )

        # Should succeed via constraint_solver fallback
        assert result["floor_plan"] is not None
        assert result["errors"] is not None  # may have warnings but not fatal

    @pytest.mark.asyncio
    async def test_uses_first_available(self, sample_spec: FloorPlanSpec):
        """Should use the first available generator in the chain."""
        from app.core.pipeline import GenerationPipeline

        with patch("app.core.pipeline.LLMClient"):
            pipeline = GenerationPipeline()

        # Mock Modal as unavailable
        mock_modal = AsyncMock()
        mock_modal.is_available = AsyncMock(return_value=False)
        mock_modal.name = "housediffusion_modal_v1"
        pipeline.generators["housediffusion"] = mock_modal

        # Mock local as available and returning valid polygons
        from shapely.geometry import box
        mock_polygons = [box(0, 0, 4, 5), box(4, 0, 7, 4),
                         box(0, 5, 4, 9), box(4, 4, 7, 6)]
        mock_local = AsyncMock()
        mock_local.is_available = AsyncMock(return_value=True)
        mock_local.generate = AsyncMock(return_value=mock_polygons)
        mock_local.name = "housediffusion_local_v1"
        pipeline.generators["housediffusion_local"] = mock_local

        result = await pipeline.generate_from_spec(
            spec=sample_spec,
            generator="housediffusion",
        )

        # Should have used the local generator
        mock_local.generate.assert_called_once()
        assert result["floor_plan"] is not None
