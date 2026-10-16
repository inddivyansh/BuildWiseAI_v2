"""
IfcOpenShell BIM generation: clean 2D polygons → IFC file with walls, spaces, doors.

Produces industry-standard IFC4 files that open in Revit, ArchiCAD, FreeCAD, Bonsai.
All coordinates in meters. Z-axis is up.
"""

from pathlib import Path

import ifcopenshell
import ifcopenshell.api
from shapely.geometry import Polygon


def create_ifc_from_floor_plan(
    room_polygons: list[Polygon],
    room_labels: list[str],
    walls: list[dict],
    floor_height_m: float = 2.7,
    wall_thickness_m: float = 0.15,
    output_path: str | Path = "output.ifc",
) -> Path:
    """
    Generate a complete IFC4 file from cleaned floor plan geometry.

    Args:
        room_polygons: Clean room polygons from geometry cleanup pipeline
        room_labels: Display names for each room
        walls: Wall segments from extract_wall_segments()
        floor_height_m: Floor-to-floor height
        wall_thickness_m: Wall thickness
        output_path: Where to write the .ifc file

    Returns:
        Path to the generated .ifc file
    """
    output_path = Path(output_path)
    
    # Create new IFC file
    ifc = ifcopenshell.api.run("project.create_file", version="IFC4")
    
    # Project setup
    project = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcProject", name="ArchiGen Project")
    
    # Units: meters
    ifcopenshell.api.run("unit.assign_unit", ifc, length={"is_metric": True, "raw": "METRES"})
    
    # Context
    model_context = ifcopenshell.api.run("context.add_context", ifc, context_type="Model")
    body_context = ifcopenshell.api.run(
        "context.add_context", ifc,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=model_context,
    )
    
    # Site and Building
    site = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcSite", name="Site")
    building = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuilding", name="Building")
    storey = ifcopenshell.api.run("root.create_entity", ifc, ifc_class="IfcBuildingStorey", name="Ground Floor")
    
    ifcopenshell.api.run("aggregate.assign_object", ifc, products=[site], relating_object=project)
    ifcopenshell.api.run("aggregate.assign_object", ifc, products=[building], relating_object=site)
    ifcopenshell.api.run("aggregate.assign_object", ifc, products=[storey], relating_object=building)
    
    # --- Create IfcSpaces (rooms) ---
    for polygon, label in zip(room_polygons, room_labels):
        space = ifcopenshell.api.run(
            "root.create_entity", ifc,
            ifc_class="IfcSpace",
            name=label,
        )
        ifcopenshell.api.run(
            "spatial.assign_container", ifc,
            products=[space],
            relating_structure=storey,
        )
        
        # Create extruded solid representation for the space
        coords = list(polygon.exterior.coords[:-1])  # Remove closing point
        points = [ifc.createIfcCartesianPoint((x, y)) for x, y in coords]
        polyline = ifc.createIfcPolyline(points + [points[0]])
        profile = ifc.createIfcArbitraryClosedProfileDef("AREA", None, polyline)
        
        direction = ifc.createIfcDirection((0.0, 0.0, 1.0))
        solid = ifc.createIfcExtrudedAreaSolid(profile, None, direction, floor_height_m)
        
        shape_rep = ifc.createIfcShapeRepresentation(
            body_context, "Body", "SweptSolid", [solid]
        )
        product_shape = ifc.createIfcProductDefinitionShape(None, None, [shape_rep])
        space.Representation = product_shape
        
        # Set area property
        pset = ifcopenshell.api.run("pset.add_pset", ifc, product=space, name="Pset_SpaceCommon")
        ifcopenshell.api.run("pset.edit_pset", ifc, pset=pset, properties={"GrossFloorArea": polygon.area})
    
    # --- Create IfcWalls ---
    for wall_data in walls:
        sx, sy = wall_data["start"]["x"], wall_data["start"]["y"]
        ex, ey = wall_data["end"]["x"], wall_data["end"]["y"]
        
        wall = ifcopenshell.api.run(
            "root.create_entity", ifc,
            ifc_class="IfcWall",
            name=wall_data["id"],
        )
        ifcopenshell.api.run(
            "spatial.assign_container", ifc,
            products=[wall],
            relating_structure=storey,
        )
        
        # Wall as extruded rectangle
        import math
        length = math.hypot(ex - sx, ey - sy)
        angle = math.atan2(ey - sy, ex - sx)
        
        # Profile: rectangle (length × thickness)
        half_t = wall_thickness_m / 2
        wall_points = [
            ifc.createIfcCartesianPoint((0.0, -half_t)),
            ifc.createIfcCartesianPoint((length, -half_t)),
            ifc.createIfcCartesianPoint((length, half_t)),
            ifc.createIfcCartesianPoint((0.0, half_t)),
        ]
        wall_polyline = ifc.createIfcPolyline(wall_points + [wall_points[0]])
        wall_profile = ifc.createIfcArbitraryClosedProfileDef("AREA", None, wall_polyline)
        
        direction = ifc.createIfcDirection((0.0, 0.0, 1.0))
        wall_solid = ifc.createIfcExtrudedAreaSolid(wall_profile, None, direction, floor_height_m)
        
        wall_shape_rep = ifc.createIfcShapeRepresentation(
            body_context, "Body", "SweptSolid", [wall_solid]
        )
        wall_product_shape = ifc.createIfcProductDefinitionShape(None, None, [wall_shape_rep])
        wall.Representation = wall_product_shape
        
        # Position and rotate the wall
        origin = ifc.createIfcCartesianPoint((sx, sy, 0.0))
        dir_x = ifc.createIfcDirection((math.cos(angle), math.sin(angle), 0.0))
        dir_z = ifc.createIfcDirection((0.0, 0.0, 1.0))
        axis2 = ifc.createIfcAxis2Placement3D(origin, dir_z, dir_x)
        placement = ifc.createIfcLocalPlacement(None, axis2)
        wall.ObjectPlacement = placement
        
        # Tag exterior walls
        if wall_data.get("is_exterior"):
            pset = ifcopenshell.api.run("pset.add_pset", ifc, product=wall, name="Pset_WallCommon")
            ifcopenshell.api.run("pset.edit_pset", ifc, pset=pset, properties={"IsExternal": True})
    
    # Write file
    ifc.write(str(output_path))
    return output_path
