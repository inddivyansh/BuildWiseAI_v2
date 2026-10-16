"""CubiCasa5k segmentation postprocessing.

Converts 44-channel model output into room polygons with class labels.
Two approaches:
  A) CubiCasa's own get_polygons() (if available)
  B) Simple contour-based fallback
"""

import numpy as np
import structlog

logger = structlog.get_logger()

# CubiCasa output channel split: [21 heatmaps, 12 room seg, 11 icon seg]
N_HEATMAP = 21
N_ROOM_SEG = 12
N_ICON_SEG = 11

# Room segmentation classes (indices 0-11 within the room seg channels)
ROOM_CLASSES = {
    0: "Background",
    1: "Outdoor",
    2: "Wall",
    3: "Kitchen",
    4: "Living Room",
    5: "Bed Room",
    6: "Bath",
    7: "Entry",
    8: "Railing",
    9: "Storage",
    10: "Garage",
    11: "Undefined",
}

# Classes that represent actual rooms (skip background, outdoor, wall, railing)
ROOM_CLASS_IDS = [3, 4, 5, 6, 7, 9, 10, 11]

# Icon segmentation classes
ICON_CLASSES = {
    0: "No Icon",
    1: "Window",
    2: "Door",
    3: "Closet",
    4: "Electrical Appliance",
    5: "Toilet",
    6: "Sink",
    7: "Sauna Bench",
    8: "Fire Place",
    9: "Bathtub",
    10: "Chimney",
}


def postprocess_segmentation(
    output: np.ndarray,
    scale_m_per_pixel: float,
    original_size: tuple[int, int],
    pad_info: dict,
) -> dict:
    """Convert 44-channel model output to room polygons.

    Args:
        output: (44, H, W) numpy array from model.
        scale_m_per_pixel: Pixel to meter conversion.
        original_size: (width, height) of original image.
        pad_info: Padding info from preprocessing.

    Returns:
        {polygons, room_class_ids, walls, doors, windows, confidence, warnings}
    """
    warnings = []

    # Split output channels
    heatmaps = output[:N_HEATMAP]          # (21, H, W)
    room_seg = output[N_HEATMAP:N_HEATMAP + N_ROOM_SEG]  # (12, H, W)
    icon_seg = output[N_HEATMAP + N_ROOM_SEG:]            # (11, H, W)

    # Argmax for room segmentation
    room_mask = np.argmax(room_seg, axis=0)  # (H, W) with values 0-11

    # Crop out padding
    resized_w, resized_h = pad_info["resized_size"]
    room_mask = room_mask[:resized_h, :resized_w]

    # Scale factor from resized image back to original
    inv_scale = 1.0 / pad_info["scale_factor"]

    # Per-pixel scale to meters
    m_per_resized_px = scale_m_per_pixel * inv_scale

    # Extract room polygons per class
    polygons = []
    room_class_ids = []

    for class_id in ROOM_CLASS_IDS:
        binary_mask = (room_mask == class_id).astype(np.uint8)
        if binary_mask.sum() < 100:  # Minimum 100 pixels
            continue

        class_polygons = _mask_to_polygons(binary_mask, m_per_resized_px)
        for poly in class_polygons:
            polygons.append(poly)
            room_class_ids.append(class_id)

    # Extract walls from wall class (index 2)
    wall_mask = (room_mask == 2).astype(np.uint8)
    wall_segments = _extract_walls_from_mask(wall_mask, m_per_resized_px)

    # Extract doors and windows from icon segmentation
    icon_mask = np.argmax(icon_seg[:, :resized_h, :resized_w], axis=0)
    doors = _extract_icons(icon_mask, class_id=2, m_per_px=m_per_resized_px, label="Door")
    windows = _extract_icons(icon_mask, class_id=1, m_per_px=m_per_resized_px, label="Window")

    # Compute confidence from segmentation quality
    total_classified = np.sum(room_mask > 0)
    total_pixels = room_mask.size
    coverage = total_classified / total_pixels if total_pixels > 0 else 0
    confidence = min(0.9, 0.5 + coverage * 0.5)

    if not polygons:
        warnings.append("No room polygons detected from segmentation")
        confidence = 0.1

    return {
        "polygons": polygons,
        "room_class_ids": room_class_ids,
        "walls": wall_segments,
        "doors": doors,
        "windows": windows,
        "confidence": round(confidence, 2),
        "warnings": warnings,
    }


def _mask_to_polygons(
    binary_mask: np.ndarray,
    m_per_px: float,
    min_area_sqm: float = 2.0,
    simplify_tolerance_px: float = 3.0,
) -> list[list[tuple[float, float]]]:
    """Convert a binary mask to a list of simplified polygons in meters."""
    import cv2

    contours, _ = cv2.findContours(
        binary_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    polygons = []
    min_area_px = min_area_sqm / (m_per_px * m_per_px)

    for contour in contours:
        area_px = cv2.contourArea(contour)
        if area_px < min_area_px:
            continue

        # Simplify contour
        epsilon = simplify_tolerance_px
        approx = cv2.approxPolyDP(contour, epsilon, True)
        if len(approx) < 3:
            continue

        # Convert to meter coordinates
        pts = []
        for pt in approx:
            x_m = round(float(pt[0][0]) * m_per_px, 3)
            y_m = round(float(pt[0][1]) * m_per_px, 3)
            pts.append((x_m, y_m))

        # Close polygon
        if pts and pts[0] != pts[-1]:
            pts.append(pts[0])

        polygons.append(pts)

    return polygons


def _extract_walls_from_mask(
    wall_mask: np.ndarray,
    m_per_px: float,
) -> list[dict]:
    """Extract wall line segments from the wall binary mask."""
    import cv2

    # Thin the wall mask to get center lines
    skeleton = cv2.ximgproc.thinning(wall_mask * 255) if hasattr(cv2, 'ximgproc') else wall_mask

    lines = cv2.HoughLinesP(
        skeleton, 1, np.pi / 180,
        threshold=30, minLineLength=20, maxLineGap=5,
    )
    if lines is None:
        return []

    walls = []
    for i, line in enumerate(lines[:200]):  # Cap at 200
        x1, y1, x2, y2 = line[0]
        walls.append({
            "id": f"wall_{i}",
            "start": {"x": round(x1 * m_per_px, 3), "y": round(y1 * m_per_px, 3)},
            "end": {"x": round(x2 * m_per_px, 3), "y": round(y2 * m_per_px, 3)},
            "is_exterior": False,
        })
    return walls


def _extract_icons(
    icon_mask: np.ndarray,
    class_id: int,
    m_per_px: float,
    label: str,
) -> list[dict]:
    """Extract icon positions (doors, windows) from icon segmentation mask."""
    import cv2

    binary = (icon_mask == class_id).astype(np.uint8)
    if binary.sum() < 10:
        return []

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    icons = []
    for contour in contours:
        M = cv2.moments(contour)
        if M["m00"] < 10:
            continue
        cx = M["m10"] / M["m00"]
        cy = M["m01"] / M["m00"]
        icons.append({
            "name": label,
            "x": round(cx * m_per_px, 3),
            "y": round(cy * m_per_px, 3),
        })
    return icons
