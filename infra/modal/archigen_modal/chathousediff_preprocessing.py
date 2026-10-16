"""
Convert ArchiGen FloorPlanSpec -> ChatHouseDiffusion model input.

ChatHouseDiffusion (arXiv 2410.11908) architecture:
  - LLM Parser -> Graphormer + T5 Embeddings -> U-Net Diffusion
  - We bypass the LLM parser and feed our FloorPlanSpec directly

Model interface (from repo analysis):
  - Unet takes graphormer_dict with:
      node_feat: (B, 10, 2304) -- concat [category_t5, location_t5, size_t5]
      in_degree/out_degree: (B, 10) -- clamped to [0, 4], +1 offset
      path_data: (B, 10, 10, 4, 1) -- edge features along shortest paths
      dist: (B, 10, 10) -- shortest path distances
      attn_mask: (B, 11, 11) -- padding mask (+1 for virtual graph token)
  - diffusion.sample(batch_size=1, text=None, graphormer_dict=dict, cond_scale=N)

t5_feature.pkl format: List of 3 dicts [category_dict, location_dict, size_dict]
  - category_dict: {"Unknown": array(768,), "LivingRoom": ..., "Kitchen": ...}
  - location_dict: {"north": ..., "center": ..., "Unknown": ...}
  - size_dict: {"Unknown": ..., "XS": ..., "S": ..., "M": ..., "L": ..., "XL": ...}

For editing:
  - Previous plan encoded as noised latent
  - edit_strength (tau) controls blend between old and new
"""

from __future__ import annotations

import math
import pickle
from collections import deque
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
# Constants (matching ChatHD repo)
# ---------------------------------------------------------------------------

MAX_ROOMS = 10          # MAX_NUM_NODES from graph_encoder.py
RESOLUTION = 64         # ChatHD generates 64x64 images (from params.pkl: image_size=64)
ENCODED_DIM = 768       # T5 embedding dimension (ENCODED_DIM from graph_encoder.py)
T5_EMBED_DIM = 768      # Alias
MULTI_HOP_MAX_DIST = 4  # Max hops for path encoding

# Room type -> T5 feature key mapping
# ChatHD uses CamelCase room type names as T5 keys (from graph_encoder.py)
ARCHIGEN_TO_CHATHD_LABEL: dict[str, str] = {
    "living":           "LivingRoom",
    "master_bedroom":   "MasterRoom",
    "bedroom":          "ChildRoom",
    "kitchen":          "Kitchen",
    "bathroom":         "Bathroom",
    "half_bath":        "Bathroom",
    "master_bathroom":  "Bathroom",
    "dining":           "DiningRoom",
    "office":           "StudyRoom",
    "balcony":          "Balcony",
    "foyer":            "Entrance",
    "hallway":          "Entrance",
    "mudroom":          "Entrance",
    "staircase":        "Entrance",
    "porch":            "Balcony",
    "laundry":          "Storage",
    "garage":           "Storage",
    "closet":           "Storage",
    "walkin_closet":    "Storage",
    "pantry":           "Storage",
    "storage":          "Storage",
    "utility":          "Storage",
    "other":            "Storage",
}

# RPLAN-style room type index for ChatHD (same 13-class scheme)
ARCHIGEN_TO_CHATHD_TYPE: dict[str, int] = {
    "living":           1,
    "master_bedroom":   2,
    "kitchen":          3,
    "bathroom":         4,
    "dining":           5,
    "bedroom":          6,
    "office":           7,
    "master_bathroom":  8,
    "half_bath":        8,
    "utility":          9,
    "balcony":          10,
    "foyer":            11,
    "storage":          12,
    "walkin_closet":    12,
    "hallway":          11,
    "closet":           12,
    "pantry":           12,
    "mudroom":          11,
    "laundry":          9,
    "garage":           13,
    "porch":            10,
    "staircase":        11,
    "other":            9,
}

# Size buckets for area -> size label mapping
_SIZE_THRESHOLDS = [
    (6.0, "XS"),
    (10.0, "S"),
    (18.0, "M"),
    (30.0, "L"),
]


def _area_to_size_label(area_sqm: float) -> str:
    """Map room area to ChatHD size label."""
    for threshold, label in _SIZE_THRESHOLDS:
        if area_sqm <= threshold:
            return label
    return "XL"


