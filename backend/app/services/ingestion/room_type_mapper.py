"""Map detected room labels to RoomType enum values.

Handles English, French, CubiCasa class names, DXF layer names, and IFC space names.
"""

import re

from shapely.geometry import Polygon

from app.schemas.floor_plan import RoomType


# --- CubiCasa room class index -> RoomType ---
CUBICASA_TO_ROOMTYPE: dict[int, RoomType | None] = {
    0: None,               # Background
    1: None,               # Outdoor
    2: None,               # Wall (not a room)
    3: RoomType.KITCHEN,
    4: RoomType.LIVING,
    5: RoomType.BEDROOM,
    6: RoomType.BATHROOM,
    7: RoomType.FOYER,     # Entry
    8: None,               # Railing (not a room)
    9: RoomType.STORAGE,
    10: RoomType.GARAGE,
    11: RoomType.OTHER,    # Undefined
}

CUBICASA_CLASS_NAMES: dict[int, str] = {
    0: "Background",
    1: "Outdoor",
    2: "Wall",
    3: "Kitchen",
    4: "Living Room",
    5: "Bed Room",
    6: "Bath",
    7: "Entry",
    8: "Railing",
    9: "Storage",
    10: "Garage",
    11: "Undefined",
}


# --- Keyword -> RoomType lookup (English + French + abbreviations) ---
_LABEL_MAP: list[tuple[list[str], RoomType]] = [
    # Master bed/bath first (more specific before less specific)
    (["master bed", "chambre principale", "suite parentale", "mbr", "master_bed"],
     RoomType.MASTER_BEDROOM),
    (["master bath", "sdb principale", "ensuite", "master_bath"],
     RoomType.MASTER_BATHROOM),

    # Bedrooms
    (["bedroom", "bed room", "chambre", "chambre a coucher", "br", "bdrm"],
     RoomType.BEDROOM),

    # Bathrooms
    (["half bath", "powder room", "half_bath", "toilette", "wc", "lavabo"],
     RoomType.HALF_BATH),
    (["bathroom", "bath room", "bath", "salle de bain", "sdb"],
     RoomType.BATHROOM),

    # Living spaces
    (["living", "living room", "salon", "sejour", "séjour", "family room", "great room"],
     RoomType.LIVING),
    (["dining", "dining room", "salle a manger", "salle à manger"],
     RoomType.DINING),
    (["kitchen", "cuisine", "kitchenette", "kit"],
     RoomType.KITCHEN),

    # Utility
    (["office", "bureau", "study", "den", "home office"],
     RoomType.OFFICE),
    (["laundry", "buanderie", "laundry room", "lndry"],
     RoomType.LAUNDRY),
    (["utility", "mechanical", "mecanique", "mécanique", "furnace"],
     RoomType.UTILITY),
    (["storage", "rangement", "storeroom", "strg"],
     RoomType.STORAGE),
    (["pantry", "garde-manger", "pntry"],
     RoomType.PANTRY),

    # Closets
    (["walk-in closet", "walkin closet", "walk in closet", "walkin", "walk-in", "dressing"],
     RoomType.WALKIN_CLOSET),
    (["closet", "placard", "penderie", "clst"],
     RoomType.CLOSET),

    # Circulation
    (["hallway", "hall", "corridor", "couloir", "passage"],
     RoomType.HALLWAY),
    (["foyer", "entry", "entrance", "entree", "entrée", "vestibule", "lobby"],
     RoomType.FOYER),
    (["mudroom", "mud room", "vestaire"],
     RoomType.MUDROOM),
    (["staircase", "stairs", "stair", "escalier"],
     RoomType.STAIRCASE),

    # Garage
    (["garage", "carport"],
     RoomType.GARAGE),

    # Outdoor
    (["balcony", "balcon"],
     RoomType.BALCONY),
    (["porch", "veranda", "véranda", "porche", "terrace", "terrasse", "deck"],
     RoomType.PORCH),
]


def map_label_to_room_type(label: str) -> RoomType | None:
    """Map a room label string to a RoomType enum value.

    Performs case-insensitive fuzzy matching against known room names
    in English and French.

    Returns None if no match found.
    """
    if not label:
        return None

    cleaned = _normalize_label(label)

    # Try exact match on keywords
    for keywords, room_type in _LABEL_MAP:
        for kw in keywords:
            if kw in cleaned:
                return room_type

    return None


def map_cubicasa_class_to_room_type(class_idx: int) -> RoomType | None:
    """Map a CubiCasa segmentation class index to RoomType."""
    return CUBICASA_TO_ROOMTYPE.get(class_idx)


def cubicasa_class_label(class_idx: int) -> str:
    """Get the human-readable label for a CubiCasa class index."""
    return CUBICASA_CLASS_NAMES.get(class_idx, f"Unknown ({class_idx})")


def map_dxf_layer_to_room_type(layer_name: str) -> RoomType | None:
    """Map a DXF layer name to RoomType.

    DXF layers often use patterns like:
    - "A-ROOM-KITCHEN"
    - "BEDROOM_1"
    - "ROOM_LIVING"
    """
    cleaned = _normalize_label(layer_name)
    # Remove common DXF prefixes
    for prefix in ["a-room-", "a-space-", "room-", "room_", "space-", "space_"]:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix):]
            break
    # Remove trailing numbers (e.g. "bedroom_1" -> "bedroom")
    cleaned = re.sub(r"[_\-]\d+$", "", cleaned)

    for keywords, room_type in _LABEL_MAP:
        for kw in keywords:
            if kw in cleaned:
                return room_type
    return None


def map_ifc_name_to_room_type(ifc_name: str) -> RoomType | None:
    """Map an IFC IfcSpace LongName/Name to RoomType."""
    return map_label_to_room_type(ifc_name)


def _normalize_label(label: str) -> str:
    """Normalize a label for fuzzy matching."""
    # Lowercase
    text = label.lower().strip()
    # Replace common separators with spaces
    text = re.sub(r"[_\-./\\]+", " ", text)
    # Remove numbers in parentheses like "(1)"
    text = re.sub(r"\(\d+\)", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def estimate_area_from_polygon(polygon_coords: list[tuple[float, float]]) -> float:
    """Compute area in sqm from a list of (x, y) coordinates in meters."""
    if len(polygon_coords) < 3:
        return 0.0
    poly = Polygon(polygon_coords)
    return abs(poly.area)


def area_to_size_bucket(area_sqm: float) -> str:
    """Classify room area for display purposes."""
    if area_sqm < 4:
        return "XS"
    elif area_sqm < 8:
        return "S"
    elif area_sqm < 15:
        return "M"
    elif area_sqm < 25:
        return "L"
    else:
        return "XL"
