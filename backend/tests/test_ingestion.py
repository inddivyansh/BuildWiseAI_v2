"""Tests for ingestion pipeline — DXF parser, IFC parser, pipeline integration."""

import io
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.schemas.floor_plan import RoomType


# --- DXF Parser Tests ---

class TestDXFParser:
    """Test DXF parsing by creating a test DXF file with ezdxf."""

    @pytest.fixture
    def sample_dxf_path(self, tmp_path):
        """Create a simple DXF file with room polylines."""
        import ezdxf

        doc = ezdxf.new("R2010")
        msp = doc.modelspace()

        # Add layers
        doc.layers.add("ROOMS", color=7)
        doc.layers.add("WALLS_EXTERIOR", color=1)
        doc.layers.add("ANNOTATIONS", color=7)
        doc.layers.add("PLOT", color=8)

        # Room 1: Kitchen (4x3m)
        msp.add_lwpolyline(
            [(0, 0), (4, 0), (4, 3), (0, 3), (0, 0)],
            dxfattribs={"layer": "ROOMS"},
            close=True,
        )

        # Room 2: Living Room (5x4m)
        msp.add_lwpolyline(
            [(4, 0), (9, 0), (9, 4), (4, 4), (4, 0)],
            dxfattribs={"layer": "ROOMS"},
            close=True,
        )

        # Room 3: Bedroom (4x4m)
        msp.add_lwpolyline(
            [(0, 3), (4, 3), (4, 7), (0, 7), (0, 3)],
            dxfattribs={"layer": "ROOMS"},
            close=True,
        )

        # Room labels as TEXT entities
        from ezdxf.enums import TextEntityAlignment
        msp.add_text(
            "Kitchen", height=0.3,
            dxfattribs={"layer": "ANNOTATIONS"},
        ).set_placement((2, 1.5), align=TextEntityAlignment.MIDDLE_CENTER)

        msp.add_text(
            "Living Room", height=0.3,
            dxfattribs={"layer": "ANNOTATIONS"},
        ).set_placement((6.5, 2), align=TextEntityAlignment.MIDDLE_CENTER)

        msp.add_text(
            "Bedroom", height=0.3,
            dxfattribs={"layer": "ANNOTATIONS"},
        ).set_placement((2, 5), align=TextEntityAlignment.MIDDLE_CENTER)

        # Plot boundary
        msp.add_lwpolyline(
            [(0, 0), (12, 0), (12, 10), (0, 10), (0, 0)],
            dxfattribs={"layer": "PLOT"},
        )

        # Walls
        msp.add_line(
            (0, 0), (9, 0),
            dxfattribs={"layer": "WALLS_EXTERIOR"},
        )
        msp.add_line(
            (0, 0), (0, 7),
            dxfattribs={"layer": "WALLS_EXTERIOR"},
        )

        path = tmp_path / "test_plan.dxf"
        doc.saveas(str(path))
        return path

    @pytest.mark.asyncio
    async def test_parse_dxf(self, sample_dxf_path):
        from app.services.ingestion.dxf_parser import DXFParser

        parser = DXFParser()
        assert parser.name == "ezdxf_parser"
        assert ".dxf" in parser.supported_extensions
        assert await parser.is_available()

        result = await parser.parse(sample_dxf_path)

        assert result["confidence"] == 0.85
        assert len(result["polygons"]) == 3
        assert len(result["room_labels"]) == 3
        assert len(result["room_types"]) == 3

        # Check room types were mapped
        types = result["room_types"]
        assert RoomType.KITCHEN.value in types
        assert RoomType.LIVING.value in types
        assert RoomType.BEDROOM.value in types

        # Check walls were extracted
        assert len(result["walls"]) >= 2

        # Check plot bounds detected
        assert result["plot_bounds"] is not None
        pw, pd = result["plot_bounds"]
        assert pw == pytest.approx(12.0, abs=0.1)
        assert pd == pytest.approx(10.0, abs=0.1)


# --- IFC Parser Tests ---