def load_t5_features(t5_path: str | Path) -> dict:
    """Load T5 feature embeddings from pickle file.

    t5_feature.pkl contains a LIST of 3 dicts:
      [category_dict, location_dict, size_dict]

    We return a structured dict with keys 'category', 'location', 'size'.
    """
    with open(t5_path, "rb") as f:
        raw = pickle.load(f)

    if isinstance(raw, list) and len(raw) >= 3:
        return {
            "category": raw[0],
            "location": raw[1],
            "size": raw[2],
        }
    elif isinstance(raw, dict):
        # Fallback: if already a flat dict, wrap it
        return {"category": raw, "location": raw, "size": raw}
    else:
        print(f"WARNING: Unexpected t5_feature.pkl format: {type(raw)}")
        return {"category": {}, "location": {}, "size": {}}


def get_t5_embedding(
    key: str,
    t5_dict: dict[str, np.ndarray],
) -> np.ndarray:
    """Look up T5 embedding for a key from one of the sub-dicts.

    Falls back to 'Unknown', then zero vector.
    """
    # Try exact match
    if key in t5_dict:
        val = t5_dict[key]
        if isinstance(val, np.ndarray):
            return val.astype(np.float32)
        return np.array(val, dtype=np.float32)

    # Try case-insensitive
    for k, v in t5_dict.items():
        if k.lower() == key.lower():
            if isinstance(v, np.ndarray):
                return v.astype(np.float32)
            return np.array(v, dtype=np.float32)

    # Try "Unknown" fallback
    if "Unknown" in t5_dict and key != "Unknown":
        val = t5_dict["Unknown"]
        if isinstance(val, np.ndarray):
            return val.astype(np.float32)
        return np.array(val, dtype=np.float32)

    return np.zeros(ENCODED_DIM, dtype=np.float32)


def _compute_shortest_paths(adj: np.ndarray, num_nodes: int):
    """Compute shortest path distances using BFS."""
    dist = np.full((num_nodes, num_nodes), -1, dtype=np.int64)

    for src in range(num_nodes):
        visited = np.full(num_nodes, -1, dtype=np.int64)
        visited[src] = 0
        queue = deque([src])
        while queue:
            node = queue.popleft()
            for nbr in range(num_nodes):
                if adj[node, nbr] and visited[nbr] == -1:
                    visited[nbr] = visited[node] + 1
                    queue.append(nbr)
        dist[src] = visited

    return dist


