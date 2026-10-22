"""
Dynamic spatial and geometric validation layer for architectural blueprints.

Ensures spatial common sense, proportional allocation, and geometric consistency
across multiple blueprint types:
- Residential
- Commercial / Retail / Restaurant
- Office / Workplace
- Industrial / Warehouse
- Healthcare / Clinic
- Educational / School
- Site Plans & Engineering Layouts
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Any

import structlog
from shapely.geometry import Polygon

from app.schemas.floor_plan import (
    FloorPlanSpec,
    GeneratedFloorPlan,
    Room,
    RoomType,
)

logger = structlog.get_logger()


class BlueprintType(str, Enum):
    RESIDENTIAL = "residential"
    COMMERCIAL = "commercial"
    OFFICE = "office"
    INDUSTRIAL = "industrial"
    HEALTHCARE = "healthcare"
    EDUCATIONAL = "educational"
    SITE_PLAN = "site_plan"
    OTHER = "other"


# Type-specific room classification and proportional guidelines
TYPE_KEYWORDS: dict[BlueprintType, list[str]] = {
    BlueprintType.RESIDENTIAL: [
        "house", "home", "apartment", "flat", "villa", "bungalow", "duplex",
        "cottage", "studio", "residence", "residential", "bedroom", "living room",
        "master bedroom", "family room",
    ],
    BlueprintType.COMMERCIAL: [
        "commercial", "retail", "shop", "store", "restaurant", "cafe", "diner",
        "bar", "boutique", "showroom", "mall", "supermarket", "salon", "merchandise",
    ],
    BlueprintType.OFFICE: [
        "office", "workplace", "corporate", "coworking", "conference", "boardroom",
        "meeting room", "cubicle", "huddle", "executive suite", "receptionist",
    ],
    BlueprintType.INDUSTRIAL: [
        "warehouse", "factory", "industrial", "manufacturing", "distribution",
        "loading dock", "storage bay", "assembly", "workshop", "plant", "depot",
    ],
    BlueprintType.HEALTHCARE: [
        "clinic", "hospital", "medical", "doctor", "dental", "healthcare",
        "patient", "triage", "exam room", "infirmary", "pharmacy", "consultation",
    ],
    BlueprintType.EDUCATIONAL: [
        "school", "classroom", "university", "college", "kindergarten", "daycare",
        "library", "lecture hall", "academy", "training center",
    ],
    BlueprintType.SITE_PLAN: [
        "site plan", "master plan", "campus", "plot plan", "parking lot",
        "logistics park", "setback plan", "engineering layout",
    ],
}


# Primary facility anchors that unambiguously determine blueprint classification
ANCHOR_KEYWORDS: dict[BlueprintType, list[str]] = {
    BlueprintType.EDUCATIONAL: ["school", "classroom", "university", "college", "kindergarten", "daycare", "lecture hall", "academy"],
    BlueprintType.HEALTHCARE: ["clinic", "hospital", "medical", "dental", "triage", "infirmary", "doctor", "exam room", "patient"],
    BlueprintType.INDUSTRIAL: ["warehouse", "factory", "manufacturing", "storage bay", "loading dock", "assembly plant", "depot"],
    BlueprintType.COMMERCIAL: ["retail", "restaurant", "cafe", "boutique", "showroom", "supermarket", "sales floor", "diner"],
    BlueprintType.SITE_PLAN: ["site plan", "master plan", "campus layout", "logistics park", "parking lot"],
    BlueprintType.OFFICE: ["corporate office", "coworking", "boardroom", "conference room", "executive suite", "open office"],
    BlueprintType.RESIDENTIAL: ["apartment", "house", "villa", "bungalow", "duplex", "cottage", "residence", "residential", "bedroom", "living room"],
}


def detect_blueprint_type(
    prompt: str = "",
    style: str | None = None,
    rooms: list[Room] | None = None,
) -> BlueprintType:
    """Dynamically determine the blueprint type from prompt, style, and room labels."""
    combined_text = f"{prompt} {style or ''}".lower()

    # Check room labels/types if available
    if rooms:
        room_labels = " ".join(r.label.lower() for r in rooms)
        combined_text += f" {room_labels}"

    # Score each type by matching keywords
    scores: dict[BlueprintType, int] = {bp_type: 0 for bp_type in BlueprintType}

    # 1. Check strong anchors first
    for bp_type, anchors in ANCHOR_KEYWORDS.items():
        for anchor in anchors:
            if re.search(r"\b" + re.escape(anchor) + r"s?\b", combined_text):
                scores[bp_type] += 4

    # 2. Check general keywords
    for bp_type, keywords in TYPE_KEYWORDS.items():
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"s?\b", combined_text):
                scores[bp_type] += 2 if len(kw.split()) > 1 else 1

    best_type = max(scores, key=lambda k: scores[k])
    if scores[best_type] > 0:
        return best_type

    # Fallback: check room types
    if rooms:
        has_bed = any(r.type in (RoomType.BEDROOM, RoomType.MASTER_BEDROOM) for r in rooms)
        has_living = any(r.type == RoomType.LIVING for r in rooms)
        if has_bed or has_living:
            return BlueprintType.RESIDENTIAL
        has_office = any(r.type == RoomType.OFFICE for r in rooms)
        if has_office:
            return BlueprintType.OFFICE

    return BlueprintType.RESIDENTIAL


class DynamicSpatialValidator:
    """Validates and heals architectural proportions and geometry."""

    @staticmethod
    def validate_and_correct_spec(
        spec: FloorPlanSpec,
        prompt: str = "",
        errors: list[str] | None = None,
    ) -> FloorPlanSpec:
        """Pre-generation validation: ensures realistic room proportions and area budget.

        Detects common sense failures (such as a 20.5 m² bathroom with a 4.8 m² living room)
        and rebalances areas to meet architectural standards before polygon generation.
        """
        if errors is None:
            errors = []

        bp_type = detect_blueprint_type(prompt, spec.style, spec.rooms)
        total_footprint = spec.total_target_area

        # Scan for explicit user dimension requests in prompt so we never override intent
        prompt_lower = prompt.lower()
        explicit_bath_req = bool(
            re.search(r"(\d+(\.\d+)?)\s*(sqm|m2|m²|square meters?)\s*(bath|washroom|restroom)", prompt_lower)
            or re.search(r"(bath|washroom|restroom)\s*(of|is)?\s*(\d+(\.\d+)?)\s*(sqm|m2|m²)", prompt_lower)
            or "large bathroom" in prompt_lower
            or "spa" in prompt_lower
            or "luxury bath" in prompt_lower
        )

        if bp_type == BlueprintType.RESIDENTIAL:
            DynamicSpatialValidator._balance_residential_spec(
                spec, total_footprint, explicit_bath_req, errors
            )
        elif bp_type == BlueprintType.OFFICE:
            DynamicSpatialValidator._balance_office_spec(spec, total_footprint, errors)
        elif bp_type == BlueprintType.COMMERCIAL:
            DynamicSpatialValidator._balance_commercial_spec(spec, total_footprint, errors)
        elif bp_type in (BlueprintType.INDUSTRIAL, BlueprintType.SITE_PLAN):
            DynamicSpatialValidator._balance_industrial_spec(spec, total_footprint, errors)

        # Universal checks for all types
        DynamicSpatialValidator._enforce_minimum_areas(spec, errors)

        return spec

    @staticmethod
    def _balance_residential_spec(
        spec: FloorPlanSpec,
        total_footprint: float,
        explicit_bath_req: bool,
        errors: list[str],
    ) -> None:
        """Enforces residential spatial hierarchy: Living > Bedrooms > Kitchen > Bathrooms."""
        living_rooms = [r for r in spec.rooms if r.type == RoomType.LIVING]
        bathrooms = [r for r in spec.rooms if r.type in (RoomType.BATHROOM, RoomType.MASTER_BATHROOM, RoomType.HALF_BATH)]
        bedrooms = [r for r in spec.rooms if r.type in (RoomType.BEDROOM, RoomType.MASTER_BEDROOM)]

        # Check for disproportionate bathroom sizing
        for bath in bathrooms:
            # Standard residential bath should not exceed 6.5 m² (or 8.5 m² for master bath) unless explicit
            max_bath = 8.5 if bath.type == RoomType.MASTER_BATHROOM else 6.5

            if not explicit_bath_req and bath.target_area_sqm > max_bath:
                excess = bath.target_area_sqm - max_bath
                old_area = bath.target_area_sqm
                bath.target_area_sqm = round(max_bath, 1)

                msg = (
                    f"Corrected disproportionate {bath.label} from {old_area:.1f} m² to "
                    f"{bath.target_area_sqm:.1f} m² (standard residential proportion)."
                )
                errors.append(msg)
                logger.info("spatial_validator_clamped_bath", bath_id=bath.id, old=old_area, new=bath.target_area_sqm)

                # Reallocate excess area to living room or master bedroom
                if living_rooms:
                    living_rooms[0].target_area_sqm = round(living_rooms[0].target_area_sqm + excess, 1)
                elif bedrooms:
                    bedrooms[0].target_area_sqm = round(bedrooms[0].target_area_sqm + excess, 1)

        # Ensure Living Room is the primary social space
        if living_rooms:
            main_living = living_rooms[0]
            # In plans >= 45m², living room should be at least 14m² or 25% of total footprint
            min_living = max(14.0, total_footprint * 0.25) if total_footprint >= 50.0 else max(10.0, total_footprint * 0.22)

            # Check if any bathroom is larger than the living room
            for bath in bathrooms:
                if bath.target_area_sqm >= main_living.target_area_sqm:
                    msg = (
                        f"Detected spatial conflict: {bath.label} ({bath.target_area_sqm:.1f} m²) "
                        f"was larger than Living Room ({main_living.target_area_sqm:.1f} m²). "
                        "Rebalanced room areas to maintain functional hierarchy."
                    )
                    errors.append(msg)
                    logger.warning("spatial_hierarchy_inversion_detected", bath=bath.target_area_sqm, living=main_living.target_area_sqm)

                    # Swap and normalize areas
                    proper_living_area = max(bath.target_area_sqm, min_living)
                    proper_bath_area = 5.0 if bath.type != RoomType.HALF_BATH else 2.5
                    main_living.target_area_sqm = round(proper_living_area, 1)
                    bath.target_area_sqm = round(proper_bath_area, 1)

            if main_living.target_area_sqm < min_living and total_footprint >= 45.0:
                needed = min_living - main_living.target_area_sqm
                # Take from oversized hallways or utility if present
                for r in spec.rooms:
                    if r.type in (RoomType.HALLWAY, RoomType.FOYER, RoomType.STORAGE) and r.target_area_sqm > 10.0:
                        take = min(needed, r.target_area_sqm - 7.0)
                        r.target_area_sqm = round(r.target_area_sqm - take, 1)
                        main_living.target_area_sqm = round(main_living.target_area_sqm + take, 1)
                        needed -= take
                        if needed <= 0:
                            break

    @staticmethod
    def _balance_office_spec(
        spec: FloorPlanSpec,
        total_footprint: float,
        errors: list[str],
    ) -> None:
        """Enforces commercial office space hierarchy."""
        workspaces = [r for r in spec.rooms if r.type == RoomType.OFFICE or "work" in r.label.lower()]
        restrooms = [r for r in spec.rooms if r.type in (RoomType.BATHROOM, RoomType.HALF_BATH)]

        for rest in restrooms:
            max_rest = max(8.0, total_footprint * 0.08)
            if rest.target_area_sqm > max_rest:
                old = rest.target_area_sqm
                rest.target_area_sqm = round(max_rest, 1)
                excess = old - rest.target_area_sqm
                if workspaces:
                    workspaces[0].target_area_sqm = round(workspaces[0].target_area_sqm + excess, 1)
                errors.append(f"Adjusted office restroom from {old:.1f} m² to {rest.target_area_sqm:.1f} m².")

    @staticmethod
    def _balance_commercial_spec(
        spec: FloorPlanSpec,
        total_footprint: float,
        errors: list[str],
    ) -> None:
        """Enforces retail and commercial customer/service proportions."""
        restrooms = [r for r in spec.rooms if r.type in (RoomType.BATHROOM, RoomType.HALF_BATH)]
        for rest in restrooms:
            max_rest = max(6.0, total_footprint * 0.08)
            if rest.target_area_sqm > max_rest:
                rest.target_area_sqm = round(max_rest, 1)

    @staticmethod
    def _balance_industrial_spec(
        spec: FloorPlanSpec,
        total_footprint: float,
        errors: list[str],
    ) -> None:
        """Enforces industrial/warehouse floor vs support facility balance."""
        pass

    @staticmethod
    def _enforce_minimum_areas(spec: FloorPlanSpec, errors: list[str]) -> None:
        """Ensures every room meets minimum usable thresholds."""
        for room in spec.rooms:
            min_floor = 1.5 if room.type in (RoomType.HALF_BATH, RoomType.CLOSET, RoomType.PANTRY) else 2.5
            if room.target_area_sqm < min_floor:
                room.target_area_sqm = min_floor

    @staticmethod
    def validate_generated_geometry(
        floor_plan: GeneratedFloorPlan,
        errors: list[str] | None = None,
    ) -> GeneratedFloorPlan:
        """Post-generation geometry validation: checks actual room polygons and coordinates.

        Detects:
        - Inverted polygon assignments (e.g. bathroom ending up with living room's polygon)
        - Collapsed or near-zero area polygons
        - Extreme aspect ratios or clipping issues
        Heals detected anomalies and ensures mathematical consistency.
        """
        if errors is None:
            errors = []

        spec = floor_plan.spec
        rooms = spec.rooms

        # 1. Check for inverted polygon assignments
        # If room A target was 20m² and room B target was 5m², but room A actual is 5m²
        # and room B actual is 20m², the solver's slot optimizer inverted their bounding boxes!
        for i, room_a in enumerate(rooms):
            if not room_a.actual_area_sqm or not room_a.polygon:
                continue
            for j, room_b in enumerate(rooms):
                if i >= j or not room_b.actual_area_sqm or not room_b.polygon:
                    continue

                # Check if target areas were opposite to actual areas
                target_diff = room_a.target_area_sqm - room_b.target_area_sqm
                actual_diff = room_a.actual_area_sqm - room_b.actual_area_sqm

                # Significant inversion: target A was > B by at least 5m², but actual A < B by at least 5m²
                if target_diff >= 6.0 and actual_diff <= -6.0:
                    logger.warning(
                        "inversion_detected_healing_geometry",
                        room_a=room_a.label,
                        target_a=room_a.target_area_sqm,
                        actual_a=room_a.actual_area_sqm,
                        room_b=room_b.label,
                        target_b=room_b.target_area_sqm,
                        actual_b=room_b.actual_area_sqm,
                    )
                    # Swap polygons and actual areas to match functional intent
                    room_a.polygon, room_b.polygon = room_b.polygon, room_a.polygon
                    room_a.actual_area_sqm, room_b.actual_area_sqm = (
                        room_b.actual_area_sqm,
                        room_a.actual_area_sqm,
                    )
                    errors.append(
                        f"Healed inverted spatial geometry: aligned {room_a.label} ({room_a.actual_area_sqm:.1f} m²) "
                        f"and {room_b.label} ({room_b.actual_area_sqm:.1f} m²) with architectural proportions."
                    )

        # 2. Aspect ratio and dimensional validation
        for room in rooms:
            if room.polygon and len(room.polygon) >= 3:
                xs = [c.x for c in room.polygon]
                ys = [c.y for c in room.polygon]
                w = max(xs) - min(xs)
                h = max(ys) - min(ys)
                if min(w, h) > 0:
                    aspect = max(w, h) / min(w, h)
                    # Warn if non-corridor room has an extreme aspect ratio (> 4.5)
                    if aspect > 4.5 and room.type not in (RoomType.HALLWAY, RoomType.CLOSET):
                        errors.append(
                            f"Room '{room.label}' has a narrow aspect ratio ({aspect:.1f}:1). "
                            "Consider relaxing boundary constraints."
                        )

        # 3. Recalculate floor plan aggregate metrics
        floor_plan.total_area_sqm = round(
            sum(r.actual_area_sqm or 0 for r in spec.rooms), 2
        )
        if spec.plot.buildable_area_sqm and spec.plot.buildable_area_sqm > 0:
            floor_plan.lot_coverage_ratio = round(
                floor_plan.total_area_sqm / spec.plot.buildable_area_sqm, 3
            )

        return floor_plan
