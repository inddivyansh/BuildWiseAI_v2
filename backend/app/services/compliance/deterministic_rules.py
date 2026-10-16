"""Tier 1: Deterministic geometric and dimensional rules from NBC Part 9."""

from __future__ import annotations

import structlog

from app.schemas.floor_plan import (
    ComplianceViolation,
    GeneratedFloorPlan,
    RoomType,
)
from app.services.compliance.zone_spec_parser import ZoneSpec

logger = structlog.get_logger()

# -------------------------------------------------------------------
# NBC Part 9 minimum room dimensions (all in sqm / meters)
# -------------------------------------------------------------------

# Minimum area per room type (NBC 9.5.x)
MIN_AREAS: dict[str, float] = {
    RoomType.LIVING.value: 6.0,          # NBC 9.5.1.1
    RoomType.KITCHEN.value: 3.7,         # NBC 9.5.8.1
    RoomType.DINING.value: 5.0,          # NBC 9.5.1.1 (habitable)
    RoomType.BEDROOM.value: 4.2,         # NBC 9.5.7.1
    RoomType.MASTER_BEDROOM.value: 4.2,  # NBC 9.5.7.1
    RoomType.BATHROOM.value: 1.8,        # NBC 9.5.9.1
    RoomType.HALF_BATH.value: 1.2,       # practical minimum
    RoomType.MASTER_BATHROOM.value: 1.8, # NBC 9.5.9.1
    RoomType.OFFICE.value: 4.0,          # habitable room
    RoomType.HALLWAY.value: 0.5,         # corridor
    RoomType.FOYER.value: 1.5,           # entry
    RoomType.LAUNDRY.value: 1.5,         # utility
    RoomType.GARAGE.value: 12.0,         # single car
}

# Minimum ceiling height for habitable rooms (NBC 9.5.3.1)
MIN_CEILING_HEIGHT_M = 2.1

# Minimum hallway / corridor width (NBC 9.5.4.1)
MIN_HALLWAY_WIDTH_M = 0.86

# Minimum stair width (NBC 9.8.2.1)
MIN_STAIR_WIDTH_M = 0.86

# Minimum window area as fraction of floor area for light (NBC 9.7.2.3)
MIN_WINDOW_AREA_FRACTION = 0.05

# Maximum lot coverage (default; overridden by zone spec)
DEFAULT_MAX_LOT_COVERAGE = 0.60

# Habitable room types (require natural light, min area)
_HABITABLE = {
    RoomType.LIVING.value, RoomType.KITCHEN.value, RoomType.DINING.value,
    RoomType.BEDROOM.value, RoomType.MASTER_BEDROOM.value, RoomType.OFFICE.value,
}


