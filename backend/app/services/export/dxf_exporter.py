"""Export a GeneratedFloorPlan to DXF (AutoCAD) format."""

import math
from pathlib import Path

import ezdxf
from ezdxf.enums import TextEntityAlignment

from app.schemas.floor_plan import GeneratedFloorPlan, WallSegment

# AutoCAD color indices by room type
ROOM_ACI: dict[str, int] = {
    "living": 3,            # green
    "kitchen": 30,          # orange
    "dining": 2,            # yellow
    "bedroom": 5,           # blue
    "bathroom": 4,          # cyan
    "half_bath": 4,
    "master_bedroom": 174,  # violet
    "master_bathroom": 134, # teal
    "office": 6,            # magenta
    "laundry": 42,          # brown
    "garage": 8,            # grey
    "hallway": 9,           # light grey
    "closet": 14,
    "walkin_closet": 14,
    "pantry": 40,
    "mudroom": 42,
    "foyer": 50,
    "staircase": 8,
    "storage": 9,
    "balcony": 150,
    "porch": 150,
    "utility": 8,
    "other": 7,             # white/black
}


def export_dxf(plan: GeneratedFloorPlan, output_path: Path | None = None) -> bytes:
    """
    Render a GeneratedFloorPlan as a DXF R2010 file.

    Returns the DXF content as bytes. If output_path is provided, also saves to disk.
    """
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()

    # Create layers
    doc.layers.add("PLOT", color=8)
    doc.layers.add("SETBACKS", color=9, linetype="DASHED")
    doc.layers.add("ROOMS", color=7)
    doc.layers.add("WALLS_EXTERIOR", color=1)
    doc.layers.add("WALLS_INTERIOR", color=7)
    doc.layers.add("DOORS", color=5)
    doc.layers.add("WINDOWS", color=4)
    doc.layers.add("ANNOTATIONS", color=7)
    doc.layers.add("DIMENSIONS", color=3)

    # Add DASHED linetype
    if "DASHED" not in doc.linetypes:
        doc.linetypes.add("DASHED", pattern=[0.5, 0.25, -0.25])

    spec = plan.spec

    # --- Plot boundary ---
    w, d = spec.plot.width_m, spec.plot.depth_m
    msp.add_lwpolyline(
        [(0, 0), (w, 0), (w, d), (0, d), (0, 0)],
        dxfattribs={"layer": "PLOT"},
    )

    # --- Setback boundary ---
    s = spec.plot.setbacks
    bx = s.left_m
    by = s.front_m
    bw = w - s.left_m - s.right_m
    bh = d - s.front_m - s.rear_m
    if bw > 0 and bh > 0:
        msp.add_lwpolyline(
            [(bx, by), (bx + bw, by), (bx + bw, by + bh), (bx, by + bh), (bx, by)],
            dxfattribs={"layer": "SETBACKS", "linetype": "DASHED"},
        )

    # --- Rooms ---
    for room in spec.rooms:
        if not room.polygon or len(room.polygon) < 3:
            continue

        pts = [(c.x, c.y) for c in room.polygon]
        color = ROOM_ACI.get(room.type.value, 7)

        # Room polygon with hatch fill
        msp.add_lwpolyline(pts, dxfattribs={"layer": "ROOMS", "color": color})

        # Add solid hatch for room fill
        hatch = msp.add_hatch(color=color, dxfattribs={"layer": "ROOMS"})
        hatch.paths.add_polyline_path(pts, is_closed=True)
        hatch.set_pattern_fill("SOLID")

        # Centroid for label
        xs = [c.x for c in room.polygon[:-1]]
        ys = [c.y for c in room.polygon[:-1]]
        if xs and ys:
            cx = sum(xs) / len(xs)
            cy = sum(ys) / len(ys)

            # Room label
            msp.add_text(
                room.label,
                height=0.3,
                dxfattribs={"layer": "ANNOTATIONS", "color": color},
            ).set_placement((cx, cy + 0.2), align=TextEntityAlignment.MIDDLE_CENTER)

            # Area annotation
            if room.actual_area_sqm:
                msp.add_text(
                    f"{room.actual_area_sqm:.1f} m²",
                    height=0.2,
                    dxfattribs={"layer": "ANNOTATIONS", "color": 8},
                ).set_placement((cx, cy - 0.3), align=TextEntityAlignment.MIDDLE_CENTER)

    # --- Walls ---
    for wall in plan.walls:
        layer = "WALLS_EXTERIOR" if wall.is_exterior else "WALLS_INTERIOR"
        color = 1 if wall.is_exterior else 7
        lineweight = 50 if wall.is_exterior else 25  # 0.50mm / 0.25mm

        msp.add_line(
            (wall.start.x, wall.start.y),
            (wall.end.x, wall.end.y),
            dxfattribs={"layer": layer, "color": color, "lineweight": lineweight},
        )

    # --- Doors ---
    for door in plan.doors:
        wall = next((w for w in plan.walls if w.id == door.wall_id), None)
        if not wall:
            continue
        _draw_dxf_door(msp, door, wall)

    # --- Windows ---
    for window in plan.windows:
        wall = next((w for w in plan.walls if w.id == window.wall_id), None)
        if not wall:
            continue
        _draw_dxf_window(msp, window, wall)

    # --- Dimensions ---
    # Plot width
    msp.add_aligned_dim(
        p1=(0, -1), p2=(w, -1), distance=-0.5,
        dxfattribs={"layer": "DIMENSIONS"},
    ).render()

    # Plot depth
    msp.add_aligned_dim(
        p1=(-1, 0), p2=(-1, d), distance=-0.5,
        dxfattribs={"layer": "DIMENSIONS"},
    ).render()

    # Title block
    msp.add_text(
        f"Floor Plan — {plan.total_area_sqm:.1f} m²",
        height=0.5,
        dxfattribs={"layer": "ANNOTATIONS"},
    ).set_placement((0, d + 1.5), align=TextEntityAlignment.LEFT)

    msp.add_text(
        f"Generator: {plan.generator_model}",
        height=0.25,
        dxfattribs={"layer": "ANNOTATIONS", "color": 8},
    ).set_placement((0, d + 0.8), align=TextEntityAlignment.LEFT)

    # Write to bytes (ezdxf writes text; encode to UTF-8 bytes)
    import io
    stream = io.StringIO()
    doc.write(stream)
    dxf_bytes = stream.getvalue().encode("utf-8")

    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(dxf_bytes)

    return dxf_bytes


