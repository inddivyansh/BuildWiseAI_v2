"""
Convert ArchiGen FloorPlanSpec → GSDiff model input tensors.

GSDiff (AAAI 2025) uses a 3-stage topology-constrained pipeline:
  - Stage 0: TopoGraphModel — encodes room-level adjacency graph
  - Stage 1: TopoHeterHouseModel — 1000-step DDPM diffusion generating corners
  - Stage 2: TopoEdgeModel — predicts edges between corners

Input tensor formats (all padded to fixed dimensions):
  - bb_semantics: (MAX_ROOMS, 7)       room-level one-hot (7 semantic categories)
  - bb_adjacency_matrix: (MAX_ROOMS, MAX_ROOMS)  adjacency graph
  - bb_semantics_padding_mask: (MAX_ROOMS, 1)     which rooms are real
  - bb_global_matrix: (MAX_ROOMS, MAX_ROOMS)      full attention mask
  - corners_shape: (1, MAX_CORNERS, 10)           noise initialization shape
  - global_attn_matrix: (MAX_CORNERS, MAX_CORNERS) corner-level attention
  - corners_padding_mask: (MAX_CORNERS, 1)         which corners are real

Key constraints: max 8 rooms (bubble diagram), 53 max corners,
7 semantic categories, resolution 512.
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Constants matching GSDiff's topology-constrained model
# ---------------------------------------------------------------------------

MAX_ROOMS = 8           # Max rooms in the bubble diagram
MAX_CORNERS = 53        # Max total corners across all rooms
NUM_SEMANTIC_CLASSES = 7  # GSDiff uses 7 room semantic categories
RESOLUTION = 512        # GSDiff operates in 512×512 pixel space
DIFFUSION_STEPS = 1000  # DDPM steps

# Corner features: (x, y) coords + 7 semantic one-hot + 1 padding = 10
CORNER_FEATURE_DIM = 10

# ArchiGen room type → GSDiff semantic category index
# GSDiff categories (from paper / dataset code):
#   0 = living_room
#   1 = bedroom (master + regular)
#   2 = kitchen
#   3 = bathroom (all types)
#   4 = dining / study / office
#   5 = balcony / foyer / hallway / entrance
#   6 = other (utility, storage, garage, etc.)
ARCHIGEN_TO_GSDIFF: dict[str, int] = {
    "living":           0,
    "master_bedroom":   1,
    "bedroom":          1,
    "kitchen":          2,
    "bathroom":         3,
    "half_bath":        3,
    "master_bathroom":  3,
    "dining":           4,
    "office":           4,
    "balcony":          5,
    "foyer":            5,
    "hallway":          5,
    "mudroom":          5,
    "staircase":        5,
    "porch":            5,
    "laundry":          6,
    "garage":           6,
    "closet":           6,
    "walkin_closet":    6,
    "pantry":           6,
    "storage":          6,
    "utility":          6,
    "other":            6,
}

# Room types to prioritize for GSDiff (ordered by importance).
# Same strategy as HouseDiffusion's _split_rooms().
_CORE_PRIORITY = [
    "living", "kitchen", "master_bedroom", "bedroom",
    "dining", "garage", "master_bathroom", "bathroom", "office",
]


def get_gsdiff_semantic(archigen_room_type: str) -> int:
    """Map an ArchiGen room type string to a GSDiff semantic category index."""
    return ARCHIGEN_TO_GSDIFF.get(archigen_room_type, 6)  # default: other


def split_rooms_for_gsdiff(
    rooms: list[dict],
) -> tuple[list[int], list[int]]:
    """
    Split rooms into core (for GSDiff, max 8) and ancillary (algorithmic).

    Same priority-based splitting as HouseDiffusion's _split_rooms().

    Returns:
        (core_indices, ancillary_indices) — indices into the rooms list.
    """
    scored: list[tuple[int, int]] = []
    for i, room in enumerate(rooms):
        rtype = room["type"]
        if rtype in _CORE_PRIORITY:
            score = _CORE_PRIORITY.index(rtype)
        else:
            score = 100  # ancillary
        scored.append((i, score))

    scored.sort(key=lambda x: x[1])

    core = [idx for idx, score in scored[:MAX_ROOMS] if score < 100]
    core_set = set(core)
    ancillary = [idx for idx, _ in scored if idx not in core_set]

    # If no core rooms, take the largest rooms
    if not core and ancillary:
        ancillary.sort(key=lambda i: -rooms[i].get("target_area_sqm", 0))
        core = ancillary[:min(len(ancillary), MAX_ROOMS)]
        ancillary = ancillary[len(core):]

    # Preserve original order within each group
    core.sort()
    ancillary.sort()

    return core, ancillary


def spec_to_gsdiff_input(spec_dict: dict) -> dict:
    """
    Convert a serialized FloorPlanSpec dict into GSDiff input tensors.

    Args:
        spec_dict: FloorPlanSpec serialized via .model_dump_json() + json.loads().

    Returns:
        dict with numpy arrays ready for torch conversion:
            - bb_semantics: float (MAX_ROOMS, 7) — room-level one-hot
            - bb_adjacency_matrix: float (MAX_ROOMS, MAX_ROOMS) — adjacency
            - bb_semantics_padding_mask: float (MAX_ROOMS, 1) — which rooms are real
            - bb_global_matrix: float (MAX_ROOMS, MAX_ROOMS) — full attention mask
            - corners_shape: tuple (1, MAX_CORNERS, 10) — for noise init
            - global_attn_matrix: float (MAX_CORNERS, MAX_CORNERS) — corner attention
            - corners_padding_mask: float (MAX_CORNERS, 1)
            - num_rooms: int
            - num_corners_per_room: list[int]
            - total_corners: int
            - buildable_width: float (meters)
            - buildable_depth: float (meters)
            - target_areas: float array [num_rooms]
            - room_indices: list[int] — original spec indices if split was applied
    """
    rooms = spec_dict["rooms"]
    adjacency = spec_dict.get("adjacency", [])
    plot = spec_dict["plot"]

    # If more than MAX_ROOMS, split and only send core rooms
    if len(rooms) > MAX_ROOMS:
        core_indices, ancillary_indices = split_rooms_for_gsdiff(rooms)
        core_rooms = [rooms[i] for i in core_indices]
        core_ids = {rooms[i]["id"] for i in core_indices}
        # Filter adjacency to core rooms only
        core_adjacency = [
            a for a in adjacency
            if a["room_a_id"] in core_ids and a["room_b_id"] in core_ids
        ]
        rooms = core_rooms
        adjacency = core_adjacency
        room_indices = core_indices
    else:
        room_indices = list(range(len(rooms)))

    num_rooms = len(rooms)

    # --- Room-level tensors ---
    # bb_semantics: (MAX_ROOMS, 7) one-hot
    bb_semantics = np.zeros((MAX_ROOMS, NUM_SEMANTIC_CLASSES), dtype=np.float32)
    for i, room in enumerate(rooms):
        cat = get_gsdiff_semantic(room["type"])
        bb_semantics[i, cat] = 1.0

    # bb_adjacency_matrix: (MAX_ROOMS, MAX_ROOMS)
    bb_adjacency_matrix = np.zeros((MAX_ROOMS, MAX_ROOMS), dtype=np.float32)
    room_id_to_idx = {r["id"]: i for i, r in enumerate(rooms)}
    for adj in adjacency:
        a_id = adj["room_a_id"]
        b_id = adj["room_b_id"]
        if a_id in room_id_to_idx and b_id in room_id_to_idx:
            a = room_id_to_idx[a_id]
            b = room_id_to_idx[b_id]
            bb_adjacency_matrix[a, b] = 1.0
            bb_adjacency_matrix[b, a] = 1.0

    # If no adjacency, assume all connected
    if not adjacency:
        for i in range(num_rooms):
            for j in range(num_rooms):
                if i != j:
                    bb_adjacency_matrix[i, j] = 1.0

    # bb_semantics_padding_mask: (MAX_ROOMS, 1) — 1 for real rooms
    bb_semantics_padding_mask = np.zeros((MAX_ROOMS, 1), dtype=np.float32)
    bb_semantics_padding_mask[:num_rooms, 0] = 1.0

    # bb_global_matrix: (MAX_ROOMS, MAX_ROOMS) — full attention mask
    # 1 where both rooms are real (unmasked), 0 otherwise
    bb_global_matrix = np.zeros((MAX_ROOMS, MAX_ROOMS), dtype=np.float32)
    bb_global_matrix[:num_rooms, :num_rooms] = 1.0

    # --- Corner-level tensors ---
    # Assume 4 corners per room for rectangular output, but we allocate
    # proportionally based on area for rooms that may need more corners
    num_corners_per_room = [4] * num_rooms
    total_corners = sum(num_corners_per_room)

    if total_corners > MAX_CORNERS:
        # Reduce corners per room to fit
        per_room = MAX_CORNERS // num_rooms
        num_corners_per_room = [max(4, per_room)] * num_rooms
        total_corners = sum(num_corners_per_room)

    # corners_shape for noise initialization
    corners_shape = (1, MAX_CORNERS, CORNER_FEATURE_DIM)

    # global_attn_matrix: (MAX_CORNERS, MAX_CORNERS) — True for real corners
    global_attn_matrix = np.zeros((MAX_CORNERS, MAX_CORNERS), dtype=np.float32)
    global_attn_matrix[:total_corners, :total_corners] = 1.0

    # corners_padding_mask: (MAX_CORNERS, 1) — 1 for real corners
    corners_padding_mask = np.zeros((MAX_CORNERS, 1), dtype=np.float32)
    corners_padding_mask[:total_corners, 0] = 1.0

    # --- Buildable dimensions ---
    buildable_w = plot.get("buildable_width_m") or (
        plot["width_m"] - plot.get("setbacks", {}).get("left_m", 1.5)
        - plot.get("setbacks", {}).get("right_m", 1.5)
    )
    buildable_d = plot.get("buildable_depth_m") or (
        plot["depth_m"] - plot.get("setbacks", {}).get("front_m", 4.5)
        - plot.get("setbacks", {}).get("rear_m", 7.5)
    )

    # --- Target areas ---
    target_areas = np.array(
        [r["target_area_sqm"] for r in rooms],
        dtype=np.float32,
    )

    return {
        "bb_semantics": bb_semantics,
        "bb_adjacency_matrix": bb_adjacency_matrix,
        "bb_semantics_padding_mask": bb_semantics_padding_mask,
        "bb_global_matrix": bb_global_matrix,
        "corners_shape": corners_shape,
        "global_attn_matrix": global_attn_matrix,
        "corners_padding_mask": corners_padding_mask,
        "num_rooms": num_rooms,
        "num_corners_per_room": num_corners_per_room,
        "total_corners": total_corners,
        "buildable_width": float(buildable_w),
        "buildable_depth": float(buildable_d),
        "target_areas": target_areas,
        "room_indices": room_indices,
    }
