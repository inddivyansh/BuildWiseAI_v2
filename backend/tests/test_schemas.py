"""Tests for Pydantic schema validation."""

import pytest
from pydantic import ValidationError

from app.schemas.floor_plan import (
    AdjacencyRequirement,
    ComplianceResult,
    ComplianceViolation,
    Coordinate,
    DoorPlacement,
    FloorPlanSpec,
    GeneratedFloorPlan,
    PlotConstraints,
    Relationship,
    Room,
    RoomType,
    Setbacks,
    WallSegment,
    WindowPlacement,
)
from app.schemas.generation import (
    EditAction,
    EditRequest,
    EditResponse,
    ExportFormat,
    GenerateRequest,
    GenerateResponse,
    IngestRequest,
    ProjectCreate,
)


# --- Fixtures ---

def _make_room(**overrides) -> dict:
    defaults = {
        "id": "bedroom_1",
        "type": "bedroom",
        "label": "Bedroom 1",
        "target_area_sqm": 12.0,
    }
    defaults.update(overrides)
    return defaults


def _make_plot(**overrides) -> dict:
    defaults = {"width_m": 12.0, "depth_m": 20.0}
    defaults.update(overrides)
    return defaults


def _make_spec(**overrides) -> dict:
    defaults = {
        "rooms": [_make_room()],
        "plot": _make_plot(),
    }
    defaults.update(overrides)
    return defaults


# --- Room ---

