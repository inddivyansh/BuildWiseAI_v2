"""Integration tests for the full compliance checker flow."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.schemas.floor_plan import (
    ComplianceResult,
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
from app.services.compliance.checker import ComplianceChecker, _deduplicate_violations, _sort_violations
from app.services.compliance.deterministic_rules import DeterministicRuleChecker
from app.services.compliance.zone_loader import ZoneDataLoader
from app.services.compliance.zone_spec_parser import ZoneSpec


def _make_compliant_plan() -> GeneratedFloorPlan:
    """Create a plan that passes all deterministic checks."""
    rooms = [
        Room(
            id="living_1", type=RoomType.LIVING, label="Living Room",
            target_area_sqm=20.0, actual_area_sqm=20.0,
            polygon=[
                Coordinate(x=2, y=5), Coordinate(x=7, y=5),
                Coordinate(x=7, y=9), Coordinate(x=2, y=9), Coordinate(x=2, y=5),
            ],
        ),
        Room(
            id="bedroom_1", type=RoomType.BEDROOM, label="Bedroom",
            target_area_sqm=12.0, actual_area_sqm=12.0,
            polygon=[
                Coordinate(x=8, y=5), Coordinate(x=12, y=5),
                Coordinate(x=12, y=8), Coordinate(x=8, y=8), Coordinate(x=8, y=5),
            ],
        ),
        Room(
            id="kitchen_1", type=RoomType.KITCHEN, label="Kitchen",
            target_area_sqm=8.0, actual_area_sqm=8.0,
            polygon=[
                Coordinate(x=2, y=10), Coordinate(x=6, y=10),
                Coordinate(x=6, y=12), Coordinate(x=2, y=12), Coordinate(x=2, y=10),
            ],
        ),
        Room(
            id="bath_1", type=RoomType.BATHROOM, label="Bathroom",
            target_area_sqm=4.0, actual_area_sqm=4.0,
            polygon=[
                Coordinate(x=7, y=10), Coordinate(x=9, y=10),
                Coordinate(x=9, y=12), Coordinate(x=7, y=12), Coordinate(x=7, y=10),
            ],
        ),
    ]
    spec = FloorPlanSpec(
        rooms=rooms,
        plot=PlotConstraints(
            width_m=20.0, depth_m=30.0,
            setbacks=Setbacks(front_m=4.5, rear_m=7.5, left_m=1.5, right_m=1.5),
        ),
    )
    ext_wall = WallSegment(
        id="ext1", start=Coordinate(x=2, y=5), end=Coordinate(x=12, y=5),
        is_exterior=True,
    )
    return GeneratedFloorPlan(
        spec=spec,
        walls=[ext_wall],
        doors=[DoorPlacement(id="d1", wall_id="w_int1", position_along_wall=0.5)],
        windows=[WindowPlacement(id="win1", wall_id="ext1", position_along_wall=0.5)],
        generation_id="test-integration",
        generator_model="test",
        generation_time_s=0,
        total_area_sqm=44.0,
        lot_coverage_ratio=44.0 / (20 * 30),
        area_deviation_pct=0,
    )


def _make_noncompliant_plan() -> GeneratedFloorPlan:
    """Create a plan with multiple violations."""
    rooms = [
        Room(
            id="bedroom_1", type=RoomType.BEDROOM, label="Tiny Bedroom",
            target_area_sqm=3.0, actual_area_sqm=3.0,  # below 4.2 min
            polygon=[
                Coordinate(x=0.5, y=1), Coordinate(x=2.5, y=1),  # violates left setback
                Coordinate(x=2.5, y=2.5), Coordinate(x=0.5, y=2.5),
                Coordinate(x=0.5, y=1),  # violates front setback
            ],
        ),
        Room(
            id="kitchen_1", type=RoomType.KITCHEN, label="Tiny Kitchen",
            target_area_sqm=2.0, actual_area_sqm=2.0,  # below 3.7 min
            polygon=[
                Coordinate(x=3, y=5), Coordinate(x=5, y=5),
                Coordinate(x=5, y=6), Coordinate(x=3, y=6), Coordinate(x=3, y=5),
            ],
        ),
    ]
    spec = FloorPlanSpec(
        rooms=rooms,
        plot=PlotConstraints(
            width_m=20.0, depth_m=30.0,
            setbacks=Setbacks(front_m=4.5, rear_m=7.5, left_m=1.5, right_m=1.5),
        ),
    )
    return GeneratedFloorPlan(
        spec=spec,
        walls=[],
        doors=[],  # no doors = egress violation
        windows=[],
        generation_id="test-noncompliant",
        generator_model="test",
        generation_time_s=0,
        total_area_sqm=5.0,
        lot_coverage_ratio=5.0 / (20 * 30),
        area_deviation_pct=0,
    )


class TestComplianceCheckerTier1Only:
    """Test checker with only Tier 1 (no graph, no LLM)."""

    @pytest.mark.asyncio
    async def test_compliant_plan(self):
        checker = ComplianceChecker()
        plan = _make_compliant_plan()
        result = await checker.check(plan, locale="CA-QC", run_llm=False)

        assert isinstance(result, ComplianceResult)
        assert result.jurisdiction == "CA-QC"
        assert result.checked_rules >= 8
        errors = [v for v in result.violations if v.severity == "error"]
        assert len(errors) == 0
        assert result.is_compliant is True

    @pytest.mark.asyncio
    async def test_noncompliant_plan(self):
        checker = ComplianceChecker()
        plan = _make_noncompliant_plan()
        result = await checker.check(plan, locale="CA-QC", run_llm=False)

        assert result.is_compliant is False
        assert len(result.violations) >= 2
        rule_ids = {v.rule_id for v in result.violations}
        assert "NBC_9.5_MIN_AREA" in rule_ids  # bedroom + kitchen too small
        assert "NBC_9.9.1.1_EGRESS" in rule_ids  # no doors

    @pytest.mark.asyncio
    async def test_violations_sorted_by_severity(self):
        checker = ComplianceChecker()
        plan = _make_noncompliant_plan()
        result = await checker.check(plan, run_llm=False)

        severities = [v.severity for v in result.violations]
        order = {"error": 0, "warning": 1, "info": 2}
        for i in range(len(severities) - 1):
            assert order.get(severities[i], 3) <= order.get(severities[i + 1], 3)


class TestDeduplication:
    def test_duplicates_removed(self):
        v1 = ComplianceViolation(
            rule_id="R1", severity="error", description="d1",
            affected_rooms=["a"], code_reference="ref",
        )
        v2 = ComplianceViolation(
            rule_id="R1", severity="error", description="d1 duplicate",
            affected_rooms=["a"], code_reference="ref",
        )
        result = _deduplicate_violations([v1, v2])
        assert len(result) == 1

    def test_different_rooms_kept(self):
        v1 = ComplianceViolation(
            rule_id="R1", severity="error", description="d1",
            affected_rooms=["a"], code_reference="ref",
        )
        v2 = ComplianceViolation(
            rule_id="R1", severity="error", description="d1",
            affected_rooms=["b"], code_reference="ref",
        )
        result = _deduplicate_violations([v1, v2])
        assert len(result) == 2


class TestSorting:
    def test_errors_first(self):
        violations = [
            ComplianceViolation(
                rule_id="R1", severity="info", description="info",
                affected_rooms=[], code_reference="",
            ),
            ComplianceViolation(
                rule_id="R2", severity="error", description="error",
                affected_rooms=[], code_reference="",
            ),
            ComplianceViolation(
                rule_id="R3", severity="warning", description="warning",
                affected_rooms=[], code_reference="",
            ),
        ]
        sorted_v = _sort_violations(violations)
        assert sorted_v[0].severity == "error"
        assert sorted_v[1].severity == "warning"
        assert sorted_v[2].severity == "info"


class TestZoneLoader:
    def test_empty_loader(self):
        loader = ZoneDataLoader()
        assert loader.zone_count == 0
        assert loader.get_zone_spec("31001Ma") is None
        assert loader.list_zones() == []

    def test_get_zone_by_point_empty(self):
        loader = ZoneDataLoader()
        assert loader.get_zone_by_point(-71.25, 46.77) is None

    def test_get_zone_setbacks_missing(self):
        loader = ZoneDataLoader()
        assert loader.get_zone_setbacks("nonexistent") == {}