def build_graphormer_dict(
    rooms: list[dict],
    adjacency: list[dict],
    t5_features: dict,
) -> dict[str, np.ndarray]:
    """
    Build graphormer_dict matching ChatHD's expected format.

    This replaces the DGL-based construction in graph_encoder.py with
    a pure-numpy implementation.

    Args:
        rooms: List of room dicts with 'type', 'target_area_sqm'
        adjacency: List of adjacency dicts with 'room_a_id', 'room_b_id'
        t5_features: Structured dict with 'category', 'location', 'size' sub-dicts

    Returns:
        dict with numpy arrays for the Graphormer
    """
    num_rooms = min(len(rooms), MAX_ROOMS)
    rooms_used = rooms[:num_rooms]

    # Extract sub-dicts (handle both structured and flat formats)
    if isinstance(t5_features, dict) and "category" in t5_features:
        cat_dict = t5_features["category"]
        loc_dict = t5_features["location"]
        size_dict = t5_features["size"]
    else:
        # Flat dict fallback
        cat_dict = t5_features if isinstance(t5_features, dict) else {}
        loc_dict = cat_dict
        size_dict = cat_dict

    # --- Build adjacency matrix ---
    room_id_to_idx = {r["id"]: i for i, r in enumerate(rooms_used)}
    adj = np.zeros((num_rooms, num_rooms), dtype=bool)
    for a in adjacency:
        a_id = a.get("room_a_id", "")
        b_id = a.get("room_b_id", "")
        if a_id in room_id_to_idx and b_id in room_id_to_idx:
            i, j = room_id_to_idx[a_id], room_id_to_idx[b_id]
            adj[i, j] = True
            adj[j, i] = True

    if not adjacency:
        for i in range(num_rooms):
            for j in range(num_rooms):
                if i != j:
                    adj[i, j] = True

    # --- node_feat: concat [category_t5(768), location_t5(768), size_t5(768)] ---
    node_feat = np.zeros((1, MAX_ROOMS, ENCODED_DIM * 3), dtype=np.float32)
    for i, room in enumerate(rooms_used):
        # Category embedding
        cat_key = ARCHIGEN_TO_CHATHD_LABEL.get(room["type"], "Storage")
        cat_embed = get_t5_embedding(cat_key, cat_dict)

        # Location embedding (no spatial info available, use "Unknown")
        loc_embed = get_t5_embedding("Unknown", loc_dict)

        # Size embedding
        area = room.get("target_area_sqm", 15.0)
        size_key = _area_to_size_label(area)
        size_embed = get_t5_embedding(size_key, size_dict)

        node_feat[0, i, :ENCODED_DIM] = cat_embed
        node_feat[0, i, ENCODED_DIM:ENCODED_DIM * 2] = loc_embed
        node_feat[0, i, ENCODED_DIM * 2:] = size_embed

    # --- in_degree / out_degree ---
    degrees = adj.sum(axis=1).astype(np.int64) + 1
    degrees = np.clip(degrees, 0, 4)

    in_degree = np.zeros((1, MAX_ROOMS), dtype=np.int64)
    out_degree = np.zeros((1, MAX_ROOMS), dtype=np.int64)
    in_degree[0, :num_rooms] = degrees
    out_degree[0, :num_rooms] = degrees

    # --- dist: shortest path distances ---
    sp_dist = _compute_shortest_paths(adj, num_rooms)
    dist = np.full((1, MAX_ROOMS, MAX_ROOMS), -1, dtype=np.int64)
    dist[0, :num_rooms, :num_rooms] = sp_dist

    # --- path_data: edge features along shortest paths ---
    path_data = np.zeros(
        (1, MAX_ROOMS, MAX_ROOMS, MULTI_HOP_MAX_DIST, 1),
        dtype=np.float32,
    )
    for i in range(num_rooms):
        for j in range(num_rooms):
            d = sp_dist[i, j]
            if d > 0:
                for k in range(min(int(d), MULTI_HOP_MAX_DIST)):
                    path_data[0, i, j, k, 0] = 1.0

    # --- attn_mask: (1, MAX_ROOMS+1, MAX_ROOMS+1) ---
    attn_mask = np.zeros(
        (1, MAX_ROOMS + 1, MAX_ROOMS + 1), dtype=np.float32
    )
    attn_mask[0, :, num_rooms + 1:] = 1.0

    return {
        "attn_mask": attn_mask,
        "node_feat": node_feat,
        "in_degree": in_degree,
        "out_degree": out_degree,
        "path_data": path_data,
        "dist": dist,
    }


def spec_to_chathd_input(
    spec_dict: dict,
    t5_features: dict,
) -> dict:
    """
    Convert a FloorPlanSpec dict into ChatHouseDiffusion input tensors.

    Args:
        spec_dict: FloorPlanSpec serialized via model_dump_json() + json.loads()
        t5_features: Structured dict with 'category', 'location', 'size' sub-dicts

    Returns:
        dict with graphormer_dict, room_types, num_rooms, dimensions, etc.
    """
    rooms = spec_dict["rooms"]
    adjacency = spec_dict.get("adjacency", [])
    plot = spec_dict["plot"]

    num_rooms = min(len(rooms), MAX_ROOMS)
    rooms_used = rooms[:num_rooms]

    room_types = np.array([
        ARCHIGEN_TO_CHATHD_TYPE.get(r["type"], 9)
        for r in rooms_used
    ], dtype=np.int64)

    graphormer_dict = build_graphormer_dict(rooms_used, adjacency, t5_features)

    target_areas = np.array(
        [r["target_area_sqm"] for r in rooms_used], dtype=np.float32
    )
    total_area = target_areas.sum()
    room_areas = target_areas / total_area if total_area > 0 else target_areas

    buildable_w = plot.get("buildable_width_m") or (
        plot["width_m"] - plot.get("setbacks", {}).get("left_m", 1.5)
        - plot.get("setbacks", {}).get("right_m", 1.5)
    )
    buildable_d = plot.get("buildable_depth_m") or (
        plot["depth_m"] - plot.get("setbacks", {}).get("front_m", 4.5)
        - plot.get("setbacks", {}).get("rear_m", 7.5)
    )

    # Extract category sub-dict for per-room embeddings
    cat_dict = t5_features.get("category", {}) if isinstance(t5_features, dict) else {}

    t5_embeddings = np.zeros((num_rooms, ENCODED_DIM), dtype=np.float32)
    for i, room in enumerate(rooms_used):
        cat_key = ARCHIGEN_TO_CHATHD_LABEL.get(room["type"], "Storage")
        t5_embeddings[i] = get_t5_embedding(cat_key, cat_dict)

    adjacency_matrix = np.zeros((num_rooms, num_rooms), dtype=np.float32)
    room_id_to_idx = {r["id"]: i for i, r in enumerate(rooms_used)}
    for adj in adjacency:
        a_id = adj["room_a_id"]
        b_id = adj["room_b_id"]
        if a_id in room_id_to_idx and b_id in room_id_to_idx:
            a = room_id_to_idx[a_id]
            b = room_id_to_idx[b_id]
            adjacency_matrix[a, b] = 1.0
            adjacency_matrix[b, a] = 1.0
    if not adjacency:
        for i in range(num_rooms):
            for j in range(num_rooms):
                if i != j:
                    adjacency_matrix[i, j] = 1.0

    return {
        "graphormer_dict": graphormer_dict,
        "room_types": room_types,
        "num_rooms": num_rooms,
        "buildable_width": float(buildable_w),
        "buildable_depth": float(buildable_d),
        "target_areas": target_areas,
        "room_areas": room_areas,
        "t5_embeddings": t5_embeddings,
        "adjacency_matrix": adjacency_matrix,
    }