class TestIFCParser:
    """Test IFC parsing using ifc_builder to create a test file, then parse it back."""

    @pytest.fixture
    def sample_ifc_path(self, tmp_path):
        """Create a test IFC file using the existing ifc_builder."""
        try:
            from shapely.geometry import Polygon
            from app.services.geometry.ifc_builder import create_ifc_from_floor_plan

            kitchen = Polygon([(0, 0), (4, 0), (4, 3), (0, 3), (0, 0)])
            living = Polygon([(4, 0), (9, 0), (9, 4), (4, 4), (4, 0)])
            bedroom = Polygon([(0, 3), (4, 3), (4, 7), (0, 7), (0, 3)])

            walls = [
                {"id": "w0", "start": {"x": 0, "y": 0}, "end": {"x": 9, "y": 0}, "is_exterior": True},
                {"id": "w1", "start": {"x": 0, "y": 0}, "end": {"x": 0, "y": 7}, "is_exterior": True},
            ]

            path = tmp_path / "test_plan.ifc"
            create_ifc_from_floor_plan(
                room_polygons=[kitchen, living, bedroom],
                room_labels=["Kitchen", "Living Room", "Bedroom"],
                walls=walls,
                output_path=path,
            )
            return path
        except ImportError:
            pytest.skip("ifcopenshell not available")

    @pytest.mark.asyncio
    async def test_parse_ifc(self, sample_ifc_path):
        from app.services.ingestion.ifc_parser import IFCParser

        parser = IFCParser()
        assert parser.name == "ifcopenshell_parser"
        assert ".ifc" in parser.supported_extensions

        if not await parser.is_available():
            pytest.skip("ifcopenshell not available")

        result = await parser.parse(sample_ifc_path)

        assert result["confidence"] == 0.95
        assert len(result["polygons"]) == 3
        assert len(result["room_labels"]) == 3

        # Verify labels round-tripped
        labels = result["room_labels"]
        assert "Kitchen" in labels
        assert "Living Room" in labels
        assert "Bedroom" in labels

        # Room types should be mapped
        types = result["room_types"]
        assert RoomType.KITCHEN.value in types
        assert RoomType.LIVING.value in types
        assert RoomType.BEDROOM.value in types


# --- Pipeline Integration (Mocked Parser) ---

class TestIngestionPipeline:
    """Test pipeline.ingest_from_file() with mocked parser."""

    @pytest.fixture
    def mock_parsed_data(self):
        return {
            "polygons": [
                [(0, 0), (4, 0), (4, 3), (0, 3), (0, 0)],
                [(4, 0), (9, 0), (9, 4), (4, 4), (4, 0)],
                [(0, 3), (4, 3), (4, 7), (0, 7), (0, 3)],
            ],
            "room_labels": ["Kitchen", "Living Room", "Bedroom"],
            "room_types": ["kitchen", "living", "bedroom"],
            "walls": [],
            "doors": [],
            "windows": [],
            "confidence": 0.85,
            "warnings": [],
            "plot_bounds": (12.0, 10.0),
        }

    @pytest.mark.asyncio
    async def test_ingest_from_file_builds_spec(self, tmp_path, mock_parsed_data):
        from app.core.pipeline import GenerationPipeline

        pipeline = GenerationPipeline()

        # Create a mock parser
        mock_parser = AsyncMock()
        mock_parser.name = "mock_parser"
        mock_parser.is_available = AsyncMock(return_value=True)
        mock_parser.parse = AsyncMock(return_value=mock_parsed_data)

        pipeline.parsers["dxf"] = mock_parser

        # Create dummy file
        dummy_file = tmp_path / "test.dxf"
        dummy_file.write_text("dummy")

        result = await pipeline.ingest_from_file(
            file_path=dummy_file,
            file_type="dxf",
            locale="CA-QC",
        )

        assert result["generation_id"] is not None
        assert result["confidence"] == 0.85
        assert result["floor_plan"] is not None

        fp = result["floor_plan"]
        assert len(fp.spec.rooms) == 3
        assert fp.spec.rooms[0].type == RoomType.KITCHEN
        assert fp.spec.rooms[1].type == RoomType.LIVING
        assert fp.spec.rooms[2].type == RoomType.BEDROOM

        # Check areas were computed
        for room in fp.spec.rooms:
            assert room.actual_area_sqm is not None
            assert room.actual_area_sqm > 0

        # Check plot dimensions inferred
        assert fp.spec.plot.width_m == pytest.approx(15.0, abs=1.0)
        assert fp.spec.plot.depth_m == pytest.approx(13.0, abs=1.0)

    @pytest.mark.asyncio
    async def test_ingest_no_parser(self, tmp_path):
        from app.core.pipeline import GenerationPipeline

        pipeline = GenerationPipeline()

        dummy_file = tmp_path / "test.xyz"
        dummy_file.write_text("dummy")

        result = await pipeline.ingest_from_file(
            file_path=dummy_file,
            file_type="xyz_unknown",
        )

        assert result["floor_plan"] is None
        assert any("No parser" in w for w in result["warnings"])

    @pytest.mark.asyncio
    async def test_ingest_empty_result(self, tmp_path):
        from app.core.pipeline import GenerationPipeline

        pipeline = GenerationPipeline()

        mock_parser = AsyncMock()
        mock_parser.name = "mock_empty"
        mock_parser.is_available = AsyncMock(return_value=True)
        mock_parser.parse = AsyncMock(return_value={
            "polygons": [],
            "room_labels": [],
            "room_types": [],
            "walls": [],
            "doors": [],
            "windows": [],
            "confidence": 0.1,
            "warnings": ["Nothing found"],
            "plot_bounds": None,
        })
        pipeline.parsers["test"] = mock_parser

        dummy_file = tmp_path / "empty.dxf"
        dummy_file.write_text("dummy")

        result = await pipeline.ingest_from_file(file_path=dummy_file, file_type="test")

        assert result["floor_plan"] is None
        assert any("No room polygons" in w for w in result["warnings"])


