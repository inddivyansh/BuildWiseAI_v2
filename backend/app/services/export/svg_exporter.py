"""Export a GeneratedFloorPlan to SVG."""

import math
import xml.etree.ElementTree as ET

from app.schemas.floor_plan import (
    Coordinate,
    GeneratedFloorPlan,
    RoomType,
    WallSegment,
)

# Room colors by type (pastel palette for readability)
ROOM_COLORS: dict[str, str] = {
    "living": "#E8F5E9",
    "kitchen": "#FFF3E0",
    "dining": "#FFFDE7",
    "bedroom": "#E3F2FD",
    "bathroom": "#E0F7FA",
    "half_bath": "#E0F7FA",
    "master_bedroom": "#C5CAE9",
    "master_bathroom": "#B2DFDB",
    "office": "#F3E5F5",
    "laundry": "#EFEBE9",
    "garage": "#ECEFF1",
    "hallway": "#F5F5F5",
    "closet": "#FBE9E7",
    "walkin_closet": "#FBE9E7",
    "pantry": "#FFF8E1",
    "mudroom": "#EFEBE9",
    "foyer": "#FFF9C4",
    "staircase": "#E0E0E0",
    "storage": "#F5F5F5",
    "balcony": "#E8EAF6",
    "porch": "#E8EAF6",
    "utility": "#ECEFF1",
    "other": "#FAFAFA",
}

# Pixels per meter for SVG rendering
SCALE = 50.0
PADDING = 40  # px padding around the plan


def export_svg(plan: GeneratedFloorPlan) -> str:
    """Render a GeneratedFloorPlan as an SVG string."""
    spec = plan.spec
    plot_w = spec.plot.width_m
    plot_d = spec.plot.depth_m

    svg_w = plot_w * SCALE + 2 * PADDING
    svg_h = plot_d * SCALE + 2 * PADDING

    root = ET.Element(
        "svg",
        xmlns="http://www.w3.org/2000/svg",
        width=str(int(svg_w)),
        height=str(int(svg_h)),
        viewBox=f"0 0 {int(svg_w)} {int(svg_h)}",
    )

    # Background
    ET.SubElement(root, "rect", width="100%", height="100%", fill="white")

    # Defs for fonts
    defs = ET.SubElement(root, "defs")
    style = ET.SubElement(defs, "style")
    style.text = (
        ".room-label { font-family: Arial, sans-serif; font-size: 11px; "
        "fill: #333; text-anchor: middle; dominant-baseline: central; } "
        ".area-label { font-family: Arial, sans-serif; font-size: 9px; "
        "fill: #666; text-anchor: middle; dominant-baseline: central; } "
        ".dim-label { font-family: Arial, sans-serif; font-size: 8px; "
        "fill: #999; text-anchor: middle; } "
        ".title { font-family: Arial, sans-serif; font-size: 14px; "
        "fill: #222; font-weight: bold; }"
    )

    # Group with offset
    g = ET.SubElement(root, "g", transform=f"translate({PADDING},{PADDING})")

    # Plot boundary
    _draw_plot(g, spec)

    # Setback lines (dashed)
    _draw_setbacks(g, spec)

    # Rooms
    for room in spec.rooms:
        if room.polygon:
            _draw_room(g, room)

    # Walls
    for wall in plan.walls:
        _draw_wall(g, wall)

    # Doors
    for door in plan.doors:
        wall = next((w for w in plan.walls if w.id == door.wall_id), None)
        if wall:
            _draw_door(g, door, wall)

    # Windows
    for window in plan.windows:
        wall = next((w for w in plan.walls if w.id == window.wall_id), None)
        if wall:
            _draw_window(g, window, wall)

    # Title
    ET.SubElement(
        root, "text", x=str(PADDING), y="20",
        **{"class": "title"},
    ).text = f"Floor Plan — {plan.total_area_sqm:.1f} m²"

    # Dimensions along bottom
    _draw_dimensions(g, spec)

    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _sx(x: float) -> float:
    """Convert meters to SVG x coordinate."""
    return x * SCALE


def _sy(y: float) -> float:
    """Convert meters to SVG y coordinate (Y flipped: 0=top in SVG)."""
    return y * SCALE


def _draw_plot(g: ET.Element, spec):
    """Draw the plot boundary rectangle."""
    w = spec.plot.width_m
    d = spec.plot.depth_m
    ET.SubElement(g, "rect",
        x="0", y="0",
        width=str(_sx(w)), height=str(_sy(d)),
        fill="none", stroke="#888", **{"stroke-width": "2", "stroke-dasharray": "8,4"},
    )


def _draw_setbacks(g: ET.Element, spec):
    """Draw setback lines as dashed rectangles."""
    s = spec.plot.setbacks
    x = _sx(s.left_m)
    y = _sy(s.front_m)
    w = _sx(spec.plot.width_m - s.left_m - s.right_m)
    h = _sy(spec.plot.depth_m - s.front_m - s.rear_m)
    if w > 0 and h > 0:
        ET.SubElement(g, "rect",
            x=str(x), y=str(y),
            width=str(w), height=str(h),
            fill="none", stroke="#CCC",
            **{"stroke-width": "1", "stroke-dasharray": "4,3"},
        )


