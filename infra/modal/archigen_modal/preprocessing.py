"""
Convert ArchiGen FloorPlanSpec → HouseDiffusion model input tensors.

HouseDiffusion (CVPR 2023) expects per-corner condition tensors packed into a
fixed-length sequence (max 100 corners, zero-padded). Each corner carries:
  - room_type: one-hot [25]
  - corner_index: one-hot [32]  (index within its room)
  - room_index: one-hot [32]    (which room it belongs to, 1-indexed)
  - padding_mask: [1]
  - connections: [2]             (prev/next corner indices for point expansion)

Plus three attention masks [100×100]:
  - door_mask:  0 where rooms are door-connected, 1 elsewhere
  - self_mask:  0 where corners belong to same room, 1 elsewhere
  - gen_mask:   0 for real corners, 1 for padding

Reference: house_diffusion/rplanhg_datasets.py — RPlanhgDataset.__getitem__()
"""

from __future__ import annotations

import numpy as np

# ---------------------------------------------------------------------------
# Constants matching HouseDiffusion's RPLAN dataset format
# ---------------------------------------------------------------------------

MAX_NUM_POINTS = 100   # Fixed sequence length (padded)
NUM_ROOM_CLASSES = 25  # One-hot dimension for room types (RPLAN uses 0-24)
NUM_ROOM_SLOTS = 32    # One-hot dimension for room_index
NUM_CORNER_SLOTS = 32  # One-hot dimension for corner_index

# ArchiGen room type string → RPLAN room class index
# RPLAN uses: 1=living, 2=master_room, ..., 10=balcony, 11=entrance, etc.
# But in the dataset code, exterior types {15,17,16} get remapped to {11,12,13}
# and regular rooms use indices 1-10. The model expects 25-dim one-hot (0-24).
ARCHIGEN_TO_RPLAN: dict[str, int] = {
    "living":           1,   # living_room
    "master_bedroom":   2,   # master_room
    "kitchen":          3,   # kitchen
    "bathroom":         4,   # bathroom
    "dining":           5,   # dining_room
    "bedroom":          6,   # child_room
    "office":           7,   # study_room
    "master_bathroom":  8,   # second_bathroom
    "half_bath":        8,   # second_bathroom (closest match)
    "utility":          9,   # guest_room (general purpose)
    "balcony":          10,  # balcony
    "foyer":            11,  # entrance (remapped from 15→11 in dataset)
    "storage":          12,  # storage/wall-in (remapped from 17→12)
    "walkin_closet":    12,  # walk_in → storage slot
    # Less direct mappings
    "hallway":          11,  # entrance
    "closet":           12,  # storage
    "pantry":           12,  # storage
    "mudroom":          11,  # entrance
    "laundry":          9,   # guest_room (utility space)
    "garage":           13,  # external_area (remapped from 16→13)
    "porch":            10,  # balcony (outdoor)
    "staircase":        11,  # entrance
    "other":            9,   # guest_room (catch-all)
}


def get_rplan_index(archigen_room_type: str) -> int:
    """Map an ArchiGen room type string to an RPLAN room class index."""
    return ARCHIGEN_TO_RPLAN.get(archigen_room_type, 9)  # default: guest_room


