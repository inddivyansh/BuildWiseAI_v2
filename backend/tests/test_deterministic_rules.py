"""Tests for Tier 1 deterministic compliance rules."""

import pytest

from app.schemas.floor_plan import (
    ComplianceViolation,
    Coordinate,
    DoorPlacement,
    FloorPlanSpec,
    GeneratedFloorPlan,
    PlotConstraints,
    Room,
    RoomType,
    Setbacks,
    WallSegment,
    WindowPlacement,
)
from app.services.compliance.deterministic_rules import DeterministicRuleChecker
from app.services.compliance.zone_spec_parser import ZoneSpec
from app.services.compliance.zone_loader import ZoneDataLoader


def _make_room(
    room_id: str,
    room_type: RoomType,
    area: float,
    polygon: list[tuple[float, float]] | None = None,
    floor: int = 0,
) -> Room:
    """Helper to create a room with optional polygon."""
    if polygon:
        coords = [Coordinate(x=x, y=y) for x, y in polygon]
    else:
        side = area**0.5
        coords = [
            Coordinate(x=2, y=2),
            Coordinate(x=2 + side, y=2),
            Coordinate(x=2 + side, y=2 + side),
            Coordinate(x=2, y=2 + side),
            Coordinate(x=2, y=2),
        ]
    return Room(
        id=room_id,
        type=room_type,
        label=room_id.replace("_", " ").title(),
        target_area_sqm=area,
        actual_area_sqm=area,
        polygon=coords,
        floor=floor,
    )


def _make_plan(
    rooms: list[Room],
    plot_width: float = 20.0,
    plot_depth: float = 30.0,
    setbacks: Setbacks | None = None,
    walls: list[WallSegment] | None = None,
    doors: list[DoorPlacement] | None = None,
    windows: list[WindowPlacement] | None = None,
) -> GeneratedFloorPlan:
    """Helper to create a GeneratedFloorPlan for testing."""
    sb = setbacks or Setbacks(front_m=4.5, rear_m=7.5, left_m=1.5, right_m=1.5)
    spec = FloorPlanSpec(
        rooms=rooms,
        plot=PlotConstraints(width_m=plot_width, depth_m=plot_depth, setbacks=sb),
    )
    total_area = sum(r.actual_area_sqm or 0 for r in rooms)
    buildable = spec.plot.buildable_area_sqm or 1
    return GeneratedFloorPlan(
        spec=spec,
        walls=walls if walls is not None else [],
        doors=doors if doors is not None else [DoorPlacement(id="d1", wall_id="w1", position_along_wall=0.5)],
        windows=windows if windows is not None else [],
        generation_id="test-gen",
        generator_model="test",
        generation_time_s=0,
        total_area_sqm=total_area,
        lot_coverage_ratio=total_area / buildable if buildable else 0,
        area_deviation_pct=0,
    )


@pytest.fixture
def checker():
    return DeterministicRuleChecker()


class TestRoomMinimums:
    def test_rooms_above_minimum_pass(self, checker):
        rooms = [
            _make_room("living_1", RoomType.LIVING, 15.0),
            _make_room("bedroom_1", RoomType.BEDROOM, 10.0),
            _make_room("kitchen_1", RoomType.KITCHEN, 8.0),
            _make_room("bathroom_1", RoomType.BATHROOM, 4.0),
        ]
        plan = _make_plan(rooms)
        violations = checker._check_room_minimums(plan)
        assert len(violations) == 0

    def test_bedroom_below_minimum_fails(self, checker):
        rooms = [
            _make_room("bedroom_1", RoomType.BEDROOM, 3.0),  # below 4.2
        ]
        plan = _make_plan(rooms)
        violations = checker._check_room_minimums(plan)
        assert len(violations) == 1
        assert violations[0].rule_id == "NBC_9.5_MIN_AREA"
        assert "bedroom_1" in violations[0].affected_rooms

    def test_kitchen_below_minimum_fails(self, checker):
        rooms = [
            _make_room("kitchen_1", RoomType.KITCHEN, 2.5),  # below 3.7
        ]
        plan = _make_plan(rooms)
        violations = checker._check_room_minimums(plan)
        assert len(violations) == 1

    def test_living_below_minimum_fails(self, checker):
        rooms = [
            _make_room("living_1", RoomType.LIVING, 4.0),  # below 6.0
        ]
        plan = _make_plan(rooms)
        violations = checker._check_room_minimums(plan)
        assert len(violations) == 1

    def test_bathroom_at_minimum_passes(self, checker):
        rooms = [
            _make_room("bath_1", RoomType.BATHROOM, 1.8),  # exactly minimum
        ]
        plan = _make_plan(rooms)
        violations = checker._check_room_minimums(plan)
        assert len(violations) == 0

    def test_storage_has_no_minimum(self, checker):
        rooms = [
            _make_room("storage_1", RoomType.STORAGE, 0.5),  # no minimum defined
        ]
        plan = _make_plan(rooms)
        violations = checker._check_room_minimums(plan)
        assert len(violations) == 0


