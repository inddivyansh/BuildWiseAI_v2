"""DXF floor plan parser using ezdxf.

Extracts room polygons, wall lines, and room labels from DXF files.
Reverse direction of backend/app/services/export/dxf_exporter.py.
"""

from pathlib import Path

import structlog
from shapely.geometry import Polygon as ShapelyPolygon

from app.services.ingestion.interface import FloorPlanParser
from app.services.ingestion.room_type_mapper import (
    map_dxf_layer_to_room_type,
    map_label_to_room_type,
)

logger = structlog.get_logger()

# DXF unit codes ($INSUNITS header variable)
_UNIT_TO_METERS: dict[int, float] = {
    0: 1.0,      # Unitless — assume meters
    1: 0.0254,   # Inches
    2: 0.3048,   # Feet
    3: 1609.344,  # Miles
    4: 0.001,    # Millimeters
    5: 0.01,     # Centimeters
    6: 1.0,      # Meters
    7: 1000.0,   # Kilometers
    8: 0.0000254,  # Microinches
    9: 0.001,    # Mils
    10: 0.9144,  # Yards
    14: 0.01,    # Decimeters
}

# Keywords that indicate room-containing layers
_ROOM_LAYER_KEYWORDS = [
    "room", "space", "area", "zone", "piece", "pièce",
    "bedroom", "kitchen", "living", "bath", "office",
    "chambre", "cuisine", "salon", "sdb", "bureau",
]

# Keywords that indicate wall-containing layers
_WALL_LAYER_KEYWORDS = [
    "wall", "mur", "partition", "cloison", "exterior", "interior",
]