def spec_to_housediff_input(spec_dict: dict) -> dict:
    """
    Convert a serialized FloorPlanSpec dict into HouseDiffusion input tensors.

    This replicates the data preparation from RPlanhgDataset.__getitem__()
    for the 'eval' / sampling path (corners initialized to zeros).

    Args:
        spec_dict: FloorPlanSpec serialized via .model_dump().

    Returns:
        dict with numpy arrays ready for torch conversion:
            - room_types:  float [MAX_NUM_POINTS, 25]
            - corner_indices: float [MAX_NUM_POINTS, 32]
            - room_indices: float [MAX_NUM_POINTS, 32]
            - connections:  float [MAX_NUM_POINTS, 2]
            - src_key_padding_mask: float [MAX_NUM_POINTS]
            - door_mask:   float [MAX_NUM_POINTS, MAX_NUM_POINTS]
            - self_mask:   float [MAX_NUM_POINTS, MAX_NUM_POINTS]
            - gen_mask:    float [MAX_NUM_POINTS, MAX_NUM_POINTS]
            - graph:       float [200, 3]  (room adjacency triples)
            - num_rooms:   int
            - num_corners_per_room: list[int]
            - total_corners: int
            - buildable_width: float
            - buildable_depth: float
    """
    rooms = spec_dict["rooms"]
    adjacency = spec_dict.get("adjacency", [])
    plot = spec_dict["plot"]

    num_rooms = len(rooms)

    # --- Determine corners per room (4 for rectangular output) ---
    num_corners_per_room = [4] * num_rooms
    total_corners = sum(num_corners_per_room)

    if total_corners > MAX_NUM_POINTS:
        raise ValueError(
            f"Too many corners ({total_corners}) for {num_rooms} rooms. "
            f"Max supported: {MAX_NUM_POINTS}."
        )

    # --- Build per-corner condition arrays ---
    # Following rplanhg_datasets.py lines 189-210
    corner_bounds: list[tuple[int, int]] = []  # (start, end) per room
    all_room_types = []      # [total_corners, 25]
    all_corner_indices = []  # [total_corners, 32]
    all_room_indices = []    # [total_corners, 32]
    all_padding_masks = []   # [total_corners, 1]
    all_connections = []     # [total_corners, 2]

    cursor = 0
    for room_idx, room in enumerate(rooms):
        nc = num_corners_per_room[room_idx]
        rplan_type = get_rplan_index(room["type"])

        # room_type: one-hot [25], repeated for each corner
        rtype_onehot = np.eye(NUM_ROOM_CLASSES)[rplan_type]  # [25]
        rtype = np.repeat(rtype_onehot[np.newaxis, :], nc, axis=0)  # [nc, 25]

        # room_index: one-hot [32], 1-indexed (room 0 → index 1)
        ridx_onehot = np.eye(NUM_ROOM_SLOTS)[room_idx + 1]  # [32]
        room_index = np.repeat(ridx_onehot[np.newaxis, :], nc, axis=0)  # [nc, 32]

        # corner_index: one-hot [32], 0-indexed within room
        corner_index = np.array([
            np.eye(NUM_CORNER_SLOTS)[c] for c in range(nc)
        ])  # [nc, 32]

        # padding_mask: 1 for real corners
        padding_mask = np.ones((nc, 1))

        # connections: [current_corner_global, next_corner_global] per corner.
        # Used by expand_points in the transformer for edge interpolation.
        # Must match rplanhg_datasets.py: [[i, (i+1)%nc] for i in range(nc)] + offset
        connections = np.array([
            [c, (c + 1) % nc] for c in range(nc)
        ]) + cursor  # [nc, 2]

        corner_bounds.append((cursor, cursor + nc))
        cursor += nc

        all_room_types.append(rtype)
        all_corner_indices.append(corner_index)
        all_room_indices.append(room_index)
        all_padding_masks.append(padding_mask)
        all_connections.append(connections)

    # Concatenate all corners
    room_types = np.concatenate(all_room_types, axis=0)        # [total, 25]
    corner_indices = np.concatenate(all_corner_indices, axis=0) # [total, 32]
    room_indices = np.concatenate(all_room_indices, axis=0)     # [total, 32]
    padding_masks = np.concatenate(all_padding_masks, axis=0)   # [total, 1]
    connections = np.concatenate(all_connections, axis=0)        # [total, 2]

    # --- Pad to MAX_NUM_POINTS ---
    pad_len = MAX_NUM_POINTS - total_corners

    room_types = np.concatenate([room_types, np.zeros((pad_len, NUM_ROOM_CLASSES))], axis=0)
    corner_indices = np.concatenate([corner_indices, np.zeros((pad_len, NUM_CORNER_SLOTS))], axis=0)
    room_indices = np.concatenate([room_indices, np.zeros((pad_len, NUM_ROOM_SLOTS))], axis=0)
    padding_masks_flat = np.concatenate([padding_masks.flatten(), np.zeros(pad_len)])
    connections = np.concatenate([connections, np.zeros((pad_len, 2))], axis=0)

    # --- Build adjacency graph triples [room_i, edge_type, room_j] ---
    # edge_type: 1 = door-connected, -1 = not adjacent
    room_id_to_idx = {r["id"]: i for i, r in enumerate(rooms)}
    connected_pairs: set[tuple[int, int]] = set()

    for adj in adjacency:
        a_id = adj["room_a_id"]
        b_id = adj["room_b_id"]
        if a_id in room_id_to_idx and b_id in room_id_to_idx:
            a = room_id_to_idx[a_id]
            b = room_id_to_idx[b_id]
            if a < b:
                connected_pairs.add((a, b))
            else:
                connected_pairs.add((b, a))

    graph_triples = []
    for i in range(num_rooms):
        for j in range(i + 1, num_rooms):
            if (i, j) in connected_pairs:
                graph_triples.append([i, 1, j])
            else:
                graph_triples.append([i, -1, j])

    # If no adjacency specified, assume all rooms are connected
    if not adjacency:
        graph_triples = [[i, 1, j] for i in range(num_rooms) for j in range(i + 1, num_rooms)]

    graph = np.array(graph_triples, dtype=np.float32) if graph_triples else np.zeros((0, 3), dtype=np.float32)
    # Pad graph to fixed size 200
    graph_padded = np.zeros((200, 3), dtype=np.float32)
    graph_padded[:len(graph)] = graph

    # --- Build attention masks [MAX_NUM_POINTS × MAX_NUM_POINTS] ---
    # Following rplanhg_datasets.py lines 220-231
    door_mask = np.ones((MAX_NUM_POINTS, MAX_NUM_POINTS), dtype=np.float32)
    self_mask = np.ones((MAX_NUM_POINTS, MAX_NUM_POINTS), dtype=np.float32)
    gen_mask = np.ones((MAX_NUM_POINTS, MAX_NUM_POINTS), dtype=np.float32)

    # gen_mask: 0 for real corners (unmasked), 1 for padding (masked)
    gen_mask[:total_corners, :total_corners] = 0

    for i in range(num_rooms):
        si, ei = corner_bounds[i]
        for j in range(num_rooms):
            sj, ej = corner_bounds[j]
            if i == j:
                # self_mask: 0 where corners belong to same room
                self_mask[si:ei, sj:ej] = 0
            else:
                # door_mask: 0 where rooms are door-connected
                pair = (min(i, j), max(i, j))
                if pair in connected_pairs or not adjacency:
                    # If no adjacency specified, treat all as connected
                    door_mask[si:ei, sj:ej] = 0

    # For rooms with no connections, connect to first room (living room heuristic)
    if adjacency:
        for i in range(num_rooms):
            has_connection = any(
                (min(i, j), max(i, j)) in connected_pairs
                for j in range(num_rooms) if j != i
            )
            if not has_connection and num_rooms > 1:
                si, ei = corner_bounds[i]
                s0, e0 = corner_bounds[0]
                door_mask[si:ei, s0:e0] = 0

    # --- Buildable dimensions ---
    buildable_w = plot.get("buildable_width_m") or (
        plot["width_m"] - plot.get("setbacks", {}).get("left_m", 1.5)
        - plot.get("setbacks", {}).get("right_m", 1.5)
    )
    buildable_d = plot.get("buildable_depth_m") or (
        plot["depth_m"] - plot.get("setbacks", {}).get("front_m", 4.5)
        - plot.get("setbacks", {}).get("rear_m", 7.5)
    )

    # --- Target areas (for postprocessing fallback) ---
    target_areas = np.array(
        [r["target_area_sqm"] for r in rooms],
        dtype=np.float32,
    )

    return {
        "room_types": room_types.astype(np.float32),
        "corner_indices": corner_indices.astype(np.float32),
        "room_indices": room_indices.astype(np.float32),
        "connections": connections.astype(np.float32),
        "src_key_padding_mask": (1 - padding_masks_flat).astype(np.float32),
        "door_mask": door_mask,
        "self_mask": self_mask,
        "gen_mask": gen_mask,
        "graph": graph_padded,
        "num_rooms": num_rooms,
        "num_corners_per_room": num_corners_per_room,
        "total_corners": total_corners,
        "buildable_width": float(buildable_w),
        "buildable_depth": float(buildable_d),
        "target_areas": target_areas,
    }


def build_room_type_onehot(room_types: np.ndarray) -> np.ndarray:
    """
    Build one-hot encoded room type matrix (legacy helper).

    Args:
        room_types: int array [N] of RPLAN indices

    Returns:
        float array [N, NUM_ROOM_CLASSES] one-hot encoded
    """
    n = len(room_types)
    onehot = np.zeros((n, NUM_ROOM_CLASSES), dtype=np.float32)
    for i, rt in enumerate(room_types):
        idx = min(int(rt), NUM_ROOM_CLASSES - 1)
        onehot[i, idx] = 1.0
    return onehot