class TestSetbacks:
    def test_rooms_within_setbacks_pass(self, checker):
        rooms = [
            _make_room(
                "living_1", RoomType.LIVING, 15.0,
                polygon=[(2, 5), (6, 5), (6, 10), (2, 10), (2, 5)],
            ),
        ]
        plan = _make_plan(rooms)
        violations = checker._check_setback_compliance(plan)
        assert len(violations) == 0

    def test_room_violates_front_setback(self, checker):
        rooms = [
            _make_room(
                "living_1", RoomType.LIVING, 15.0,
                polygon=[(2, 1), (6, 1), (6, 5), (2, 5), (2, 1)],  # y=1 < front=4.5
            ),
        ]
        plan = _make_plan(rooms)
        violations = checker._check_setback_compliance(plan)
        assert len(violations) >= 1
        assert "front setback" in violations[0].description

    def test_room_violates_left_setback(self, checker):
        rooms = [
            _make_room(
                "bed_1", RoomType.BEDROOM, 10.0,
                polygon=[(0.5, 5), (4, 5), (4, 8), (0.5, 8), (0.5, 5)],  # x=0.5 < left=1.5
            ),
        ]
        plan = _make_plan(rooms)
        violations = checker._check_setback_compliance(plan)
        assert len(violations) >= 1
        assert "left setback" in violations[0].description


class TestLotCoverage:
    def test_coverage_within_limit_passes(self, checker):
        rooms = [
            _make_room("living_1", RoomType.LIVING, 20.0),
        ]
        plan = _make_plan(rooms, plot_width=20, plot_depth=30)
        violations = checker._check_lot_coverage(plan)
        assert len(violations) == 0

    def test_coverage_exceeds_limit_fails(self, checker):
        # Create rooms that exceed 60% coverage
        rooms = [
            _make_room("living_1", RoomType.LIVING, 200.0),
            _make_room("bed_1", RoomType.BEDROOM, 200.0),
        ]
        plan = _make_plan(rooms, plot_width=20, plot_depth=30)
        # Total = 400sqm, lot = 600sqm, coverage = 66.7% > 60%
        plan.total_area_sqm = 400
        plan.lot_coverage_ratio = 400 / (20 * 30)
        violations = checker._check_lot_coverage(plan)
        assert len(violations) == 1
        assert violations[0].rule_id == "LOT_COVERAGE_EXCEEDED"


class TestEgress:
    def test_plan_with_doors_passes(self, checker):
        rooms = [_make_room("living_1", RoomType.LIVING, 15.0)]
        plan = _make_plan(rooms)
        violations = checker._check_egress(plan)
        assert len(violations) == 0

    def test_plan_without_doors_fails(self, checker):
        rooms = [_make_room("living_1", RoomType.LIVING, 15.0)]
        plan = _make_plan(rooms, doors=[])
        violations = checker._check_egress(plan)
        assert len(violations) >= 1
        assert violations[0].rule_id == "NBC_9.9.1.1_EGRESS"


class TestBuildingFootprint:
    def test_room_within_plot_passes(self, checker):
        rooms = [
            _make_room(
                "living_1", RoomType.LIVING, 15.0,
                polygon=[(2, 5), (6, 5), (6, 10), (2, 10), (2, 5)],
            ),
        ]
        plan = _make_plan(rooms)
        violations = checker._check_building_footprint(plan)
        assert len(violations) == 0

    def test_room_outside_plot_fails(self, checker):
        rooms = [
            _make_room(
                "living_1", RoomType.LIVING, 15.0,
                polygon=[(-1, 5), (3, 5), (3, 10), (-1, 10), (-1, 5)],
            ),
        ]
        plan = _make_plan(rooms)
        violations = checker._check_building_footprint(plan)
        assert len(violations) >= 1
        assert violations[0].rule_id == "BUILDING_OUTSIDE_PLOT"