class TestRoom:
    def test_valid_room(self):
        room = Room(**_make_room())
        assert room.type == RoomType.BEDROOM
        assert room.target_area_sqm == 12.0
        assert room.floor == 0
        assert room.polygon is None

    def test_room_with_polygon(self):
        room = Room(**_make_room(polygon=[
            {"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 3}, {"x": 0, "y": 3}, {"x": 0, "y": 0}
        ]))
        assert len(room.polygon) == 5

    def test_room_rejects_zero_area(self):
        with pytest.raises(ValidationError):
            Room(**_make_room(target_area_sqm=0))

    def test_room_rejects_negative_area(self):
        with pytest.raises(ValidationError):
            Room(**_make_room(target_area_sqm=-5))

    def test_room_all_types_valid(self):
        for rt in RoomType:
            room = Room(**_make_room(type=rt.value))
            assert room.type == rt


# --- PlotConstraints ---

class TestPlotConstraints:
    def test_computes_buildable_area(self):
        plot = PlotConstraints(**_make_plot())
        # 12 - 1.5 - 1.5 = 9m buildable width
        assert plot.buildable_width_m == 9.0
        # 20 - 4.5 - 7.5 = 8m buildable depth
        assert plot.buildable_depth_m == 8.0
        assert plot.buildable_area_sqm == 72.0
        assert plot.area_sqm == 240.0

    def test_custom_setbacks(self):
        plot = PlotConstraints(
            width_m=15.0, depth_m=25.0,
            setbacks=Setbacks(front_m=6.0, rear_m=6.0, left_m=2.0, right_m=2.0),
        )
        assert plot.buildable_width_m == 11.0
        assert plot.buildable_depth_m == 13.0

    def test_rejects_zero_dimensions(self):
        with pytest.raises(ValidationError):
            PlotConstraints(width_m=0, depth_m=20)

    def test_lot_coverage_bounds(self):
        with pytest.raises(ValidationError):
            PlotConstraints(width_m=10, depth_m=20, max_lot_coverage=1.5)
        with pytest.raises(ValidationError):
            PlotConstraints(width_m=10, depth_m=20, max_lot_coverage=0)


# --- FloorPlanSpec ---

class TestFloorPlanSpec:
    def test_valid_spec(self):
        spec = FloorPlanSpec(**_make_spec())
        assert len(spec.rooms) == 1
        assert spec.total_target_area == 12.0
        assert spec.locale == "CA-QC"

    def test_unique_room_ids_enforced(self):
        with pytest.raises(ValidationError, match="unique"):
            FloorPlanSpec(**_make_spec(rooms=[
                _make_room(id="room_1"),
                _make_room(id="room_1"),
            ]))

    def test_multiple_rooms(self):
        spec = FloorPlanSpec(**_make_spec(rooms=[
            _make_room(id="living_1", type="living", label="Living Room", target_area_sqm=25),
            _make_room(id="kitchen_1", type="kitchen", label="Kitchen", target_area_sqm=12),
            _make_room(id="bedroom_1", type="bedroom", label="Bedroom", target_area_sqm=14),
        ]))
        assert spec.total_target_area == 51.0
        assert spec.room_ids == {"living_1", "kitchen_1", "bedroom_1"}

    def test_adjacency_requirements(self):
        spec = FloorPlanSpec(**_make_spec(
            rooms=[
                _make_room(id="kitchen_1", type="kitchen", label="Kitchen", target_area_sqm=12),
                _make_room(id="dining_1", type="dining", label="Dining", target_area_sqm=14),
            ],
            adjacency=[
                {"room_a_id": "kitchen_1", "room_b_id": "dining_1", "relationship": "adjacent"}
            ],
        ))
        assert len(spec.adjacency) == 1
        assert spec.adjacency[0].relationship == Relationship.ADJACENT

    def test_multi_floor(self):
        spec = FloorPlanSpec(**_make_spec(num_floors=2))
        assert spec.num_floors == 2

    def test_rejects_too_many_floors(self):
        with pytest.raises(ValidationError):
            FloorPlanSpec(**_make_spec(num_floors=6))


# --- WallSegment, Door, Window ---

class TestBuildingElements:
    def test_wall_segment(self):
        wall = WallSegment(
            id="wall_0",
            start=Coordinate(x=0, y=0),
            end=Coordinate(x=5, y=0),
        )
        assert wall.thickness_m == 0.15
        assert not wall.is_exterior

    def test_door_placement(self):
        door = DoorPlacement(
            id="door_0", wall_id="wall_0", position_along_wall=0.5,
        )
        assert door.width_m == 0.9
        assert door.height_m == 2.1

    def test_window_placement(self):
        window = WindowPlacement(
            id="win_0", wall_id="wall_0", position_along_wall=0.5,
        )
        assert window.sill_height_m == 0.9

    def test_door_position_bounds(self):
        with pytest.raises(ValidationError):
            DoorPlacement(id="d", wall_id="w", position_along_wall=1.5)


# --- Compliance ---

class TestCompliance:
    def test_compliant_result(self):
        result = ComplianceResult(
            is_compliant=True, violations=[], checked_rules=42, jurisdiction="CA-QC"
        )
        assert result.is_compliant

    def test_violation(self):
        v = ComplianceViolation(
            rule_id="NBC_9.5.3.1",
            severity="error",
            description="Bedroom window area below minimum",
            affected_rooms=["bedroom_1"],
            code_reference="NBC 9.5.3.1",
            suggestion="Increase window size to 5% of floor area",
        )
        assert v.severity == "error"


# --- API Schemas ---

class TestAPISchemas:
    def test_generate_request_min_length(self):
        with pytest.raises(ValidationError):
            GenerateRequest(prompt="short")

    def test_generate_request_valid(self):
        req = GenerateRequest(prompt="3-bedroom bungalow on a 12x20m lot")
        assert req.locale == "CA-QC"
        assert req.run_compliance is True

    def test_edit_request(self):
        req = EditRequest(
            generation_id="abc-123",
            prompt="Make the master bedroom bigger",
        )
        assert req.generation_id == "abc-123"

    def test_ingest_request(self):
        req = IngestRequest(file_type="image")
        assert req.auto_detect_rooms is True

    def test_project_create(self):
        proj = ProjectCreate(name="My House")
        assert proj.description is None
