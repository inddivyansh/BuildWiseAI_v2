"""Image floor plan parser with CubiCasa5k (Modal GPU) and OpenCV CPU fallback.

Two paths:
1. Modal CubiCasa (GPU): semantic segmentation -> room polygons (high confidence)
2. OpenCV fallback (CPU): contour detection -> room polygons (low confidence)
"""

from pathlib import Path

import numpy as np
import structlog

from app.services.ingestion.interface import FloorPlanParser
from app.services.ingestion.room_type_mapper import (
    map_cubicasa_class_to_room_type,
    cubicasa_class_label,
)
from app.services.ingestion.scale_detection import (
    detect_image_dpi,
    estimate_scale_from_image_width,
    estimate_scale_from_typical_room_size,
)

logger = structlog.get_logger()


class ImageParser(FloorPlanParser):
    """Parse floor plan images using CubiCasa5k (Modal) or OpenCV fallback."""

    def __init__(self):
        self._modal_cls = None
        self._modal_checked = False

    @property
    def name(self) -> str:
        return "image_parser"

    @property
    def supported_extensions(self) -> list[str]:
        return [".png", ".jpg", ".jpeg", ".tiff", ".bmp", ".webp", ".pdf"]

    async def is_available(self) -> bool:
        return True  # OpenCV fallback always available

    async def parse(
        self,
        file_path: Path,
        scale_m_per_pixel: float | None = None,
        locale: str = "CA-QC",
    ) -> dict:
        warnings: list[str] = []

        # Read image (or rasterize first page of PDF)
        import cv2
        from PIL import Image

        img = None
        if file_path.suffix.lower() == ".pdf":
            try:
                import fitz
            except ImportError:
                try:
                    import pymupdf as fitz
                except ImportError:
                    fitz = None

            if fitz is not None:
                try:
                    doc = fitz.open(str(file_path))
                    if len(doc) == 0:
                        return {
                            "polygons": [],
                            "room_labels": [],
                            "room_types": [],
                            "walls": [],
                            "doors": [],
                            "windows": [],
                            "confidence": 0.0,
                            "warnings": ["Empty PDF file"],
                            "plot_bounds": None,
                        }
                    page = doc[0]
                    pix = page.get_pixmap(dpi=150)
                    img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
                    doc.close()
                except Exception as e:
                    warnings.append(f"Failed to render PDF page: {e}")
                    img = None
            else:
                warnings.append("PyMuPDF (fitz) is not installed to render PDF floor plans")
                img = None
        else:
            img = cv2.imread(str(file_path))
        if img is None:
            return {
                "polygons": [],
                "room_labels": [],
                "room_types": [],
                "walls": [],
                "doors": [],
                "windows": [],
                "confidence": 0.0,
                "warnings": ["Failed to read image file"],
                "plot_bounds": None,
            }

        h, w = img.shape[:2]

        # Determine scale
        if scale_m_per_pixel is None:
            scale_m_per_pixel = detect_image_dpi(file_path)
        if scale_m_per_pixel is None:
            # Will be refined after room detection
            scale_m_per_pixel = estimate_scale_from_image_width(w)
            warnings.append(
                f"No scale provided; estimated {scale_m_per_pixel:.5f} m/px from image width"
            )

        # Try CubiCasa Modal first
        cubicasa_result = await self._try_cubicasa_modal(file_path, scale_m_per_pixel)
        if cubicasa_result is not None:
            return cubicasa_result

        # Fallback to OpenCV
        logger.info("cubicasa_unavailable_using_opencv_fallback")
        warnings.append("CubiCasa model unavailable; using OpenCV contour detection (lower accuracy)")
        return self._opencv_fallback(img, scale_m_per_pixel, warnings)

    async def _try_cubicasa_modal(
        self, file_path: Path, scale_m_per_pixel: float
    ) -> dict | None:
        """Try to use the CubiCasa5k Modal service for segmentation."""
        try:
            if not self._modal_checked:
                self._modal_checked = True
                import modal
                self._modal_cls = modal.Cls.from_name(
                    "archigen-ingestion", "CubiCasaIngestionService"
                )

            if self._modal_cls is None:
                return None

            # Health check
            service = self._modal_cls()
            health = await service.health.remote.aio()
            if not health.get("model_loaded"):
                logger.warning("cubicasa_modal_not_ready")
                return None

            # Read image bytes
            image_bytes = file_path.read_bytes()

            # Call segmentation
            result = await service.segment.remote.aio(
                image_bytes=image_bytes,
                scale_m_per_pixel=scale_m_per_pixel,
            )

            # Convert CubiCasa class IDs to room types
            room_types = []
            room_labels = []
            for class_id in result.get("room_class_ids", []):
                rt = map_cubicasa_class_to_room_type(class_id)
                room_types.append(rt.value if rt else None)
                room_labels.append(cubicasa_class_label(class_id))

            return {
                "polygons": result["polygons"],
                "room_labels": room_labels,
                "room_types": room_types,
                "walls": result.get("walls", []),
                "doors": result.get("doors", []),
                "windows": result.get("windows", []),
                "confidence": result.get("confidence", 0.75),
                "warnings": result.get("warnings", []),
                "plot_bounds": None,
            }

        except Exception as e:
            logger.warning("cubicasa_modal_failed", error=str(e))
            self._modal_cls = None
            return None

    def _opencv_fallback(
        self, img: np.ndarray, scale: float, warnings: list[str]
    ) -> dict:
        """Basic contour detection using OpenCV."""
        import cv2

        h, w = img.shape[:2]

        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

        # Apply adaptive threshold to detect walls
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 15, 5,
        )

        # Morphological operations to clean up
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel, iterations=2)
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel, iterations=1)

        # Invert to get rooms as white regions
        rooms_mask = cv2.bitwise_not(binary)

        # Find contours
        contours, _ = cv2.findContours(
            rooms_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        # Filter and convert to room polygons
        min_area_px = 2.0 / (scale * scale)  # 2 sqm minimum
        polygons = []
        room_labels = []
        room_types = []

        for i, contour in enumerate(contours):
            area_px = cv2.contourArea(contour)
            if area_px < min_area_px:
                continue

            # Simplify contour
            epsilon = 0.02 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)
            if len(approx) < 3:
                continue

            # Convert to meter coordinates
            pts = []
            for pt in approx:
                x_m = round(pt[0][0] * scale, 3)
                y_m = round(pt[0][1] * scale, 3)
                pts.append((x_m, y_m))
            # Close polygon
            if pts and pts[0] != pts[-1]:
                pts.append(pts[0])

            polygons.append(pts)
            room_labels.append(f"Room {len(polygons)}")
            room_types.append(None)

        # Re-estimate scale if we have polygons
        if polygons and "estimated" in (warnings[0] if warnings else ""):
            areas_px = [cv2.contourArea(c) for c in contours if cv2.contourArea(c) >= min_area_px]
            if areas_px:
                try:
                    new_scale = estimate_scale_from_typical_room_size(areas_px)
                    # Re-convert polygons with improved scale
                    new_polygons = []
                    for contour_pts in polygons:
                        rescaled = [
                            (round(x / scale * new_scale, 3), round(y / scale * new_scale, 3))
                            for x, y in contour_pts
                        ]
                        new_polygons.append(rescaled)
                    polygons = new_polygons
                    scale = new_scale
                    warnings.append(f"Scale refined to {scale:.5f} m/px using detected room sizes")
                except Exception:
                    pass

        # Extract wall segments from binary image
        wall_segments = self._extract_walls_from_binary(binary, scale)

        warnings.append("Manual review recommended — OpenCV detection has limited accuracy")

        return {
            "polygons": polygons,
            "room_labels": room_labels,
            "room_types": room_types,
            "walls": wall_segments,
            "doors": [],
            "windows": [],
            "confidence": 0.4,
            "warnings": warnings,
            "plot_bounds": (round(w * scale, 1), round(h * scale, 1)),
        }

    def _extract_walls_from_binary(
        self, binary: np.ndarray, scale: float
    ) -> list[dict]:
        """Extract wall line segments from binary wall mask using HoughLines."""
        import cv2

        lines = cv2.HoughLinesP(
            binary, 1, np.pi / 180,
            threshold=50, minLineLength=30, maxLineGap=10,
        )
        if lines is None:
            return []

        walls = []
        for i, line in enumerate(lines[:100]):  # Cap at 100 walls
            x1, y1, x2, y2 = line[0]
            walls.append({
                "id": f"wall_{i}",
                "start": {"x": round(x1 * scale, 3), "y": round(y1 * scale, 3)},
                "end": {"x": round(x2 * scale, 3), "y": round(y2 * scale, 3)},
                "is_exterior": False,
            })
        return walls