def spec_to_chathd_edit_input(
    spec_dict: dict,
    previous_polygons: list[list[tuple[float, float]]],
    edit_strength: float,
    t5_features: dict,
) -> dict:
    """
    Convert a FloorPlanSpec + previous plan into ChatHouseDiffusion edit inputs.
    """
    inputs = spec_to_chathd_input(spec_dict, t5_features)

    plot = spec_dict["plot"]
    buildable_w = inputs["buildable_width"]
    buildable_d = inputs["buildable_depth"]
    offset_x = plot.get("setbacks", {}).get("left_m", 1.5)
    offset_y = plot.get("setbacks", {}).get("front_m", 4.5)

    # Rasterize previous plan to RESOLUTION x RESOLUTION image
    plan_image = np.zeros((RESOLUTION, RESOLUTION), dtype=np.float32)

    for i, poly_coords in enumerate(previous_polygons):
        if not poly_coords or i >= len(inputs["room_types"]):
            continue

        room_type_val = float(inputs["room_types"][i]) / 13.0

        pixel_coords = []
        for x, y in poly_coords:
            px = int((x - offset_x) / buildable_w * RESOLUTION)
            py = int((y - offset_y) / buildable_d * RESOLUTION)
            px = max(0, min(px, RESOLUTION - 1))
            py = max(0, min(py, RESOLUTION - 1))
            pixel_coords.append((px, py))

        _fill_polygon_scanline(plan_image, pixel_coords, room_type_val)

    inputs["previous_plan"] = plan_image
    inputs["edit_strength"] = float(edit_strength)

    return inputs


def _fill_polygon_scanline(
    image: np.ndarray,
    coords: list[tuple[int, int]],
    value: float,
):
    """Fill a polygon in an image using simple scanline approach."""
    if len(coords) < 3:
        return

    if coords[0] == coords[-1]:
        coords = coords[:-1]

    ys = [c[1] for c in coords]
    min_y = max(0, min(ys))
    max_y = min(image.shape[0] - 1, max(ys))

    for y in range(min_y, max_y + 1):
        intersections = []
        n = len(coords)
        for i in range(n):
            j = (i + 1) % n
            y1, y2 = coords[i][1], coords[j][1]
            if y1 == y2:
                continue
            if min(y1, y2) <= y < max(y1, y2):
                x_intersect = coords[i][0] + (y - y1) * (coords[j][0] - coords[i][0]) / (y2 - y1)
                intersections.append(int(x_intersect))

        intersections.sort()
        for k in range(0, len(intersections) - 1, 2):
            x_start = max(0, intersections[k])
            x_end = min(image.shape[1] - 1, intersections[k + 1])
            image[y, x_start:x_end + 1] = value
