"""
Tests for DynamicSpatialValidator and spatial common sense enforcement.

Verifies:
1. Bathroom proportion limits in residential blueprints (e.g. 20.5m² bath in 72m² flat corrected).
2. Preservation of user intent when explicit room sizes are requested.
3. Multi-blueprint context detection and type-specific rules (commercial, office, etc.).
4. Constraint solver slot area protection (prevents swapping 20m² living room with 5m² bath).
5. Post-generation geometry validation and polygon healing.
"""

import pytest
from shapely.geometry import box

from app.schemas.floor_plan import (
    FloorPlanSpec,
    GeneratedFloorPlan,
    PlotConstraints,
    Room,
    RoomType,
    Setbacks,
    Coordinate,
)
from app.services.geometry.spatial_validator import (
    BlueprintType,
    DynamicSpatialValidator,
    detect_blueprint_type,
)
from app.services.generation.constraint_solver import solve_layout, ConstraintSolver
from app.core.pipeline import GenerationPipeline


def test_detect_blueprint_types():
    assert detect_blueprint_type("Modern 2 bedroom apartment 72m²") == BlueprintType.RESIDENTIAL
    assert detect_blueprint_type("Boutique retail shop with checkout counter and stockroom") == BlueprintType.COMMERCIAL
    assert detect_blueprint_type("Corporate coworking office with conference room and open workspace") == BlueprintType.OFFICE
    assert detect_blueprint_type("Logistics warehouse with 2 loading docks and high-bay storage") == BlueprintType.INDUSTRIAL
    assert detect_blueprint_type("Medical outpatient clinic with 3 exam rooms and waiting area") == BlueprintType.HEALTHCARE
    assert detect_blueprint_type("Elementary school with 4 classrooms and faculty office") == BlueprintType.EDUCATIONAL
    assert detect_blueprint_type("Campus master site plan with parking and perimeter setbacks") == BlueprintType.SITE_PLAN


def test_corrects_absurd_residential_bathroom_proportions():
    """Simulates the exact user problem: 20.5 m² bathroom and 4.8 m² living room in a 72 m² plan."""
    spec = FloorPlanSpec(
        rooms=[
            Room(id="bath_1", type=RoomType.BATHROOM, label="Central Bathroom", target_area_sqm=20.5),
            Room(id="living_1", type=RoomType.LIVING, label="Living Room", target_area_sqm=4.8),
            Room(id="bed_1", type=RoomType.MASTER_BEDROOM, label="Master Bedroom", target_area_sqm=11.1),
            Room(id="bed_2", type=RoomType.BEDROOM, label="Bedroom 2", target_area_sqm=5.7),
            Room(id="kitchen_1", type=RoomType.KITCHEN, label="Kitchen", target_area_sqm=5.8),
            Room(id="hall_1", type=RoomType.HALLWAY, label="Central Hallway", target_area_sqm=13.8),
            Room(id="foyer_1", type=RoomType.FOYER, label="Main Entrance", target_area_sqm=10.4),
        ],
        plot=PlotConstraints(width_m=12.0, depth_m=16.0),
        num_floors=1,
    )

    errors = []
    corrected = DynamicSpatialValidator.validate_and_correct_spec(
        spec, prompt="Top-down floor plan of 2 bedroom apartment (72m²)", errors=errors
    )

    # Bathroom must be scaled down to standard residential size
    bath = next(r for r in corrected.rooms if r.id == "bath_1")
    living = next(r for r in corrected.rooms if r.id == "living_1")

    assert bath.target_area_sqm <= 6.5, f"Bathroom was not clamped: {bath.target_area_sqm}"
    assert living.target_area_sqm >= 14.0, f"Living room was not expanded: {living.target_area_sqm}"
    assert living.target_area_sqm > bath.target_area_sqm, "Living room must be strictly larger than bathroom"
    assert any("disproportionate" in e.lower() or "rebalance" in e.lower() for e in errors)


def test_preserves_explicit_user_intent_for_luxury_bathroom():
    """If the user explicitly asked for a 15m² luxury spa bathroom, respect their explicit request."""
    spec = FloorPlanSpec(
        rooms=[
            Room(id="bath_1", type=RoomType.MASTER_BATHROOM, label="Luxury Spa Bathroom", target_area_sqm=15.0),
            Room(id="living_1", type=RoomType.LIVING, label="Living Room", target_area_sqm=35.0),
            Room(id="bed_1", type=RoomType.MASTER_BEDROOM, label="Master Bedroom", target_area_sqm=20.0),
        ],
        plot=PlotConstraints(width_m=15.0, depth_m=20.0),
        num_floors=1,
    )

    errors = []
    corrected = DynamicSpatialValidator.validate_and_correct_spec(
        spec, prompt="Large villa with a 15m² luxury spa bathroom and spacious living room", errors=errors
    )

    bath = next(r for r in corrected.rooms if r.id == "bath_1")
    # Should not be clamped because user explicitly requested a spa/luxury bath
    assert bath.target_area_sqm >= 14.0