class DXFParser(FloorPlanParser):
    """Parse DXF files into room polygons using ezdxf."""

    @property
    def name(self) -> str:
        return "ezdxf_parser"

    @property
    def supported_extensions(self) -> list[str]:
        return [".dxf"]

    async def is_available(self) -> bool:
        try:
            import ezdxf  # noqa: F401
            return True
        except ImportError:
            return False

    async def parse(
        self,
        file_path: Path,
        scale_m_per_pixel: float | None = None,
        locale: str = "CA-QC",
    ) -> dict:
        import ezdxf

        warnings: list[str] = []

        doc = ezdxf.readfile(str(file_path))
        msp = doc.modelspace()

        # Determine unit conversion factor
        unit_scale = self._get_unit_scale(doc)
        logger.info("dxf_units_detected", unit_scale=unit_scale)

        # Scan layers
        all_layers = {layer.dxf.name: layer for layer in doc.layers}
        room_layers = self._find_room_layers(all_layers)
        wall_layers = self._find_wall_layers(all_layers)

        # Extract room polygons
        polygons_raw, labels, sources = self._extract_rooms(
            msp, room_layers, unit_scale, warnings
        )

        # If no room-specific layers found, try all closed polylines
        if not polygons_raw:
            warnings.append("No room layers detected; using all closed polylines with area > 2 sqm")
            polygons_raw, labels, sources = self._extract_all_closed_polylines(
                msp, unit_scale, min_area=2.0
            )

        # Extract text entities for room labels
        text_labels = self._extract_text_labels(msp, unit_scale)

        # Match text labels to nearest polygon centroid
        for i, (poly, label) in enumerate(zip(polygons_raw, labels)):
            if label:
                continue
            centroid = ShapelyPolygon(poly).centroid
            best_label, best_dist = None, float("inf")
            for text, tx, ty in text_labels:
                dist = ((centroid.x - tx) ** 2 + (centroid.y - ty) ** 2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_label = text
            # Only assign if text is within ~3m of centroid
            if best_label and best_dist < 3.0:
                labels[i] = best_label

        # Map labels to room types
        room_types: list[str | None] = []
        for i, label in enumerate(labels):
            rt = None
            if label:
                rt = map_label_to_room_type(label)
            if rt is None and sources[i]:
                rt = map_dxf_layer_to_room_type(sources[i])
            room_types.append(rt.value if rt else None)

        # Fill in missing labels from room types
        for i, (label, rt) in enumerate(zip(labels, room_types)):
            if not label and rt:
                labels[i] = rt.replace("_", " ").title()
            elif not label:
                labels[i] = f"Room {i + 1}"

        # Extract walls
        wall_segments = self._extract_walls(msp, wall_layers, unit_scale)

        # Detect plot bounds from PLOT layer or bounding box
        plot_bounds = self._detect_plot_bounds(msp, unit_scale)

        return {
            "polygons": polygons_raw,
            "room_labels": labels,
            "room_types": room_types,
            "walls": wall_segments,
            "doors": [],
            "windows": [],
            "confidence": 0.85,
            "warnings": warnings,
            "plot_bounds": plot_bounds,
        }

    def _get_unit_scale(self, doc) -> float:
        """Get meters-per-unit conversion from DXF header."""
        try:
            insunits = doc.header.get("$INSUNITS", 0)
            return _UNIT_TO_METERS.get(insunits, 1.0)
        except Exception:
            return 1.0

    def _find_room_layers(self, layers: dict) -> set[str]:
        """Find layers that likely contain room geometry."""
        result = set()
        for name in layers:
            lower = name.lower()
            for kw in _ROOM_LAYER_KEYWORDS:
                if kw in lower:
                    result.add(name)
                    break
        # Also include "ROOMS" layer from our own DXF exporter
        if "ROOMS" in layers:
            result.add("ROOMS")
        return result

    def _find_wall_layers(self, layers: dict) -> set[str]:
        """Find layers that likely contain wall geometry."""
        result = set()
        for name in layers:
            lower = name.lower()
            for kw in _WALL_LAYER_KEYWORDS:
                if kw in lower:
                    result.add(name)
                    break
        for name in ["WALLS_EXTERIOR", "WALLS_INTERIOR"]:
            if name in layers:
                result.add(name)
        return result

    def _extract_rooms(
        self, msp, room_layers: set[str], unit_scale: float, warnings: list[str]
    ) -> tuple[list[list[tuple]], list[str], list[str]]:
        """Extract room polygons from room-specific layers."""
        polygons = []
        labels = []
        sources = []

        for entity in msp:
            if entity.dxf.layer not in room_layers:
                continue
            if entity.dxftype() == "LWPOLYLINE":
                pts = self._lwpolyline_to_coords(entity, unit_scale)
                if len(pts) >= 3:
                    poly = ShapelyPolygon(pts)
                    if poly.is_valid and poly.area > 0.5:  # > 0.5 sqm
                        polygons.append(pts)
                        labels.append("")  # Fill from text entities later
                        sources.append(entity.dxf.layer)
            elif entity.dxftype() in ("POLYLINE", "3DFACE"):
                pts = self._polyline_to_coords(entity, unit_scale)
                if len(pts) >= 3:
                    poly = ShapelyPolygon(pts)
                    if poly.is_valid and poly.area > 0.5:
                        polygons.append(pts)
                        labels.append("")
                        sources.append(entity.dxf.layer)

        return polygons, labels, sources

    def _extract_all_closed_polylines(
        self, msp, unit_scale: float, min_area: float = 2.0
    ) -> tuple[list[list[tuple]], list[str], list[str]]:
        """Fallback: extract all closed polylines with area > min_area."""
        polygons = []
        labels = []
        sources = []

        for entity in msp:
            if entity.dxftype() == "LWPOLYLINE":
                if not entity.closed:
                    continue
                pts = self._lwpolyline_to_coords(entity, unit_scale)
                if len(pts) >= 3:
                    poly = ShapelyPolygon(pts)
                    if poly.is_valid and poly.area >= min_area:
                        polygons.append(pts)
                        labels.append("")
                        sources.append(entity.dxf.layer)

        return polygons, labels, sources

    def _extract_text_labels(
        self, msp, unit_scale: float
    ) -> list[tuple[str, float, float]]:
        """Extract text entities with their positions."""
        texts = []
        for entity in msp:
            if entity.dxftype() in ("TEXT", "MTEXT"):
                try:
                    if entity.dxftype() == "TEXT":
                        text = entity.dxf.text
                        x, y = entity.dxf.insert.x * unit_scale, entity.dxf.insert.y * unit_scale
                    else:
                        text = entity.text
                        x, y = entity.dxf.insert.x * unit_scale, entity.dxf.insert.y * unit_scale
                    if text and len(text.strip()) > 1:
                        # Filter out dimension text and area annotations
                        stripped = text.strip()
                        if not stripped.replace(".", "").replace(",", "").replace(" ", "").isdigit():
                            if "m²" not in stripped and "sqm" not in stripped.lower():
                                texts.append((stripped, x, y))
                except Exception:
                    continue
        return texts

    def _extract_walls(
        self, msp, wall_layers: set[str], unit_scale: float
    ) -> list[dict]:
        """Extract wall line segments from wall layers."""
        walls = []
        wall_id = 0

        for entity in msp:
            if entity.dxf.layer not in wall_layers:
                continue
            if entity.dxftype() == "LINE":
                sx = entity.dxf.start.x * unit_scale
                sy = entity.dxf.start.y * unit_scale
                ex = entity.dxf.end.x * unit_scale
                ey = entity.dxf.end.y * unit_scale
                is_ext = "exterior" in entity.dxf.layer.lower() or "ext" in entity.dxf.layer.lower()
                walls.append({
                    "id": f"wall_{wall_id}",
                    "start": {"x": round(sx, 3), "y": round(sy, 3)},
                    "end": {"x": round(ex, 3), "y": round(ey, 3)},
                    "is_exterior": is_ext,
                })
                wall_id += 1

        return walls

    def _detect_plot_bounds(self, msp, unit_scale: float) -> tuple[float, float] | None:
        """Try to detect plot dimensions from PLOT layer or bounding box."""
        # Check for PLOT layer polyline
        for entity in msp:
            if entity.dxf.layer == "PLOT" and entity.dxftype() == "LWPOLYLINE":
                pts = self._lwpolyline_to_coords(entity, unit_scale)
                if len(pts) >= 4:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    return (max(xs) - min(xs), max(ys) - min(ys))
        return None

    def _lwpolyline_to_coords(
        self, entity, unit_scale: float
    ) -> list[tuple[float, float]]:
        """Convert LWPOLYLINE entity to coordinate list."""
        pts = []
        for x, y, *_ in entity.get_points():
            pts.append((round(x * unit_scale, 3), round(y * unit_scale, 3)))
        # Ensure closed
        if pts and pts[0] != pts[-1]:
            pts.append(pts[0])
        return pts

    def _polyline_to_coords(
        self, entity, unit_scale: float
    ) -> list[tuple[float, float]]:
        """Convert POLYLINE/3DFACE entity to coordinate list."""
        pts = []
        try:
            for vertex in entity.vertices:
                x, y = vertex.dxf.location.x, vertex.dxf.location.y
                pts.append((round(x * unit_scale, 3), round(y * unit_scale, 3)))
        except Exception:
            return []
        if pts and pts[0] != pts[-1]:
            pts.append(pts[0])
        return pts