def _draw_room(g: ET.Element, room):
    """Draw a room polygon with fill and label."""
    coords = room.polygon
    if not coords or len(coords) < 3:
        return

    points = " ".join(f"{_sx(c.x)},{_sy(c.y)}" for c in coords)
    color = ROOM_COLORS.get(room.type.value, ROOM_COLORS["other"])

    ET.SubElement(g, "polygon",
        points=points,
        fill=color,
        stroke="#444",
        **{"stroke-width": "1.5"},
    )

    # Compute centroid for label
    xs = [c.x for c in coords[:-1]]  # skip closing point
    ys = [c.y for c in coords[:-1]]
    if xs and ys:
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)

        ET.SubElement(g, "text",
            x=str(_sx(cx)), y=str(_sy(cy) - 6),
            **{"class": "room-label"},
        ).text = room.label

        if room.actual_area_sqm:
            ET.SubElement(g, "text",
                x=str(_sx(cx)), y=str(_sy(cy) + 8),
                **{"class": "area-label"},
            ).text = f"{room.actual_area_sqm:.1f} m²"


def _draw_wall(g: ET.Element, wall: WallSegment):
    """Draw a wall segment as a line."""
    stroke = "#222" if wall.is_exterior else "#666"
    width = "3" if wall.is_exterior else "1.5"
    ET.SubElement(g, "line",
        x1=str(_sx(wall.start.x)), y1=str(_sy(wall.start.y)),
        x2=str(_sx(wall.end.x)), y2=str(_sy(wall.end.y)),
        stroke=stroke, **{"stroke-width": width},
    )


def _draw_door(g: ET.Element, door, wall: WallSegment):
    """Draw a door as an arc symbol on its wall."""
    pos = door.position_along_wall
    dx = wall.end.x - wall.start.x
    dy = wall.end.y - wall.start.y
    px = wall.start.x + dx * pos
    py = wall.start.y + dy * pos

    r = door.width_m * 0.5 * SCALE
    cx = _sx(px)
    cy = _sy(py)

    # Door swing arc
    angle = math.atan2(dy, dx)
    arc_start_x = cx + r * math.cos(angle + math.pi / 2)
    arc_start_y = cy + r * math.sin(angle + math.pi / 2)
    arc_end_x = cx + r * math.cos(angle)
    arc_end_y = cy + r * math.sin(angle)

    ET.SubElement(g, "path",
        d=f"M {cx},{cy} L {arc_start_x},{arc_start_y} "
          f"A {r},{r} 0 0 1 {arc_end_x},{arc_end_y} Z",
        fill="none", stroke="#0077CC",
        **{"stroke-width": "1"},
    )


def _draw_window(g: ET.Element, window, wall: WallSegment):
    """Draw a window as parallel lines on its wall."""
    pos = window.position_along_wall
    dx = wall.end.x - wall.start.x
    dy = wall.end.y - wall.start.y
    length = math.sqrt(dx**2 + dy**2) or 1
    ux, uy = dx / length, dy / length  # unit vector along wall
    nx, ny = -uy, ux  # normal

    px = wall.start.x + dx * pos
    py = wall.start.y + dy * pos

    half_w = window.width_m * 0.5
    offset = 0.08  # perpendicular offset for double line

    for sign in (-1, 1):
        ox = nx * offset * sign
        oy = ny * offset * sign
        x1 = _sx(px - ux * half_w + ox)
        y1 = _sy(py - uy * half_w + oy)
        x2 = _sx(px + ux * half_w + ox)
        y2 = _sy(py + uy * half_w + oy)
        ET.SubElement(g, "line",
            x1=str(x1), y1=str(y1),
            x2=str(x2), y2=str(y2),
            stroke="#2196F3", **{"stroke-width": "2"},
        )


def _draw_dimensions(g: ET.Element, spec):
    """Draw overall dimension annotations."""
    w = spec.plot.width_m
    d = spec.plot.depth_m

    # Bottom dimension
    y_dim = _sy(d) + 20
    ET.SubElement(g, "line",
        x1="0", y1=str(y_dim), x2=str(_sx(w)), y2=str(y_dim),
        stroke="#999", **{"stroke-width": "1"},
    )
    ET.SubElement(g, "text",
        x=str(_sx(w / 2)), y=str(y_dim + 14),
        **{"class": "dim-label"},
    ).text = f"{w:.1f} m"

    # Right dimension
    x_dim = _sx(w) + 20
    ET.SubElement(g, "line",
        x1=str(x_dim), y1="0", x2=str(x_dim), y2=str(_sy(d)),
        stroke="#999", **{"stroke-width": "1"},
    )
    # Rotated text for vertical dimension
    mid_y = _sy(d / 2)
    text_el = ET.SubElement(g, "text",
        x=str(x_dim + 14), y=str(mid_y),
        transform=f"rotate(90, {x_dim + 14}, {mid_y})",
        **{"class": "dim-label"},
    )
    text_el.text = f"{d:.1f} m"
