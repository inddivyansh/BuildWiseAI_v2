"""Tests for IFC BIM file generation."""

import tempfile
from pathlib import Path

import pytest
from shapely.geometry import box

from app.services.geometry.cleanup import extract_wall_segments

try:
    from app.services.geometry.ifc_builder import create_ifc_from_floor_plan
    HAS_IFC = True
except ImportError:
    HAS_IFC = False

pytestmark = pytest.mark.skipif(not HAS_IFC, reason="ifcopenshell not available on this platform")


class TestIFCBuilder:
    def _make_simple_plan(self):
        """Two adjacent rooms: 5x4m and 4x4m."""
        rooms = [box(0, 0, 5, 4), box(5, 0, 9, 4)]
        labels = ["Living Room", "Kitchen"]
        walls = extract_wall_segments(rooms)
        return rooms, labels, walls

    def test_creates_ifc_file(self):
        rooms, labels, walls = self._make_simple_plan()
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.ifc"
            result = create_ifc_from_floor_plan(rooms, labels, walls, output_path=output)
            assert result.exists()
            assert result.stat().st_size > 0

    def test_ifc_file_is_valid(self):
        rooms, labels, walls = self._make_simple_plan()
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.ifc"
            create_ifc_from_floor_plan(rooms, labels, walls, output_path=output)

            import ifcopenshell
            ifc = ifcopenshell.open(str(output))

            # Check basic IFC structure
            assert ifc.by_type("IfcProject")
            assert ifc.by_type("IfcSite")
            assert ifc.by_type("IfcBuilding")
            assert ifc.by_type("IfcBuildingStorey")

    def test_correct_number_of_spaces(self):
        rooms, labels, walls = self._make_simple_plan()
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.ifc"
            create_ifc_from_floor_plan(rooms, labels, walls, output_path=output)

            import ifcopenshell
            ifc = ifcopenshell.open(str(output))

            spaces = ifc.by_type("IfcSpace")
            assert len(spaces) == 2
            space_names = {s.Name for s in spaces}
            assert "Living Room" in space_names
            assert "Kitchen" in space_names

    def test_walls_created(self):
        rooms, labels, walls = self._make_simple_plan()
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.ifc"
            create_ifc_from_floor_plan(rooms, labels, walls, output_path=output)

            import ifcopenshell
            ifc = ifcopenshell.open(str(output))

            ifc_walls = ifc.by_type("IfcWall")
            assert len(ifc_walls) == len(walls)

    def test_exterior_walls_tagged(self):
        rooms, labels, walls = self._make_simple_plan()
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.ifc"
            create_ifc_from_floor_plan(rooms, labels, walls, output_path=output)

            import ifcopenshell
            ifc = ifcopenshell.open(str(output))

            # At least some walls should have Pset_WallCommon with IsExternal
            psets = ifc.by_type("IfcPropertySet")
            wall_common = [p for p in psets if p.Name == "Pset_WallCommon"]
            assert len(wall_common) > 0

    def test_custom_floor_height(self):
        rooms, labels, walls = self._make_simple_plan()
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.ifc"
            create_ifc_from_floor_plan(
                rooms, labels, walls, floor_height_m=3.0, output_path=output
            )

            import ifcopenshell
            ifc = ifcopenshell.open(str(output))
            assert ifc.by_type("IfcProject")

    def test_single_room(self):
        rooms = [box(0, 0, 5, 4)]
        labels = ["Studio"]
        walls = extract_wall_segments(rooms)
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.ifc"
            create_ifc_from_floor_plan(rooms, labels, walls, output_path=output)

            import ifcopenshell
            ifc = ifcopenshell.open(str(output))
            assert len(ifc.by_type("IfcSpace")) == 1

    def test_three_room_layout(self):
        """L-shaped layout: 3 rooms."""
        rooms = [
            box(0, 0, 5, 4),   # Living
            box(5, 0, 9, 4),   # Kitchen
            box(0, 4, 9, 8),   # Master Bedroom
        ]
        labels = ["Living Room", "Kitchen", "Master Bedroom"]
        walls = extract_wall_segments(rooms)
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "test.ifc"
            create_ifc_from_floor_plan(rooms, labels, walls, output_path=output)

            import ifcopenshell
            ifc = ifcopenshell.open(str(output))
            assert len(ifc.by_type("IfcSpace")) == 3
            assert len(ifc.by_type("IfcWall")) == len(walls)
