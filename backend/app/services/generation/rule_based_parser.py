"""Deterministic Rule-Based Floor Plan Specification Parser.

Converts natural language architectural prompts into validated FloorPlanSpec
structures using pattern matching, keyword extraction, and architectural heuristics.
100% offline — zero LLM calls, zero API keys, zero rate-limit quotas, instant execution.
"""

from __future__ import annotations

import re
import uuid
import structlog
from typing import Any

from app.schemas.floor_plan import (
    AdjacencyRequirement,
    FloorPlanSpec,
    PlotConstraints,
    Relationship,
    Room,
    RoomType,
    Setbacks,
)
from app.services.geometry.spatial_validator import DynamicSpatialValidator

logger = structlog.get_logger()

# Word-to-number mapping
_WORD_TO_NUM: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "single": 1, "double": 2, "triple": 3, "quad": 4,
}


def _parse_int_token(token: str | None, default: int = 1) -> int:
    if not token:
        return default
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _WORD_TO_NUM.get(token, default)


class RuleBasedParser:
    """Deterministic parser for architectural floor plan requests."""

    @classmethod
    def parse_prompt_to_spec(
        cls,
        prompt: str,
        plot_width_m: float | None = None,
        plot_depth_m: float | None = None,
        locale: str = "CA-QC",
        zone_id: str | None = None,
    ) -> FloorPlanSpec:
        """Parse natural language prompt into a valid FloorPlanSpec without an LLM."""
        text = prompt.lower().strip()
        logger.info("rule_based_parser_started", prompt=prompt[:80])

        # 1. Extract or default plot dimensions
        width_m = plot_width_m
        depth_m = plot_depth_m

        if width_m is None or depth_m is None:
            # Match "15x20", "15 by 20", "15m x 20m", "15 * 20"
            dim_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:x|by|\*|×)\s*(\d+(?:\.\d+)?)\s*m?", text)
            if dim_match:
                extracted_w = float(dim_match.group(1))
                extracted_d = float(dim_match.group(2))
                width_m = width_m or extracted_w
                depth_m = depth_m or extracted_d

            # Match "15m wide", "20m deep"
            w_match = re.search(r"(\d+(?:\.\d+)?)\s*m?\s*(?:wide|width)", text)
            if w_match and width_m is None:
                width_m = float(w_match.group(1))
            d_match = re.search(r"(\d+(?:\.\d+)?)\s*m?\s*(?:deep|depth|long|length)", text)
            if d_match and depth_m is None:
                depth_m = float(d_match.group(1))

        # Sensible defaults for suburban/urban residential lots
        width_m = max(width_m or 15.0, 8.0)
        depth_m = max(depth_m or 20.0, 10.0)

        # 2. Extract number of floors
        num_floors = 1
        if any(term in text for term in ("2 story", "2-story", "two story", "two-story", "2 floor", "2-floor", "duplex", "second floor")):
            num_floors = 2
        elif any(term in text for term in ("3 story", "3-story", "three story", "triplex")):
            num_floors = 3

        # 3. Detect commercial or specialized context
        is_commercial = any(k in text for k in ("commercial", "office building", "clinic", "retail", "shop", "warehouse"))

        rooms: list[Room] = []
        adjacencies: list[AdjacencyRequirement] = []

        if is_commercial:
            rooms = cls._build_commercial_rooms(text)
        else:
            rooms = cls._build_residential_rooms(text)

        # 4. Generate logical architectural adjacencies
        adjacencies = cls._build_default_adjacencies(rooms)

        # 5. Build plot constraints with standard setbacks
        plot = PlotConstraints(
            width_m=width_m,
            depth_m=depth_m,
            setbacks=Setbacks(front_m=4.5, rear_m=7.5, left_m=1.5, right_m=1.5),
            max_lot_coverage=0.6,
        )

        spec = FloorPlanSpec(
            rooms=rooms,
            adjacency=adjacencies,
            plot=plot,
            num_floors=num_floors,
            locale=locale,
            zone_id=zone_id,
        )

        # 6. Apply dynamic spatial validator for proportional balancing against plot footprint
        errors: list[str] = []
        spec = DynamicSpatialValidator.validate_and_correct_spec(spec, prompt=prompt, errors=errors)

        logger.info(
            "rule_based_parser_complete",
            room_count=len(spec.rooms),
            total_area=spec.total_target_area,
            floors=spec.num_floors,
            plot=f"{spec.plot.width_m}x{spec.plot.depth_m}m",
        )
        return spec

    @classmethod
    def _build_residential_rooms(cls, text: str) -> list[Room]:
        rooms: list[Room] = []

        # Bedroom count
        bed_match = re.search(r"(\d+|one|two|three|four|five|six|seven)\s*[- ]*(?:bed|bedroom|br)s?", text)
        bed_count = _parse_int_token(bed_match.group(1), default=3) if bed_match else 3
        # Enforce realistic range [1, 6]
        bed_count = max(1, min(bed_count, 6))

        # Bathroom count
        bath_match = re.search(r"(\d+(?:\.5)?|one|two|three|four|five)\s*[- ]*(?:bath|bathroom)s?", text)
        if bath_match:
            raw_b = bath_match.group(1).lower()
            if raw_b.endswith(".5"):
                bath_count = int(float(raw_b))
                has_half_bath = True
            else:
                bath_count = _parse_int_token(raw_b, default=2)
                has_half_bath = False
        else:
            bath_count = 2 if bed_count >= 3 else 1
            has_half_bath = False
        bath_count = max(1, min(bath_count, 5))

        # --- Primary Living Spaces ---
        rooms.append(Room(
            id="living_1",
            type=RoomType.LIVING,
            label="Living Room",
            target_area_sqm=24.0,
            min_area_sqm=16.0,
            requires_window=True,
        ))

        rooms.append(Room(
            id="kitchen_1",
            type=RoomType.KITCHEN,
            label="Kitchen",
            target_area_sqm=12.0,
            min_area_sqm=8.0,
            requires_window=True,
        ))

        if any(term in text for term in ("dining", "dinning", "dining area", "dining room")) or bed_count >= 3:
            rooms.append(Room(
                id="dining_1",
                type=RoomType.DINING,
                label="Dining Room",
                target_area_sqm=12.0,
                min_area_sqm=8.0,
                requires_window=True,
            ))

        # --- Bedrooms ---
        rooms.append(Room(
            id="bedroom_1",
            type=RoomType.MASTER_BEDROOM,
            label="Master Bedroom",
            target_area_sqm=16.0,
            min_area_sqm=12.0,
            requires_window=True,
        ))

        for i in range(2, bed_count + 1):
            rooms.append(Room(
                id=f"bedroom_{i}",
                type=RoomType.BEDROOM,
                label=f"Bedroom {i}",
                target_area_sqm=11.5,
                min_area_sqm=8.5,
                requires_window=True,
            ))

        # --- Bathrooms ---
        rooms.append(Room(
            id="bathroom_1",
            type=RoomType.BATHROOM,
            label="Main Bathroom",
            target_area_sqm=5.0,
            min_area_sqm=3.5,
            requires_window=False,
        ))

        if bath_count >= 2:
            rooms.append(Room(
                id="bathroom_2",
                type=RoomType.MASTER_BATHROOM,
                label="Master Ensuite",
                target_area_sqm=4.5,
                min_area_sqm=3.2,
                requires_window=False,
            ))

        for b in range(3, bath_count + 1):
            rooms.append(Room(
                id=f"bathroom_{b}",
                type=RoomType.BATHROOM,
                label=f"Bathroom {b}",
                target_area_sqm=4.0,
                min_area_sqm=3.0,
                requires_window=False,
            ))

        if has_half_bath:
            rooms.append(Room(
                id="half_bath_1",
                type=RoomType.HALF_BATH,
                label="Powder Room",
                target_area_sqm=2.5,
                min_area_sqm=1.8,
                requires_window=False,
            ))

        # --- Additional Amenities ---
        # Garage
        if any(term in text for term in ("garage", "carport", "parking")):
            is_double = any(term in text for term in ("double garage", "2-car", "2 car", "two car"))
            rooms.append(Room(
                id="garage_1",
                type=RoomType.GARAGE,
                label="Double Garage" if is_double else "Garage",
                target_area_sqm=32.0 if is_double else 18.0,
                min_area_sqm=16.0,
                requires_window=False,
                requires_exterior_wall=True,
            ))

        # Home Office / Study
        if any(term in text for term in ("office", "study", "den", "workroom", "library")):
            rooms.append(Room(
                id="office_1",
                type=RoomType.OFFICE,
                label="Home Office",
                target_area_sqm=10.0,
                min_area_sqm=7.0,
                requires_window=True,
            ))

        # Laundry
        if any(term in text for term in ("laundry", "washroom", "washer")):
            rooms.append(Room(
                id="laundry_1",
                type=RoomType.LAUNDRY,
                label="Laundry Room",
                target_area_sqm=4.5,
                min_area_sqm=3.0,
                requires_window=False,
            ))

        # Balcony / Patio / Porch
        if any(term in text for term in ("balcony", "patio", "porch", "deck", "terrace")):
            rooms.append(Room(
                id="porch_1",
                type=RoomType.PORCH,
                label="Patio / Porch",
                target_area_sqm=8.0,
                min_area_sqm=4.0,
                requires_window=False,
                requires_exterior_wall=True,
            ))

        # Foyer / Entry
        rooms.append(Room(
            id="foyer_1",
            type=RoomType.FOYER,
            label="Entry Foyer",
            target_area_sqm=4.5,
            min_area_sqm=3.0,
            requires_window=False,
        ))

        return rooms

    @classmethod
    def _build_commercial_rooms(cls, text: str) -> list[Room]:
        rooms: list[Room] = [
            Room(id="reception_1", type=RoomType.FOYER, label="Reception Area", target_area_sqm=20.0, min_area_sqm=12.0),
            Room(id="conf_1", type=RoomType.OFFICE, label="Conference Room", target_area_sqm=24.0, min_area_sqm=16.0),
            Room(id="office_1", type=RoomType.OFFICE, label="Executive Office", target_area_sqm=15.0, min_area_sqm=10.0),
            Room(id="office_2", type=RoomType.OFFICE, label="Staff Office", target_area_sqm=15.0, min_area_sqm=10.0),
            Room(id="break_1", type=RoomType.KITCHEN, label="Breakroom", target_area_sqm=12.0, min_area_sqm=8.0),
            Room(id="restroom_1", type=RoomType.BATHROOM, label="Restroom", target_area_sqm=6.0, min_area_sqm=4.0),
        ]
        return rooms

    @classmethod
    def _build_default_adjacencies(cls, rooms: list[Room]) -> list[AdjacencyRequirement]:
        adjacencies: list[AdjacencyRequirement] = []
        room_ids = {r.id for r in rooms}

        # Kitchen <-> Dining
        if "kitchen_1" in room_ids and "dining_1" in room_ids:
            adjacencies.append(AdjacencyRequirement(
                room_a_id="kitchen_1", room_b_id="dining_1", relationship=Relationship.ADJACENT
            ))

        # Dining <-> Living
        if "dining_1" in room_ids and "living_1" in room_ids:
            adjacencies.append(AdjacencyRequirement(
                room_a_id="dining_1", room_b_id="living_1", relationship=Relationship.ADJACENT
            ))
        elif "kitchen_1" in room_ids and "living_1" in room_ids:
            # Open concept kitchen <-> living
            adjacencies.append(AdjacencyRequirement(
                room_a_id="kitchen_1", room_b_id="living_1", relationship=Relationship.ADJACENT
            ))

        # Foyer <-> Living
        if "foyer_1" in room_ids and "living_1" in room_ids:
            adjacencies.append(AdjacencyRequirement(
                room_a_id="foyer_1", room_b_id="living_1", relationship=Relationship.ADJACENT
            ))

        # Master Ensuite <-> Master Bedroom
        if "bathroom_2" in room_ids and "bedroom_1" in room_ids:
            adjacencies.append(AdjacencyRequirement(
                room_a_id="bathroom_2", room_b_id="bedroom_1", relationship=Relationship.CONNECTED
            ))

        # Garage <-> Foyer / Mudroom
        if "garage_1" in room_ids and "foyer_1" in room_ids:
            adjacencies.append(AdjacencyRequirement(
                room_a_id="garage_1", room_b_id="foyer_1", relationship=Relationship.ADJACENT
            ))

        return adjacencies