class TestHallwayWidth:
    def test_wide_hallway_passes(self, checker):
        rooms = [
            _make_room(
                "hall_1", RoomType.HALLWAY, 3.0,
                polygon=[(2, 5), (5, 5), (5, 6), (2, 6), (2, 5)],  # 3m x 1m
            ),
        ]
        plan = _make_plan(rooms)
        violations = checker._check_hallway_width(plan)
        assert len(violations) == 0

    def test_narrow_hallway_warns(self, checker):
        rooms = [
            _make_room(
                "hall_1", RoomType.HALLWAY, 1.5,
                polygon=[(2, 5), (4, 5), (4, 5.5), (2, 5.5), (2, 5)],  # 2m x 0.5m
            ),
        ]
        plan = _make_plan(rooms)
        violations = checker._check_hallway_width(plan)
        assert len(violations) >= 1
        assert violations[0].severity == "warning"


class TestFullCheck:
    def test_compliant_plan_returns_no_errors(self, checker):
        rooms = [
            _make_room(
                "living_1", RoomType.LIVING, 20.0,
                polygon=[(2, 5), (7, 5), (7, 9), (2, 9), (2, 5)],
            ),
            _make_room(
                "bedroom_1", RoomType.BEDROOM, 12.0,
                polygon=[(8, 5), (12, 5), (12, 8), (8, 8), (8, 5)],
            ),
            _make_room(
                "kitchen_1", RoomType.KITCHEN, 8.0,
                polygon=[(2, 10), (6, 10), (6, 12), (2, 12), (2, 10)],
            ),
        ]
        plan = _make_plan(
            rooms,
            doors=[DoorPlacement(id="d1", wall_id="w1", position_along_wall=0.5)],
            windows=[WindowPlacement(id="w1", wall_id="ext1", position_along_wall=0.5)],
            walls=[WallSegment(id="ext1", start=Coordinate(x=2, y=5), end=Coordinate(x=12, y=5), is_exterior=True)],
        )
        violations = checker.check(plan)
        error_violations = [v for v in violations if v.severity == "error"]
        assert len(error_violations) == 0

    def test_multiple_violations_returned(self, checker):
        rooms = [
            _make_room("bedroom_1", RoomType.BEDROOM, 2.0),  # below min
            _make_room("kitchen_1", RoomType.KITCHEN, 2.0),  # below min
        ]
        plan = _make_plan(rooms, doors=[])  # no doors = egress violation
        violations = checker.check(plan)
        rule_ids = {v.rule_id for v in violations}
        assert "NBC_9.5_MIN_AREA" in rule_ids
        assert "NBC_9.9.1.1_EGRESS" in rule_ids


class _FakeZoneLoader:
    """Minimal stand-in for ZoneDataLoader used in zone-aware tests."""

    def __init__(self, specs: dict[str, ZoneSpec]):
        self._specs = specs

    def get_zone_spec(self, zone_id: str) -> ZoneSpec | None:
        return self._specs.get(zone_id)


