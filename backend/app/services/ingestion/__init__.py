"""Floor plan ingestion services — parse uploaded files into editable FloorPlanSpec."""

from app.services.ingestion.interface import FloorPlanParser
from app.services.ingestion.room_type_mapper import (
    map_label_to_room_type,
    map_cubicasa_class_to_room_type,
)

__all__ = [
    "FloorPlanParser",
    "map_label_to_room_type",
    "map_cubicasa_class_to_room_type",
]
