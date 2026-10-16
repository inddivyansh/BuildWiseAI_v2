"""
Mock floor plan generator for development.

Returns hardcoded but realistic floor plan layouts so the frontend
can be developed and tested without any ML models running.
"""

import uuid
import time

from shapely.geometry import box

from app.schemas.floor_plan import (
    AdjacencyRequirement,
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
from app.services.geometry.cleanup import cleanup_pipeline, extract_wall_segments


# --- Preset layouts ---

def _bungalow_3br() -> dict:
    """3-bedroom bungalow on a 12×20m lot."""
    rooms_raw = [
        box(0, 0, 5.5, 4.5),    # Living Room
        box(5.5, 0, 9.0, 4.5),  # Kitchen
        box(0, 4.5, 4.0, 8.0),  # Master Bedroom
        box(4.0, 4.5, 7.0, 7.0),  # Bedroom 2
        box(7.0, 4.5, 9.0, 7.0),  # Bathroom
        box(4.0, 7.0, 9.0, 8.0),  # Hallway
    ]
    labels = ["Living Room", "Kitchen", "Master Bedroom", "Bedroom 2", "Bathroom", "Hallway"]
    types = [RoomType.LIVING, RoomType.KITCHEN, RoomType.MASTER_BEDROOM,
             RoomType.BEDROOM, RoomType.BATHROOM, RoomType.HALLWAY]
    return {"rooms_raw": rooms_raw, "labels": labels, "types": types,
            "lot_width": 12.0, "lot_depth": 20.0}


def _studio() -> dict:
    """Simple studio apartment."""
    rooms_raw = [
        box(0, 0, 6.0, 5.0),  # Main Room
        box(0, 5.0, 3.0, 7.0),  # Kitchen
        box(3.0, 5.0, 6.0, 7.0),  # Bathroom
    ]
    labels = ["Main Room", "Kitchen", "Bathroom"]
    types = [RoomType.LIVING, RoomType.KITCHEN, RoomType.BATHROOM]
    return {"rooms_raw": rooms_raw, "labels": labels, "types": types,
            "lot_width": 10.0, "lot_depth": 15.0}


def _two_story_4br() -> dict:
    """4-bedroom two-story house (ground floor only for now)."""
    rooms_raw = [
        box(0, 0, 5.0, 5.0),    # Living Room
        box(5.0, 0, 9.0, 3.5),  # Kitchen
        box(5.0, 3.5, 9.0, 5.0),  # Dining
        box(0, 5.0, 4.5, 8.0),  # Garage
        box(4.5, 5.0, 9.0, 6.5),  # Hallway
        box(4.5, 6.5, 9.0, 8.0),  # Half Bath
    ]
    labels = ["Living Room", "Kitchen", "Dining Room", "Garage", "Hallway", "Half Bath"]
    types = [RoomType.LIVING, RoomType.KITCHEN, RoomType.DINING,
             RoomType.GARAGE, RoomType.HALLWAY, RoomType.HALF_BATH]
    return {"rooms_raw": rooms_raw, "labels": labels, "types": types,
            "lot_width": 15.0, "lot_depth": 25.0}


PRESETS = {
    "bungalow": _bungalow_3br,
    "studio": _studio,
    "two_story": _two_story_4br,
}


def _pick_preset(prompt: str) -> dict:
    """Select a preset based on keywords in the prompt."""
    prompt_lower = prompt.lower()
    if "studio" in prompt_lower or "1 bedroom" in prompt_lower or "bachelor" in prompt_lower:
        return _studio()
    if "4 bed" in prompt_lower or "two stor" in prompt_lower or "2 stor" in prompt_lower:
        return _two_story_4br()
    return _bungalow_3br()


def _polygon_to_coords(polygon) -> list[Coordinate]:
    """Convert Shapely polygon to list of Coordinate."""
    return [Coordinate(x=round(x, 3), y=round(y, 3)) for x, y in polygon.exterior.coords]


def generate_mock_floor_plan(
    prompt: str,
    plot_width_m: float | None = None,
    plot_depth_m: float | None = None,
    locale: str = "CA-QC",
) -> GeneratedFloorPlan:
    """
    Generate a mock floor plan from a text prompt.
    Returns a complete GeneratedFloorPlan with realistic geometry.
    """
    t0 = time.time()
    generation_id = str(uuid.uuid4())
    preset = _pick_preset(prompt)

    lot_w = plot_width_m or preset["lot_width"]
    lot_d = plot_depth_m or preset["lot_depth"]
    setbacks = Setbacks()
    plot = PlotConstraints(width_m=lot_w, depth_m=lot_d, setbacks=setbacks)
    boundary = box(
        setbacks.left_m, setbacks.front_m,
        lot_w - setbacks.right_m, lot_d - setbacks.rear_m,
    )

    # Run geometry cleanup
    cleaned = cleanup_pipeline(preset["rooms_raw"], boundary, grid_size_m=0.05)
    room_polygons = cleaned["rooms"]
    wall_dicts = cleaned["walls"]

    # Build Room objects
    rooms = []
    for i, (polygon, label, rtype) in enumerate(
        zip(room_polygons, preset["labels"], preset["types"])
    ):
        rooms.append(Room(
            id=f"{rtype.value}_{i}",
            type=rtype,
            label=label,
            target_area_sqm=round(polygon.area, 1),
            actual_area_sqm=round(polygon.area, 2),
            polygon=_polygon_to_coords(polygon),
            floor=0,
        ))

    # Build WallSegment objects
    walls = []
    for wd in wall_dicts:
        walls.append(WallSegment(
            id=wd["id"],
            start=Coordinate(x=wd["start"]["x"], y=wd["start"]["y"]),
            end=Coordinate(x=wd["end"]["x"], y=wd["end"]["y"]),
            is_exterior=wd["is_exterior"],
        ))

    # Place doors on interior walls
    doors = []
    interior_walls = [w for w in walls if not w.is_exterior]
    for i, wall in enumerate(interior_walls[:6]):
        doors.append(DoorPlacement(
            id=f"door_{i}",
            wall_id=wall.id,
            position_along_wall=0.5,
        ))

    # Place windows on exterior walls
    windows = []
    exterior_walls = [w for w in walls if w.is_exterior]
    for i, wall in enumerate(exterior_walls[:8]):
        windows.append(WindowPlacement(
            id=f"window_{i}",
            wall_id=wall.id,
            position_along_wall=0.5,
        ))

    spec = FloorPlanSpec(rooms=rooms, plot=plot, locale=locale)
    total_area = sum(r.actual_area_sqm or 0 for r in rooms)
    target_total = sum(r.target_area_sqm for r in rooms)

    return GeneratedFloorPlan(
        spec=spec,
        walls=walls,
        doors=doors,
        windows=windows,
        generation_id=generation_id,
        generator_model="mock_v1",
        generation_time_s=round(time.time() - t0, 3),
        total_area_sqm=round(total_area, 2),
        lot_coverage_ratio=round(total_area / plot.buildable_area_sqm, 3)
            if plot.buildable_area_sqm else 0,
        area_deviation_pct=0.0,
    )
