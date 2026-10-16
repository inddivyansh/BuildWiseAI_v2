"""
2D → 3D extrusion: convert flat room polygons and wall segments into 3D meshes.

Uses trimesh to extrude 2D Shapely polygons into 3D solids for:
- 3D preview rendering (Three.js in frontend)
- STL/OBJ/GLB export
- Collision detection and volume calculations
"""

import numpy as np
import trimesh
from shapely.geometry import Polygon


def extrude_polygon(
    polygon: Polygon,
    height_m: float = 2.7,
    z_offset_m: float = 0.0,
) -> trimesh.Trimesh:
    """
    Extrude a 2D Shapely polygon into a 3D solid.

    Args:
        polygon: 2D room or wall polygon.
        height_m: Extrusion height (floor-to-ceiling).
        z_offset_m: Vertical offset (for multi-story).

    Returns:
        trimesh.Trimesh representing the 3D solid.
    """
    coords_2d = np.array(polygon.exterior.coords[:-1])  # drop closing point
    vertices_2d = coords_2d[:, :2]

    mesh = trimesh.creation.extrude_polygon(polygon, height_m)

    if z_offset_m != 0:
        mesh.apply_translation([0, 0, z_offset_m])

    return mesh


def extrude_rooms(
    room_polygons: list[Polygon],
    room_labels: list[str] | None = None,
    floor_height_m: float = 2.7,
    floor_number: int = 0,
) -> list[dict]:
    """
    Extrude all room polygons into 3D meshes.

    Returns:
        List of dicts: {"label": str, "mesh": trimesh.Trimesh, "volume_m3": float}
    """
    results = []
    z_offset = floor_number * floor_height_m

    for i, polygon in enumerate(room_polygons):
        label = room_labels[i] if room_labels and i < len(room_labels) else f"room_{i}"
        mesh = extrude_polygon(polygon, floor_height_m, z_offset)
        results.append({
            "label": label,
            "mesh": mesh,
            "volume_m3": float(mesh.volume),
            "area_sqm": float(polygon.area),
        })

    return results


def extrude_walls(
    wall_segments: list[dict],
    floor_height_m: float = 2.7,
    wall_thickness_m: float = 0.15,
    floor_number: int = 0,
) -> list[dict]:
    """
    Extrude wall centerlines into 3D rectangular wall meshes.

    Args:
        wall_segments: Wall dicts from extract_wall_segments() with start/end coords.
        floor_height_m: Wall height.
        wall_thickness_m: Wall thickness.
        floor_number: Floor index for vertical offset.

    Returns:
        List of dicts: {"id": str, "mesh": trimesh.Trimesh, "is_exterior": bool}
    """
    from shapely.geometry import LineString

    results = []
    z_offset = floor_number * floor_height_m

    for wall_data in wall_segments:
        sx = wall_data["start"]["x"]
        sy = wall_data["start"]["y"]
        ex = wall_data["end"]["x"]
        ey = wall_data["end"]["y"]

        centerline = LineString([(sx, sy), (ex, ey)])
        wall_polygon = centerline.buffer(wall_thickness_m / 2, cap_style="flat")

        if wall_polygon.is_empty or wall_polygon.area < 1e-6:
            continue

        mesh = extrude_polygon(wall_polygon, floor_height_m, z_offset)
        results.append({
            "id": wall_data.get("id", f"wall_{len(results)}"),
            "mesh": mesh,
            "is_exterior": wall_data.get("is_exterior", False),
        })

    return results


def build_floor_mesh(
    room_polygons: list[Polygon],
    room_labels: list[str] | None = None,
    wall_segments: list[dict] | None = None,
    floor_height_m: float = 2.7,
    wall_thickness_m: float = 0.15,
    floor_number: int = 0,
) -> trimesh.Scene:
    """
    Build a complete 3D scene for one floor with rooms and walls.

    Returns:
        trimesh.Scene containing all room and wall meshes.
    """
    scene = trimesh.Scene()

    # Add rooms
    room_meshes = extrude_rooms(room_polygons, room_labels, floor_height_m, floor_number)
    for rm in room_meshes:
        scene.add_geometry(rm["mesh"], node_name=rm["label"])

    # Add walls
    if wall_segments:
        wall_meshes = extrude_walls(
            wall_segments, floor_height_m, wall_thickness_m, floor_number
        )
        for wm in wall_meshes:
            scene.add_geometry(wm["mesh"], node_name=wm["id"])

    return scene


def export_scene_glb(scene: trimesh.Scene, output_path: str) -> str:
    """Export a trimesh Scene to GLB (binary glTF) for Three.js."""
    scene.export(output_path, file_type="glb")
    return output_path