def test_constraint_solver_area_fidelity():
    """Ensure constraint solver maintains area fidelity and doesn't swap disparate slots."""
    spec = FloorPlanSpec(
        rooms=[
            Room(id="living", type=RoomType.LIVING, label="Living Room", target_area_sqm=22.0),
            Room(id="kitchen", type=RoomType.KITCHEN, label="Kitchen", target_area_sqm=10.0),
            Room(id="bed", type=RoomType.BEDROOM, label="Bedroom", target_area_sqm=12.0),
            Room(id="bath", type=RoomType.BATHROOM, label="Bathroom", target_area_sqm=4.5),
        ],
        plot=PlotConstraints(width_m=12.0, depth_m=18.0),
    )

    polygons = solve_layout(spec)
    assert len(polygons) == 4

    living_poly = polygons[0]
    bath_poly = polygons[3]

    # Living room should be significantly larger than bathroom in actual geometry
    assert living_poly.area > bath_poly.area * 2.5
    assert bath_poly.area < 8.0, f"Bathroom geometry grew too large: {bath_poly.area}"


def test_geometry_validation_heals_inverted_assignment():
    """Tests that validate_generated_geometry detects and fixes inverted polygon assignments."""
    # Simulate an inverted assignment: living room got a tiny polygon, bathroom got a huge polygon
    tiny_poly = [Coordinate(x=0, y=0), Coordinate(x=2, y=0), Coordinate(x=2, y=2), Coordinate(x=0, y=2), Coordinate(x=0, y=0)]
    huge_poly = [Coordinate(x=2, y=0), Coordinate(x=7, y=0), Coordinate(x=7, y=4), Coordinate(x=2, y=4), Coordinate(x=2, y=0)]

    spec = FloorPlanSpec(
        rooms=[
            Room(id="living", type=RoomType.LIVING, label="Living Room", target_area_sqm=20.0, polygon=tiny_poly, actual_area_sqm=4.0),
            Room(id="bath", type=RoomType.BATHROOM, label="Bathroom", target_area_sqm=4.0, polygon=huge_poly, actual_area_sqm=20.0),
        ],
        plot=PlotConstraints(width_m=10.0, depth_m=10.0),
    )

    floor_plan = GeneratedFloorPlan(
        spec=spec,
        walls=[],
        doors=[],
        windows=[],
        generation_id="test_gen",
        generator_model="test",
        generation_time_s=0.1,
        total_area_sqm=24.0,
        lot_coverage_ratio=0.5,
        area_deviation_pct=50.0,
    )

    errors = []
    healed = DynamicSpatialValidator.validate_generated_geometry(floor_plan, errors)

    living = next(r for r in healed.spec.rooms if r.id == "living")
    bath = next(r for r in healed.spec.rooms if r.id == "bath")

    # Polygons should now be swapped to match functional targets!
    assert living.actual_area_sqm == 20.0
    assert bath.actual_area_sqm == 4.0
    assert any("healed" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_pipeline_e2e_rebalances_disproportionate_plan():
    """End-to-end pipeline test with an aberrant 20.5m² bath and 4.8m² living room spec.

    Verifies that the entire pipeline produces a floor plan where living room is
    properly sized and bathroom is strictly constrained.
    """
    pipeline = GenerationPipeline()
    spec = FloorPlanSpec(
        rooms=[
            Room(id="bath_1", type=RoomType.BATHROOM, label="Central Bathroom", target_area_sqm=20.5),
            Room(id="living_1", type=RoomType.LIVING, label="Living Room", target_area_sqm=4.8),
            Room(id="bed_1", type=RoomType.MASTER_BEDROOM, label="Master Bedroom", target_area_sqm=12.0),
            Room(id="bed_2", type=RoomType.BEDROOM, label="Bedroom 2", target_area_sqm=10.0),
            Room(id="kitchen_1", type=RoomType.KITCHEN, label="Kitchen", target_area_sqm=8.0),
        ],
        plot=PlotConstraints(width_m=14.0, depth_m=20.0),
        num_floors=1,
    )

    result = await pipeline.generate_from_spec(spec, generator="constraint_solver")
    assert result["floor_plan"] is not None
    fp = result["floor_plan"]

    bath = next(r for r in fp.spec.rooms if r.id == "bath_1")
    living = next(r for r in fp.spec.rooms if r.id == "living_1")

    # In final floor plan, living room must be larger than bathroom
    assert living.actual_area_sqm is not None and bath.actual_area_sqm is not None
    assert living.actual_area_sqm > bath.actual_area_sqm, (
        f"Living room ({living.actual_area_sqm}m²) must be larger than bathroom ({bath.actual_area_sqm}m²)"
    )
    assert bath.actual_area_sqm < 8.0, f"Bathroom ({bath.actual_area_sqm}m²) must not be oversized"


def test_commercial_blueprint_spatial_balance():
    """Verifies commercial retail spatial proportions."""
    spec = FloorPlanSpec(
        rooms=[
            Room(id="sales_1", type=RoomType.OTHER, label="Retail Sales Floor", target_area_sqm=80.0),
            Room(id="stock_1", type=RoomType.STORAGE, label="Stockroom", target_area_sqm=20.0),
            Room(id="rest_1", type=RoomType.BATHROOM, label="Customer Restroom", target_area_sqm=25.0),
        ],
        plot=PlotConstraints(width_m=15.0, depth_m=20.0),
    )

    errors = []
    corrected = DynamicSpatialValidator.validate_and_correct_spec(
        spec, prompt="Boutique retail shop with merchandise sales floor, stockroom, and restroom", errors=errors
    )

    rest = next(r for r in corrected.rooms if r.id == "rest_1")
    assert rest.target_area_sqm <= 10.0, f"Commercial restroom was not constrained: {rest.target_area_sqm}"