def _draw_dxf_door(msp, door, wall: WallSegment):
    """Draw a door arc symbol on its wall."""
    pos = door.position_along_wall
    dx = wall.end.x - wall.start.x
    dy = wall.end.y - wall.start.y
    px = wall.start.x + dx * pos
    py = wall.start.y + dy * pos

    r = door.width_m * 0.5
    angle = math.degrees(math.atan2(dy, dx))

    # Door swing arc (90 degree)
    msp.add_arc(
        center=(px, py),
        radius=r,
        start_angle=angle,
        end_angle=angle + 90,
        dxfattribs={"layer": "DOORS"},
    )
    # Line from center to arc start
    end_x = px + r * math.cos(math.radians(angle))
    end_y = py + r * math.sin(math.radians(angle))
    msp.add_line(
        (px, py), (end_x, end_y),
        dxfattribs={"layer": "DOORS"},
    )


def _draw_dxf_window(msp, window, wall: WallSegment):
    """Draw a window as a break symbol (parallel lines) on its wall."""
    pos = window.position_along_wall
    dx = wall.end.x - wall.start.x
    dy = wall.end.y - wall.start.y
    length = math.sqrt(dx**2 + dy**2) or 1
    ux, uy = dx / length, dy / length
    nx, ny = -uy, ux

    px = wall.start.x + dx * pos
    py = wall.start.y + dy * pos

    half_w = window.width_m * 0.5
    offset = 0.08

    for sign in (-1, 1):
        ox = nx * offset * sign
        oy = ny * offset * sign
        msp.add_line(
            (px - ux * half_w + ox, py - uy * half_w + oy),
            (px + ux * half_w + ox, py + uy * half_w + oy),
            dxfattribs={"layer": "WINDOWS"},
        )
