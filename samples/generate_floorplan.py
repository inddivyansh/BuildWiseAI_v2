"""Generate the bundled sample floor plan: a true VECTOR PDF drawn with
PyMuPDF primitives, at architect's scale 1/4" = 1'-0" (1 real foot = 18 pt).

Doors (leaf length encodes clear width at this scale):
- D1 36" — complies with ADA 404.2.3 (32" min)
- D2 30" — VIOLATES ADA 404.2.3
- D3 32" — complies exactly (boundary case)
- FIRE EXIT 36" — no applicable clause in the ADA excerpt -> needs review

Run:  uv run --project ../backend python generate_floorplan.py
"""

from pathlib import Path

import pymupdf

OUT = Path(__file__).parent / "sample_floorplan.pdf"

FT = 18.0  # 1/4" = 1'-0"  ->  1 ft = 0.25 in paper = 18 pt
IN = FT / 12


def door(page, hinge_x: float, y: float, width_in: float, label: str) -> None:
    """A door drawn CAD-style: leaf line + swing arc chord, opening gap in wall."""
    leaf = width_in * IN
    page.draw_line((hinge_x, y), (hinge_x + leaf, y))  # leaf
    page.draw_line((hinge_x + leaf, y), (hinge_x, y - leaf))  # swing chord
    page.insert_text((hinge_x + 4, y - 4), label, fontsize=7)


def main() -> None:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)  # US Letter

    # Outer walls (30 ft x 26 ft building)
    page.draw_rect(pymupdf.Rect(72, 72, 72 + 30 * FT, 72 + 26 * FT), width=2)

    # Interior wall at x = 72 + 13 ft, with three door openings
    wall_x = 72 + 13 * FT  # 306
    openings = [  # (y_start, gap_pt)
        (180, 36 * IN),
        (300, 30 * IN),
        (420, 32 * IN),
    ]
    y_cursor = 72.0
    for y_start, gap in openings:
        page.draw_line((wall_x, y_cursor), (wall_x, y_start))
        y_cursor = y_start + gap
    page.draw_line((wall_x, y_cursor), (wall_x, 72 + 26 * FT))

    # Doors on the interior wall openings
    door(page, wall_x, 180 + 36 * IN, 36, 'D1 36"')
    door(page, wall_x, 300 + 30 * IN, 30, 'D2 30"')
    door(page, wall_x, 420 + 32 * IN, 32, 'D3 32"')

    # Fire exit on the east wall
    exit_y = 300.0
    east_x = 72 + 30 * FT  # 612 - 0; wall at x=612-... = 612? 72+540=612 -> clip; use inside
    east_x = 72 + 30 * FT - 0.0
    door(page, east_x - 36 * IN, exit_y, 36, 'FIRE EXIT 36"')

    # Room labels
    page.insert_text((150, 300), "OFFICE A", fontsize=10)
    page.insert_text((420, 300), "CORRIDOR", fontsize=10)

    # Title block
    page.draw_rect(pymupdf.Rect(340, 700, 580, 760), width=1)
    page.insert_text((350, 720), "SAMPLE OFFICE — LEVEL 1", fontsize=10)
    page.insert_text((350, 740), 'SCALE: 1/4" = 1\'-0"', fontsize=9)

    doc.save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