# --- Image Parser Tests (Mocked Modal) ---

class TestImageParser:
    """Test ImageParser with mocked Modal service."""

    @pytest.mark.asyncio
    async def test_opencv_fallback(self, tmp_path):
        """Test that OpenCV fallback works on a simple black/white image."""
        try:
            import cv2
            import numpy as np
        except ImportError:
            pytest.skip("opencv not available")

        from app.services.ingestion.image_parser import ImageParser

        # Create a simple test image with rectangles (rooms)
        img = np.ones((500, 500, 3), dtype=np.uint8) * 255  # white background

        # Draw black room boundaries
        cv2.rectangle(img, (50, 50), (200, 200), (0, 0, 0), 3)
        cv2.rectangle(img, (200, 50), (400, 200), (0, 0, 0), 3)
        cv2.rectangle(img, (50, 200), (200, 400), (0, 0, 0), 3)

        img_path = tmp_path / "test_plan.png"
        cv2.imwrite(str(img_path), img)

        parser = ImageParser()
        assert parser.name == "image_parser"
        assert await parser.is_available()

        # Should fall back to OpenCV since no Modal service
        result = await parser.parse(img_path, scale_m_per_pixel=0.02)

        assert result["confidence"] <= 0.5  # Low confidence for OpenCV
        assert "OpenCV" in " ".join(result["warnings"])
        # May or may not detect rooms depending on contour detection
        assert isinstance(result["polygons"], list)


# --- Scale Detection Tests ---

class TestScaleDetection:
    def test_estimate_from_known_dimension(self):
        from app.services.ingestion.scale_detection import estimate_scale_from_known_dimension

        scale = estimate_scale_from_known_dimension(100, 5.0)
        assert scale == pytest.approx(0.05)

    def test_estimate_from_room_size(self):
        from app.services.ingestion.scale_detection import estimate_scale_from_typical_room_size

        # 10000 px^2 rooms, assume 12 sqm average
        scale = estimate_scale_from_typical_room_size([10000], assumed_avg_area_sqm=12.0)
        assert scale == pytest.approx(0.03464, abs=0.001)

    def test_estimate_from_image_width(self):
        from app.services.ingestion.scale_detection import estimate_scale_from_image_width

        scale = estimate_scale_from_image_width(1000, assumed_plan_width_m=10.0)
        assert scale == pytest.approx(0.01)

    def test_invalid_pixel_length(self):
        from app.services.ingestion.scale_detection import estimate_scale_from_known_dimension

        with pytest.raises(ValueError):
            estimate_scale_from_known_dimension(0, 5.0)


# --- File Type Detection ---

class TestFileTypeDetection:
    def test_detect_dxf(self):
        from app.core.pipeline import GenerationPipeline

        pipeline = GenerationPipeline()
        assert pipeline.detect_file_type(Path("plan.dxf")) == "dxf"

    def test_detect_ifc(self):
        from app.core.pipeline import GenerationPipeline

        pipeline = GenerationPipeline()
        assert pipeline.detect_file_type(Path("model.ifc")) == "ifc"

    def test_detect_png(self):
        from app.core.pipeline import GenerationPipeline

        pipeline = GenerationPipeline()
        assert pipeline.detect_file_type(Path("scan.png")) == "image"

    def test_detect_jpg(self):
        from app.core.pipeline import GenerationPipeline

        pipeline = GenerationPipeline()
        assert pipeline.detect_file_type(Path("photo.jpg")) == "image"

    def test_detect_unknown(self):
        from app.core.pipeline import GenerationPipeline

        pipeline = GenerationPipeline()
        assert pipeline.detect_file_type(Path("file.xyz")) == "image"  # fallback
