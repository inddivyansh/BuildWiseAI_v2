"""Integration tests for the generation pipeline (no LLM needed)."""

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
from app.services.generation.constraint_solver import ConstraintSolver, solve_layout
from app.core.pipeline import GenerationPipeline


# --- Fixtures ---

def _bungalow_spec() -> FloorPlanSpec:
    """3-bedroom bungalow spec."""
    return FloorPlanSpec(
        rooms=[
            Room(id="living_1", type=RoomType.LIVING, label="Living Room", target_area_sqm=25),
            Room(id="kitchen_1", type=RoomType.KITCHEN, label="Kitchen", target_area_sqm=12),
            Room(id="dining_1", type=RoomType.DINING, label="Dining Room", target_area_sqm=14),
            Room(id="master_1", type=RoomType.MASTER_BEDROOM, label="Master Bedroom", target_area_sqm=16),
            Room(id="bed_2", type=RoomType.BEDROOM, label="Bedroom 2", target_area_sqm=12),
            Room(id="bath_1", type=RoomType.BATHROOM, label="Bathroom", target_area_sqm=5),
            Room(id="hallway_1", type=RoomType.HALLWAY, label="Hallway", target_area_sqm=6),
        ],
        adjacency=[
            AdjacencyRequirement(room_a_id="kitchen_1", room_b_id="dining_1", relationship=Relationship.ADJACENT),
            AdjacencyRequirement(room_a_id="living_1", room_b_id="kitchen_1", relationship=Relationship.ADJACENT),
            AdjacencyRequirement(room_a_id="master_1", room_b_id="bath_1", relationship=Relationship.ADJACENT),
        ],
        plot=PlotConstraints(width_m=12.0, depth_m=20.0),
        num_floors=1,
        style="traditional",
    )


def _studio_spec() -> FloorPlanSpec:
    return FloorPlanSpec(
        rooms=[
            Room(id="main_1", type=RoomType.LIVING, label="Main Room", target_area_sqm=20),
            Room(id="kitchen_1", type=RoomType.KITCHEN, label="Kitchen", target_area_sqm=8),
            Room(id="bath_1", type=RoomType.BATHROOM, label="Bathroom", target_area_sqm=4),
        ],
        plot=PlotConstraints(
            width_m=10.0, depth_m=16.0,
            setbacks=Setbacks(front_m=2.0, rear_m=3.0, left_m=1.0, right_m=1.0),
        ),
    )


def _large_house_spec() -> FloorPlanSpec:
    """10-room spec to test scalability."""
    return FloorPlanSpec(
        rooms=[
            Room(id="living", type=RoomType.LIVING, label="Living Room", target_area_sqm=30),
            Room(id="kitchen", type=RoomType.KITCHEN, label="Kitchen", target_area_sqm=15),
            Room(id="dining", type=RoomType.DINING, label="Dining Room", target_area_sqm=16),
            Room(id="master", type=RoomType.MASTER_BEDROOM, label="Master Bed", target_area_sqm=20),
            Room(id="mbath", type=RoomType.MASTER_BATHROOM, label="Master Bath", target_area_sqm=8),
            Room(id="bed2", type=RoomType.BEDROOM, label="Bedroom 2", target_area_sqm=14),
            Room(id="bed3", type=RoomType.BEDROOM, label="Bedroom 3", target_area_sqm=12),
            Room(id="bath", type=RoomType.BATHROOM, label="Bathroom", target_area_sqm=5),
            Room(id="office", type=RoomType.OFFICE, label="Office", target_area_sqm=10),
            Room(id="garage", type=RoomType.GARAGE, label="Garage", target_area_sqm=30),
        ],
        adjacency=[
            AdjacencyRequirement(room_a_id="kitchen", room_b_id="dining", relationship=Relationship.ADJACENT),
            AdjacencyRequirement(room_a_id="master", room_b_id="mbath", relationship=Relationship.ADJACENT),
            AdjacencyRequirement(room_a_id="garage", room_b_id="kitchen", relationship=Relationship.NEARBY),
        ],
        plot=PlotConstraints(width_m=18.0, depth_m=25.0),
    )


# --- Constraint Solver Tests ---