class TestZoneSetbackOverride:
    """Zone margins override spec.plot.setbacks."""

    def test_zone_setback_override_catches_violation(self, checker):
        """Room within spec setbacks but violates stricter zone setbacks."""
        rooms = [
            _make_room(
                "living_1", RoomType.LIVING, 15.0,
                # x=2 is within default left=1.5 but outside zone side=3.0
                polygon=[(2, 5), (6, 5), (6, 10), (2, 10), (2, 5)],
            ),
        ]
        plan = _make_plan(rooms)

        # No zone → passes with default left=1.5
        violations = checker._check_setback_compliance(plan)
        assert len(violations) == 0

        # Zone says side margin = 3.0 → x=2 < 3.0 violates
        zone_spec = ZoneSpec(zone_id="test", side_margin_m=3.0)
        violations = checker._check_setback_compliance(plan, zone_spec)
        assert len(violations) >= 1
        assert "left setback" in violations[0].description

    def test_zone_setback_passed_through_check(self, checker):
        """check() with zone_id passes zone data to setback check."""
        rooms = [
            _make_room(
                "living_1", RoomType.LIVING, 15.0,
                polygon=[(2, 5), (6, 5), (6, 10), (2, 10), (2, 5)],
            ),
        ]
        plan = _make_plan(
            rooms,
            doors=[DoorPlacement(id="d1", wall_id="w1", position_along_wall=0.5)],
            windows=[WindowPlacement(id="w1", wall_id="ext1", position_along_wall=0.5)],
            walls=[WallSegment(id="ext1", start=Coordinate(x=2, y=5), end=Coordinate(x=6, y=5), is_exterior=True)],
        )

        zone_spec = ZoneSpec(zone_id="ZN1", side_margin_m=3.0)
        loader = _FakeZoneLoader({"ZN1": zone_spec})
        violations = checker.check(plan, zone_id="ZN1", zone_loader=loader)
        setback_violations = [v for v in violations if v.rule_id == "SETBACK_VIOLATION"]
        assert len(setback_violations) >= 1

    def test_no_zone_defaults_unchanged(self, checker):
        """Without zone_id, check() behaves identically to before."""
        rooms = [
            _make_room(
                "living_1", RoomType.LIVING, 15.0,
                polygon=[(2, 5), (6, 5), (6, 10), (2, 10), (2, 5)],
            ),
        ]
        plan = _make_plan(
            rooms,
            doors=[DoorPlacement(id="d1", wall_id="w1", position_along_wall=0.5)],
            windows=[WindowPlacement(id="w1", wall_id="ext1", position_along_wall=0.5)],
            walls=[WallSegment(id="ext1", start=Coordinate(x=2, y=5), end=Coordinate(x=6, y=5), is_exterior=True)],
        )

        v_no_zone = checker.check(plan)
        v_none_zone = checker.check(plan, zone_id=None, zone_loader=None)
        assert len(v_no_zone) == len(v_none_zone)


class TestZoneHeight:
    def test_height_within_limit(self, checker):
        rooms = [_make_room("living_1", RoomType.LIVING, 15.0)]
        plan = _make_plan(rooms)
        # default_floor_height = 2.7, 1 floor = 2.7m
        zone_spec = ZoneSpec(zone_id="H1", max_height_m=11.0)
        violations = checker._check_zone_height(plan, zone_spec)
        assert len(violations) == 0

    def test_height_exceeded(self, checker):
        rooms = [_make_room("living_1", RoomType.LIVING, 15.0)]
        plan = _make_plan(rooms)
        plan.spec.num_floors = 5  # 5 × 2.7 = 13.5m
        zone_spec = ZoneSpec(zone_id="H1", max_height_m=11.0)
        violations = checker._check_zone_height(plan, zone_spec)
        assert len(violations) == 1
        assert violations[0].rule_id == "ZONE_MAX_HEIGHT"

    def test_no_height_limit(self, checker):
        rooms = [_make_room("living_1", RoomType.LIVING, 15.0)]
        plan = _make_plan(rooms)
        plan.spec.num_floors = 10
        zone_spec = ZoneSpec(zone_id="H1", max_height_m=None)
        violations = checker._check_zone_height(plan, zone_spec)
        assert len(violations) == 0


class TestZoneGreenArea:
    def test_green_area_sufficient(self, checker):
        rooms = [_make_room("living_1", RoomType.LIVING, 20.0)]
        plan = _make_plan(rooms, plot_width=20, plot_depth=30)
        plan.total_area_sqm = 20.0
        # Lot = 600sqm, building = 20sqm → green = 96.7%
        zone_spec = ZoneSpec(zone_id="H1", min_green_area_pct=30.0)
        violations = checker._check_zone_green_area(plan, zone_spec)
        assert len(violations) == 0

    def test_green_area_insufficient(self, checker):
        rooms = [
            _make_room("living_1", RoomType.LIVING, 200.0),
            _make_room("bed_1", RoomType.BEDROOM, 200.0),
        ]
        plan = _make_plan(rooms, plot_width=20, plot_depth=30)
        plan.total_area_sqm = 500.0
        # Lot = 600sqm, building = 500sqm → green = 16.7% < 30%
        zone_spec = ZoneSpec(zone_id="H1", min_green_area_pct=30.0)
        violations = checker._check_zone_green_area(plan, zone_spec)
        assert len(violations) == 1
        assert violations[0].rule_id == "ZONE_MIN_GREEN_AREA"

    def test_no_green_requirement(self, checker):
        rooms = [_make_room("living_1", RoomType.LIVING, 200.0)]
        plan = _make_plan(rooms)
        plan.total_area_sqm = 500.0
        zone_spec = ZoneSpec(zone_id="H1", min_green_area_pct=None)
        violations = checker._check_zone_green_area(plan, zone_spec)
        assert len(violations) == 0
