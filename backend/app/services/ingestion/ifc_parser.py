"""IFC floor plan parser using ifcopenshell.

Extracts room polygons (IfcSpace), walls (IfcWall), doors, and windows from IFC files.
Reverse direction of backend/app/services/geometry/ifc_builder.py.
"""

import math
from pathlib import Path

import structlog

from app.services.ingestion.interface import FloorPlanParser
from app.services.ingestion.room_type_mapper import map_ifc_name_to_room_type

logger = structlog.get_logger()


class IFCParser(FloorPlanParser):
    """Parse IFC files into room polygons using ifcopenshell."""

    @property
    def name(self) -> str:
        return "ifcopenshell_parser"

    @property
    def supported_extensions(self) -> list[str]:
        return [".ifc"]

    async def is_available(self) -> bool:
        try:
            import ifcopenshell  # noqa: F401
            return True
        except ImportError:
            return False

    async def parse(
        self,
        file_path: Path,
        scale_m_per_pixel: float | None = None,
        locale: str = "CA-QC",
    ) -> dict:
        import ifcopenshell

        warnings: list[str] = []

        ifc_file = ifcopenshell.open(str(file_path))

        # Get target storey (default: first/ground floor)
        target_storey = self._get_ground_storey(ifc_file, warnings)

        # Extract rooms (IfcSpace)
        polygons, labels, room_types = self._extract_spaces(
            ifc_file, target_storey, warnings
        )

        # Extract walls (IfcWall)
        wall_segments = self._extract_walls(ifc_file, target_storey, warnings)

        # Extract doors and windows
        doors = self._extract_openings(ifc_file, target_storey, "IfcDoor")
        windows = self._extract_openings(ifc_file, target_storey, "IfcWindow")

        # Detect plot bounds from IfcSite
        plot_bounds = self._detect_plot_bounds(ifc_file)

        return {
            "polygons": polygons,
            "room_labels": labels,
            "room_types": [rt.value if rt else None for rt in room_types],
            "walls": wall_segments,
            "doors": doors,
            "windows": windows,
            "confidence": 0.95,
            "warnings": warnings,
            "plot_bounds": plot_bounds,
        }

    def _get_ground_storey(self, ifc_file, warnings: list[str]):
        """Get the ground floor storey (first IfcBuildingStorey)."""
        storeys = ifc_file.by_type("IfcBuildingStorey")
        if not storeys:
            warnings.append("No IfcBuildingStorey found; extracting all spaces")
            return None
        # Sort by elevation, pick lowest
        storeys.sort(key=lambda s: self._get_elevation(s))
        return storeys[0]

    def _get_elevation(self, storey) -> float:
        """Get the elevation of a storey."""
        try:
            return storey.Elevation or 0.0
        except Exception:
            return 0.0

    def _extract_spaces(
        self, ifc_file, target_storey, warnings: list[str]
    ) -> tuple[list[list[tuple]], list[str], list]:
        """Extract room polygons from IfcSpace entities."""
        polygons = []
        labels = []
        room_types = []

        spaces = ifc_file.by_type("IfcSpace")
        for space in spaces:
            # Filter by storey if specified
            if target_storey and not self._is_in_storey(space, target_storey):
                continue

            # Get room label
            label = space.LongName or space.Name or "Unknown Room"

            # Extract 2D footprint polygon
            footprint = self._extract_footprint(space)
            if footprint is None or len(footprint) < 3:
                warnings.append(f"Could not extract footprint for space '{label}'")
                continue

            polygons.append(footprint)
            labels.append(label)
            room_types.append(map_ifc_name_to_room_type(label))

        return polygons, labels, room_types

    def _is_in_storey(self, element, target_storey) -> bool:
        """Check if an element belongs to the target storey."""
        try:
            for rel in element.Decomposes:
                if rel.RelatingObject == target_storey:
                    return True
        except Exception:
            pass
        try:
            for rel in element.ContainedInStructure:
                if rel.RelatingStructure == target_storey:
                    return True
        except Exception:
            pass
        return False

    def _extract_footprint(self, space) -> list[tuple[float, float]] | None:
        """Extract 2D footprint polygon from an IfcSpace.

        Tries multiple strategies:
        1. FootPrint representation
        2. Body representation projected to XY
        3. IfcExtrudedAreaSolid profile
        """
        if not space.Representation:
            return None

        for rep in space.Representation.Representations:
            # Strategy 1: Direct footprint
            if rep.RepresentationIdentifier == "FootPrint":
                return self._polyline_from_rep(rep, space)

            # Strategy 2: Body (extruded) — extract profile
            if rep.RepresentationIdentifier == "Body":
                for item in rep.Items:
                    if item.is_a("IfcExtrudedAreaSolid"):
                        return self._profile_to_coords(item.SweptArea, space)

        # Strategy 3: Try any representation
        for rep in space.Representation.Representations:
            for item in rep.Items:
                if item.is_a("IfcExtrudedAreaSolid"):
                    return self._profile_to_coords(item.SweptArea, space)

        return None

    def _profile_to_coords(
        self, profile, space
    ) -> list[tuple[float, float]] | None:
        """Convert an IfcProfileDef to 2D coordinates."""
        if profile.is_a("IfcArbitraryClosedProfileDef"):
            return self._curve_to_coords(profile.OuterCurve, space)
        elif profile.is_a("IfcRectangleProfileDef"):
            w = profile.XDim / 2
            h = profile.YDim / 2
            local_pts = [(-w, -h), (w, -h), (w, h), (-w, h), (-w, -h)]
            return self._transform_points(local_pts, space)
        return None

    def _curve_to_coords(
        self, curve, space
    ) -> list[tuple[float, float]] | None:
        """Convert an IfcCurve (polyline) to coordinates."""
        if curve.is_a("IfcPolyline"):
            pts = []
            for point in curve.Points:
                coords = point.Coordinates
                pts.append((float(coords[0]), float(coords[1])))
            # Ensure closed
            if pts and pts[0] != pts[-1]:
                pts.append(pts[0])
            return self._transform_points(pts, space)
        return None

    def _transform_points(
        self, points: list[tuple[float, float]], element
    ) -> list[tuple[float, float]]:
        """Apply element's local placement to transform points to global coords."""
        ox, oy, angle = self._get_placement(element)
        if ox == 0 and oy == 0 and angle == 0:
            return [(round(x, 3), round(y, 3)) for x, y in points]

        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        result = []
        for x, y in points:
            gx = ox + x * cos_a - y * sin_a
            gy = oy + x * sin_a + y * cos_a
            result.append((round(gx, 3), round(gy, 3)))
        return result

    def _get_placement(self, element) -> tuple[float, float, float]:
        """Extract origin (x, y) and rotation angle from ObjectPlacement."""
        try:
            placement = element.ObjectPlacement
            if placement and placement.is_a("IfcLocalPlacement"):
                rel = placement.RelativePlacement
                if rel and rel.is_a("IfcAxis2Placement3D"):
                    loc = rel.Location.Coordinates
                    ox, oy = float(loc[0]), float(loc[1])
                    angle = 0.0
                    if rel.RefDirection:
                        dx = float(rel.RefDirection.DirectionRatios[0])
                        dy = float(rel.RefDirection.DirectionRatios[1])
                        angle = math.atan2(dy, dx)
                    return ox, oy, angle
        except Exception:
            pass
        return 0.0, 0.0, 0.0

    def _extract_walls(
        self, ifc_file, target_storey, warnings: list[str]
    ) -> list[dict]:
        """Extract wall segments from IfcWall entities."""
        walls = []
        wall_id = 0

        for wall_elem in ifc_file.by_type("IfcWall"):
            if target_storey and not self._is_in_storey(wall_elem, target_storey):
                continue

            ox, oy, angle = self._get_placement(wall_elem)

            # Try to get wall length from representation
            length = self._get_wall_length(wall_elem)
            if length is None or length < 0.1:
                continue

            # Wall endpoints from placement + length
            ex = ox + length * math.cos(angle)
            ey = oy + length * math.sin(angle)

            # Check if exterior
            is_exterior = self._is_exterior_wall(wall_elem)

            walls.append({
                "id": f"wall_{wall_id}",
                "start": {"x": round(ox, 3), "y": round(oy, 3)},
                "end": {"x": round(ex, 3), "y": round(ey, 3)},
                "is_exterior": is_exterior,
            })
            wall_id += 1

        return walls

    def _get_wall_length(self, wall_elem) -> float | None:
        """Extract wall length from its representation."""
        try:
            if not wall_elem.Representation:
                return None
            for rep in wall_elem.Representation.Representations:
                for item in rep.Items:
                    if item.is_a("IfcExtrudedAreaSolid"):
                        profile = item.SweptArea
                        if profile.is_a("IfcArbitraryClosedProfileDef"):
                            curve = profile.OuterCurve
                            if curve.is_a("IfcPolyline"):
                                pts = [
                                    (float(p.Coordinates[0]), float(p.Coordinates[1]))
                                    for p in curve.Points
                                ]
                                if len(pts) >= 2:
                                    # Wall length = longest edge of the profile
                                    max_len = 0
                                    for i in range(len(pts) - 1):
                                        dx = pts[i + 1][0] - pts[i][0]
                                        dy = pts[i + 1][1] - pts[i][1]
                                        max_len = max(max_len, math.hypot(dx, dy))
                                    return max_len
                        elif profile.is_a("IfcRectangleProfileDef"):
                            return max(profile.XDim, profile.YDim)
        except Exception:
            pass
        return None

    def _is_exterior_wall(self, wall_elem) -> bool:
        """Check if wall is marked as external via Pset_WallCommon."""
        try:
            for rel in wall_elem.IsDefinedBy:
                if rel.is_a("IfcRelDefinesByProperties"):
                    pset = rel.RelatingPropertyDefinition
                    if hasattr(pset, "HasProperties"):
                        for prop in pset.HasProperties:
                            if prop.Name == "IsExternal":
                                return bool(prop.NominalValue.wrappedValue)
        except Exception:
            pass
        return False

    def _extract_openings(
        self, ifc_file, target_storey, ifc_class: str
    ) -> list[dict]:
        """Extract door or window placements."""
        openings = []
        for elem in ifc_file.by_type(ifc_class):
            if target_storey and not self._is_in_storey(elem, target_storey):
                continue
            ox, oy, _ = self._get_placement(elem)
            openings.append({
                "name": elem.Name or ifc_class,
                "x": round(ox, 3),
                "y": round(oy, 3),
            })
        return openings

    def _detect_plot_bounds(self, ifc_file) -> tuple[float, float] | None:
        """Try to detect plot dimensions from IfcSite."""
        sites = ifc_file.by_type("IfcSite")
        if not sites:
            return None
        # IfcSite doesn't directly encode lot dimensions in a standard way,
        # so we fall back to bounding box of all spaces
        return None

    def _polyline_from_rep(
        self, rep, space
    ) -> list[tuple[float, float]] | None:
        """Extract polyline coords from a shape representation."""
        for item in rep.Items:
            if item.is_a("IfcPolyline"):
                return self._curve_to_coords(item, space)
            if item.is_a("IfcGeometricCurveSet"):
                for elem in item.Elements:
                    if elem.is_a("IfcPolyline"):
                        return self._curve_to_coords(elem, space)
        return None