class TestConstraintSolver:
    def test_generates_correct_number_of_rooms(self):
        spec = _bungalow_spec()
        polygons = solve_layout(spec)
        assert len(polygons) == len(spec.rooms)

    def test_all_polygons_are_valid(self):
        spec = _bungalow_spec()
        polygons = solve_layout(spec)
        for i, poly in enumerate(polygons):
            assert isinstance(poly, Polygon), f"Room {i} is not a Polygon"
            assert poly.is_valid, f"Room {i} polygon is invalid"
            assert poly.area > 0, f"Room {i} has zero area"

    def test_rooms_within_buildable_area(self):
        spec = _bungalow_spec()
        polygons = solve_layout(spec)
        bx = spec.plot.setbacks.left_m
        by = spec.plot.setbacks.front_m
        bw = spec.plot.buildable_width_m
        bh = spec.plot.buildable_depth_m
        for i, poly in enumerate(polygons):
            minx, miny, maxx, maxy = poly.bounds
            # Allow 10cm tolerance
            assert minx >= bx - 0.1, f"Room {i} extends past left boundary"
            assert maxx <= bx + bw + 0.1, f"Room {i} extends past right boundary"
            assert miny >= by - 0.1, f"Room {i} extends past front boundary"
            assert maxy <= by + bh + 0.1, f"Room {i} extends past rear boundary"

    def test_studio_layout(self):
        spec = _studio_spec()
        polygons = solve_layout(spec)
        assert len(polygons) == 3
        total_area = sum(p.area for p in polygons)
        assert total_area > 0

    def test_large_house(self):
        spec = _large_house_spec()
        polygons = solve_layout(spec)
        assert len(polygons) == 10
        for p in polygons:
            assert p.area > 0

    @pytest.mark.asyncio
    async def test_generator_interface(self):
        solver = ConstraintSolver()
        assert solver.name == "constraint_solver_v1"
        assert not solver.requires_gpu
        assert await solver.is_available()

        spec = _studio_spec()
        polygons = await solver.generate(spec)
        assert len(polygons) == 3


# --- Full Pipeline Tests (no LLM) ---

class TestPipeline:
    @pytest.mark.asyncio
    async def test_generate_from_spec_bungalow(self):
        pipeline = GenerationPipeline()
        spec = _bungalow_spec()
        result = await pipeline.generate_from_spec(spec, generator="constraint_solver")

        assert result["generation_id"]
        assert result["floor_plan"] is not None
        fp = result["floor_plan"]
        assert len(fp.spec.rooms) == 7
        assert len(fp.walls) > 0
        assert fp.total_area_sqm > 0
        assert fp.generator_model == "constraint_solver_v1"

    @pytest.mark.asyncio
    async def test_generate_from_spec_studio(self):
        pipeline = GenerationPipeline()
        spec = _studio_spec()
        result = await pipeline.generate_from_spec(spec, generator="constraint_solver")

        assert result["floor_plan"] is not None
        assert len(result["floor_plan"].spec.rooms) == 3

    @pytest.mark.asyncio
    async def test_generate_from_spec_large(self):
        pipeline = GenerationPipeline()
        spec = _large_house_spec()
        result = await pipeline.generate_from_spec(spec, generator="constraint_solver")

        assert result["floor_plan"] is not None
        assert len(result["floor_plan"].spec.rooms) == 10
        assert result["floor_plan"].total_area_sqm > 0

    @pytest.mark.asyncio
    async def test_rooms_have_polygons_after_pipeline(self):
        pipeline = GenerationPipeline()
        spec = _bungalow_spec()
        result = await pipeline.generate_from_spec(spec, generator="constraint_solver")

        for room in result["floor_plan"].spec.rooms:
            assert room.polygon is not None
            assert len(room.polygon) >= 4
            assert room.actual_area_sqm is not None
            assert room.actual_area_sqm > 0

    @pytest.mark.asyncio
    async def test_pipeline_returns_timing(self):
        pipeline = GenerationPipeline()
        spec = _studio_spec()
        result = await pipeline.generate_from_spec(spec)

        assert "timing" in result
        assert "generation_s" in result["timing"]
        assert "geometry_cleanup_s" in result["timing"]

    @pytest.mark.asyncio
    async def test_pipeline_fallback_when_gsdiff_unavailable(self):
        pipeline = GenerationPipeline()
        spec = _studio_spec()
        # Request gsdiff but it should fall back to another available generator
        result = await pipeline.generate_from_spec(spec, generator="gsdiff")
        assert result["floor_plan"] is not None
        # GSDiff Modal is not deployed, so it should fallback to something else
        assert result["floor_plan"].generator_model != "gsdiff_modal_v1"
