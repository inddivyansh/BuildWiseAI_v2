"""Scale detection utilities for floor plan ingestion.

Provides heuristics to determine the real-world scale (meters per pixel)
of uploaded floor plan images when no explicit scale is provided.
"""

import math
from pathlib import Path

import structlog

logger = structlog.get_logger()


def estimate_scale_from_known_dimension(
    pixel_length: float, real_length_m: float
) -> float:
    """Compute scale factor from a known dimension.

    Args:
        pixel_length: Length in pixels of a known feature.
        real_length_m: Real-world length of that feature in meters.

    Returns:
        Meters per pixel.
    """
    if pixel_length <= 0:
        raise ValueError("pixel_length must be positive")
    return real_length_m / pixel_length


def estimate_scale_from_typical_room_size(
    polygon_areas_px: list[float],
    assumed_avg_area_sqm: float = 12.0,
) -> float:
    """Estimate scale from polygon areas assuming typical room sizes.

    Heuristic fallback when no explicit scale info is available.
    Assumes the average detected room is ~12 sqm (a typical bedroom).

    Args:
        polygon_areas_px: List of polygon areas in pixel^2.
        assumed_avg_area_sqm: Expected average room area in sqm.

    Returns:
        Meters per pixel.
    """
    valid_areas = [a for a in polygon_areas_px if a > 0]
    if not valid_areas:
        raise ValueError("No valid polygon areas provided")

    avg_area_px = sum(valid_areas) / len(valid_areas)
    # scale^2 = assumed_avg_area_sqm / avg_area_px
    # scale = sqrt(assumed_avg_area_sqm / avg_area_px)
    scale = math.sqrt(assumed_avg_area_sqm / avg_area_px)
    logger.info(
        "scale_estimated_from_room_size",
        avg_area_px=round(avg_area_px, 1),
        assumed_avg_sqm=assumed_avg_area_sqm,
        scale_m_per_px=round(scale, 6),
    )
    return scale


def detect_image_dpi(file_path: Path) -> float | None:
    """Read DPI from image EXIF/metadata.

    Returns pixels per meter if DPI info is found, else None.
    """
    try:
        from PIL import Image

        with Image.open(file_path) as img:
            info = img.info
            dpi = info.get("dpi")
            if dpi:
                # DPI is (x_dpi, y_dpi), average them
                avg_dpi = (dpi[0] + dpi[1]) / 2
                if avg_dpi > 0:
                    # 1 inch = 0.0254 meters
                    m_per_pixel = 0.0254 / avg_dpi
                    logger.info(
                        "dpi_detected",
                        dpi=round(avg_dpi, 1),
                        m_per_pixel=round(m_per_pixel, 6),
                    )
                    return m_per_pixel
    except Exception as e:
        logger.debug("dpi_detection_failed", error=str(e))

    return None


def estimate_scale_from_image_width(
    image_width_px: int,
    assumed_plan_width_m: float = 10.0,
) -> float:
    """Last-resort scale estimate assuming typical floor plan width.

    Args:
        image_width_px: Image width in pixels.
        assumed_plan_width_m: Assumed real-world width of the floor plan.

    Returns:
        Meters per pixel.
    """
    if image_width_px <= 0:
        raise ValueError("image_width_px must be positive")
    return assumed_plan_width_m / image_width_px