class DeterministicRuleChecker:
    """Tier 1: Hard-coded geometric and dimensional rules.

    These are pure geometry checks — no external database needed.
    Runs in <1ms for any floor plan.
    """

    def check(
        self,
        plan: GeneratedFloorPlan,
        zone_id: str | None = None,
        zone_loader: "ZoneDataLoader | None" = None,
    ) -> list[ComplianceViolation]:
        # Resolve zone spec if both zone_id and loader are provided
        zone_spec: ZoneSpec | None = None
        if zone_id and zone_loader:
            zone_spec = zone_loader.get_zone_spec(zone_id)

        violations: list[ComplianceViolation] = []
        violations.extend(self._check_room_minimums(plan))
        violations.extend(self._check_setback_compliance(plan, zone_spec))
        violations.extend(self._check_lot_coverage(plan))
        violations.extend(self._check_ceiling_height(plan))
        violations.extend(self._check_egress(plan))
        violations.extend(self._check_building_footprint(plan))
        violations.extend(self._check_hallway_width(plan))
        violations.extend(self._check_window_requirements(plan))
        if zone_spec:
            violations.extend(self._check_zone_height(plan, zone_spec))
            violations.extend(self._check_zone_green_area(plan, zone_spec))
        return violations

    def _check_room_minimums(self, plan: GeneratedFloorPlan) -> list[ComplianceViolation]:
        """Check each room meets minimum area for its type."""
        violations = []
        for room in plan.spec.rooms:
            area = room.actual_area_sqm or room.target_area_sqm
            min_area = MIN_AREAS.get(room.type.value)
            if min_area and area < min_area:
                violations.append(ComplianceViolation(
                    rule_id="NBC_9.5_MIN_AREA",
                    severity="error",
                    description=(
                        f"{room.label} ({room.type.value}) area is {area:.1f} sqm, "
                        f"below minimum {min_area:.1f} sqm"
                    ),
                    affected_rooms=[room.id],
                    code_reference=_room_type_code_ref(room.type.value),
                    suggestion=f"Increase {room.label} to at least {min_area:.1f} sqm",
                ))
        return violations

    def _check_setback_compliance(
        self, plan: GeneratedFloorPlan, zone_spec: ZoneSpec | None = None,
    ) -> list[ComplianceViolation]:
        """Check that rooms respect plot setbacks.

        If a zone_spec is provided, its margins override spec.plot.setbacks
        for front, rear, and side (left+right) setbacks.
        """
        violations = []
        spec = plan.spec
        sb = spec.plot.setbacks

        # Zone margins override spec setbacks when available
        if zone_spec:
            if zone_spec.front_margin_m is not None:
                sb = sb.model_copy(update={"front_m": zone_spec.front_margin_m})
            if zone_spec.rear_margin_m is not None:
                sb = sb.model_copy(update={"rear_m": zone_spec.rear_margin_m})
            if zone_spec.side_margin_m is not None:
                sb = sb.model_copy(update={
                    "left_m": zone_spec.side_margin_m,
                    "right_m": zone_spec.side_margin_m,
                })

        for room in spec.rooms:
            if not room.polygon:
                continue
            for coord in room.polygon:
                issues = []
                if coord.x < sb.left_m - 0.01:
                    issues.append(f"left setback ({sb.left_m}m)")
                if coord.x > spec.plot.width_m - sb.right_m + 0.01:
                    issues.append(f"right setback ({sb.right_m}m)")
                if coord.y < sb.front_m - 0.01:
                    issues.append(f"front setback ({sb.front_m}m)")
                if coord.y > spec.plot.depth_m - sb.rear_m + 0.01:
                    issues.append(f"rear setback ({sb.rear_m}m)")

                if issues:
                    violations.append(ComplianceViolation(
                        rule_id="SETBACK_VIOLATION",
                        severity="error",
                        description=(
                            f"{room.label} extends beyond {', '.join(issues)} "
                            f"at ({coord.x:.2f}, {coord.y:.2f})"
                        ),
                        affected_rooms=[room.id],
                        code_reference="Municipal zoning setbacks",
                        suggestion="Move room within buildable envelope",
                    ))
                    break  # One violation per room is enough

        return violations

    def _check_lot_coverage(self, plan: GeneratedFloorPlan) -> list[ComplianceViolation]:
        """Check total footprint doesn't exceed max lot coverage."""
        spec = plan.spec
        lot_area = spec.plot.area_sqm or (spec.plot.width_m * spec.plot.depth_m)
        max_coverage = spec.plot.max_lot_coverage or DEFAULT_MAX_LOT_COVERAGE

        if lot_area <= 0:
            return []

        coverage = plan.lot_coverage_ratio
        if coverage > max_coverage:
            return [ComplianceViolation(
                rule_id="LOT_COVERAGE_EXCEEDED",
                severity="error",
                description=(
                    f"Lot coverage {coverage:.1%} exceeds maximum {max_coverage:.1%} "
                    f"({plan.total_area_sqm:.1f} sqm on {lot_area:.1f} sqm lot)"
                ),
                affected_rooms=[r.id for r in spec.rooms],
                code_reference="Municipal zoning - lot coverage",
                suggestion="Reduce building footprint or add floors",
            )]
        return []

    def _check_ceiling_height(self, plan: GeneratedFloorPlan) -> list[ComplianceViolation]:
        """Check ceiling height for habitable rooms.

        Note: Our 2D floor plans don't store ceiling height directly,
        but we check the spec/config default.
        """
        from app.config import settings
        floor_height = settings.default_floor_height_m

        if floor_height < MIN_CEILING_HEIGHT_M:
            habitable_rooms = [
                r.id for r in plan.spec.rooms
                if r.type.value in _HABITABLE
            ]
            if habitable_rooms:
                return [ComplianceViolation(
                    rule_id="NBC_9.5.3.1_CEILING_HEIGHT",
                    severity="error",
                    description=(
                        f"Floor height {floor_height}m is below minimum "
                        f"{MIN_CEILING_HEIGHT_M}m for habitable rooms"
                    ),
                    affected_rooms=habitable_rooms,
                    code_reference="NBC 9.5.3.1",
                    suggestion=f"Increase floor height to at least {MIN_CEILING_HEIGHT_M}m",
                )]
        return []

    def _check_egress(self, plan: GeneratedFloorPlan) -> list[ComplianceViolation]:
        """Check at least 1 exit exists per floor (NBC 9.9.1.1)."""
        violations = []
        # Group rooms by floor
        floors: dict[int, list[str]] = {}
        for room in plan.spec.rooms:
            floors.setdefault(room.floor, []).append(room.id)

        # Check we have at least one door (proxy for exit)
        if not plan.doors:
            for floor, room_ids in floors.items():
                violations.append(ComplianceViolation(
                    rule_id="NBC_9.9.1.1_EGRESS",
                    severity="error",
                    description=f"Floor {floor} has no doors (exits required)",
                    affected_rooms=room_ids,
                    code_reference="NBC 9.9.1.1",
                    suggestion="Add at least one exit door per floor",
                ))
        return violations

    def _check_building_footprint(self, plan: GeneratedFloorPlan) -> list[ComplianceViolation]:
        """Check building fits within plot."""
        spec = plan.spec
        violations = []

        for room in spec.rooms:
            if not room.polygon:
                continue
            for coord in room.polygon:
                if coord.x < -0.01 or coord.y < -0.01:
                    violations.append(ComplianceViolation(
                        rule_id="BUILDING_OUTSIDE_PLOT",
                        severity="error",
                        description=f"{room.label} extends outside plot boundary",
                        affected_rooms=[room.id],
                        code_reference="Plot boundary",
                        suggestion="Move room within plot",
                    ))
                    break
                if coord.x > spec.plot.width_m + 0.01 or coord.y > spec.plot.depth_m + 0.01:
                    violations.append(ComplianceViolation(
                        rule_id="BUILDING_OUTSIDE_PLOT",
                        severity="error",
                        description=f"{room.label} extends outside plot boundary",
                        affected_rooms=[room.id],
                        code_reference="Plot boundary",
                        suggestion="Move room within plot",
                    ))
                    break

        return violations

    def _check_hallway_width(self, plan: GeneratedFloorPlan) -> list[ComplianceViolation]:
        """Check hallway/corridor minimum width."""
        violations = []
        corridor_types = {RoomType.HALLWAY.value, RoomType.FOYER.value}

        for room in plan.spec.rooms:
            if room.type.value not in corridor_types:
                continue
            if not room.polygon or len(room.polygon) < 4:
                continue

            # Estimate min width from polygon bounding box
            xs = [c.x for c in room.polygon]
            ys = [c.y for c in room.polygon]
            width = max(xs) - min(xs)
            depth = max(ys) - min(ys)
            min_dim = min(width, depth)

            if min_dim < MIN_HALLWAY_WIDTH_M:
                violations.append(ComplianceViolation(
                    rule_id="NBC_9.5.4.1_HALLWAY_WIDTH",
                    severity="warning",
                    description=(
                        f"{room.label} minimum dimension {min_dim:.2f}m "
                        f"is below {MIN_HALLWAY_WIDTH_M}m"
                    ),
                    affected_rooms=[room.id],
                    code_reference="NBC 9.5.4.1",
                    suggestion=f"Widen {room.label} to at least {MIN_HALLWAY_WIDTH_M}m",
                ))
        return violations

    def _check_window_requirements(self, plan: GeneratedFloorPlan) -> list[ComplianceViolation]:
        """Check habitable rooms have adequate window provisions."""
        violations = []
        # Count exterior walls with windows
        window_wall_ids = {w.wall_id for w in plan.windows}
        exterior_walls = {w.id for w in plan.walls if w.is_exterior}
        windowed_exterior = window_wall_ids & exterior_walls

        for room in plan.spec.rooms:
            if room.type.value not in _HABITABLE:
                continue
            if not room.requires_window:
                continue

            # Simple check: habitable rooms should have at least one exterior wall nearby
            # In a full implementation, we'd check which walls bound this room
            # For now, just check the plan has enough windows overall
            area = room.actual_area_sqm or room.target_area_sqm
            min_window_area = area * MIN_WINDOW_AREA_FRACTION

            if not plan.windows:
                violations.append(ComplianceViolation(
                    rule_id="NBC_9.7.2.3_NATURAL_LIGHT",
                    severity="warning",
                    description=(
                        f"{room.label} ({area:.1f} sqm) requires at least "
                        f"{min_window_area:.2f} sqm of window area for natural light"
                    ),
                    affected_rooms=[room.id],
                    code_reference="NBC 9.7.2.3",
                    suggestion="Add windows to exterior walls adjacent to this room",
                ))
                break  # One warning is enough

        return violations


    def _check_zone_height(
        self, plan: GeneratedFloorPlan, zone_spec: ZoneSpec,
    ) -> list[ComplianceViolation]:
        """Check building height against zone maximum."""
        if zone_spec.max_height_m is None:
            return []

        from app.config import settings
        num_floors = plan.spec.num_floors or 1
        building_height = num_floors * settings.default_floor_height_m

        if building_height > zone_spec.max_height_m:
            return [ComplianceViolation(
                rule_id="ZONE_MAX_HEIGHT",
                severity="error",
                description=(
                    f"Building height {building_height:.1f}m ({num_floors} floors × "
                    f"{settings.default_floor_height_m}m) exceeds zone maximum "
                    f"{zone_spec.max_height_m:.1f}m"
                ),
                affected_rooms=[r.id for r in plan.spec.rooms],
                code_reference=f"Zone {zone_spec.zone_id} - hauteur maximale",
                suggestion=f"Reduce to {int(zone_spec.max_height_m / settings.default_floor_height_m)} floors or less",
            )]
        return []

    def _check_zone_green_area(
        self, plan: GeneratedFloorPlan, zone_spec: ZoneSpec,
    ) -> list[ComplianceViolation]:
        """Check lot green area percentage against zone minimum."""
        if zone_spec.min_green_area_pct is None:
            return []

        lot_area = plan.spec.plot.area_sqm or (
            plan.spec.plot.width_m * plan.spec.plot.depth_m
        )
        if lot_area <= 0:
            return []

        # Green area = lot area minus building footprint
        building_area = plan.total_area_sqm or 0
        green_pct = ((lot_area - building_area) / lot_area) * 100

        if green_pct < zone_spec.min_green_area_pct:
            return [ComplianceViolation(
                rule_id="ZONE_MIN_GREEN_AREA",
                severity="warning",
                description=(
                    f"Green area {green_pct:.1f}% is below zone minimum "
                    f"{zone_spec.min_green_area_pct:.1f}% "
                    f"({lot_area - building_area:.1f} sqm green on {lot_area:.1f} sqm lot)"
                ),
                affected_rooms=[r.id for r in plan.spec.rooms],
                code_reference=f"Zone {zone_spec.zone_id} - aire verte minimale",
                suggestion="Reduce building footprint to meet green area requirement",
            )]
        return []


def _room_type_code_ref(room_type: str) -> str:
    """Get the specific NBC code reference for a room type's minimum area."""
    refs = {
        "living": "NBC 9.5.1.1",
        "kitchen": "NBC 9.5.8.1",
        "bedroom": "NBC 9.5.7.1",
        "master_bedroom": "NBC 9.5.7.1",
        "bathroom": "NBC 9.5.9.1",
        "master_bathroom": "NBC 9.5.9.1",
        "half_bath": "NBC 9.5.9.1",
        "dining": "NBC 9.5.1.1",
        "office": "NBC 9.5.1.1",
    }
    return refs.get(room_type, "NBC Part 9.5")
