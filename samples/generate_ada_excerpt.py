"""Generate the bundled sample codebook: an accessibility & egress excerpt.

Chapters 4-5 are verbatim 2010 ADA Standards for Accessible Design (a US
government work, public domain — safe to bundle, unlike NFPA/ICC codebooks).
The closing egress section is an *illustrative* provision in our own wording
(min opening width), not copied from any copyrighted code — it lets the sample
demonstrate a window (egress) check, which ADA alone does not provide.

Headings are set in bold like a real codebook so layout-aware parsers
(Docling) classify them as section headers; each clause body is its own
paragraph so the simple parser recovers the tree too.

Run:  uv run --project ../backend python generate_ada_excerpt.py
"""

from pathlib import Path

import pymupdf

OUT = Path(__file__).parent / "ada_excerpt.pdf"

# (heading, body) — an empty body is a bare heading.
PAGE1 = [
    ("2010 ADA Standards for Accessible Design (excerpt)", ""),
    ("Chapter 4: Accessible Routes", ""),
    ("403 Walking Surfaces", ""),
    (
        "403.1 General.",
        "Walking surfaces that are a part of an accessible route shall comply with 403.",
    ),
    (
        "403.5 Clearances.",
        "Walking surfaces shall provide clearances complying with 403.5.",
    ),
    (
        "403.5.1 Clear Width.",
        "Except as provided in 403.5.2 and 403.5.3, the clear width of walking "
        "surfaces shall be 36 inches (915 mm) minimum.",
    ),
]

PAGE2 = [
    ("404 Doors, Doorways, and Gates", ""),
    (
        "404.1 General.",
        "Doors, doorways, and gates that are part of an accessible route shall "
        "comply with 404.",
    ),
    (
        "404.2 Manual Doors, Doorways, and Manual Gates.",
        "Manual doors and doorways and manual gates intended for user passage "
        "shall comply with 404.2.",
    ),
    (
        "404.2.3 Clear Width.",
        "Door openings shall provide a clear width of 32 inches (815 mm) minimum.",
    ),
]

PAGE3 = [
    ("405 Ramps", ""),
    (
        "405.1 General.",
        "Ramps on accessible routes shall comply with 405.",
    ),
    (
        "405.2 Slope.",
        "Ramp runs shall have a running slope not steeper than 1:12.",
    ),
    (
        "405.5 Clear Width.",
        "The clear width of a ramp run and, where handrails are provided, the clear "
        "width between handrails shall be 36 inches (915 mm) minimum.",
    ),
]

PAGE4 = [
    ("Chapter 5: General Site and Building Elements", ""),
    ("504 Stairways", ""),
    (
        "504.1 General.",
        "Stairs shall comply with 504.",
    ),
    (
        "504.2 Treads and Risers.",
        "Stair riser heights shall be 4 inches (100 mm) minimum and 7 inches "
        "(180 mm) maximum. Stair tread depths shall be 11 inches (280 mm) minimum.",
    ),
]

# Illustrative egress provision (OUR wording, not copied from any code) so the
# sample can demonstrate a window/opening check — ADA does not size windows.
PAGE5 = [
    ("Egress: Emergency Escape and Rescue Openings", ""),
    ("1030 Emergency Escape and Rescue Openings", ""),
    (
        "1030.2 Minimum Clear Width.",
        "An emergency escape and rescue opening, including an operable window "
        "serving as the required opening, shall provide a clear opening width of "
        "20 inches (508 mm) minimum.",
    ),
]


def write_page(doc: pymupdf.Document, entries: list[tuple[str, str]]) -> None:
    page = doc.new_page(width=612, height=792)
    y = 72.0
    for heading, body in entries:
        rect = pymupdf.Rect(72, y, 540, y + 40)
        used = page.insert_textbox(
            rect, heading, fontsize=12, fontname="hebo", lineheight=1.3
        )
        y += (40 - used) + 6
        if body:
            rect = pymupdf.Rect(72, y, 540, y + 110)
            used = page.insert_textbox(rect, body, fontsize=10, lineheight=1.3)
            y += (110 - used) + 12
        else:
            y += 4


def main() -> None:
    doc = pymupdf.open()
    write_page(doc, PAGE1)
    write_page(doc, PAGE2)
    write_page(doc, PAGE3)
    write_page(doc, PAGE4)
    write_page(doc, PAGE5)
    doc.save(OUT)
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
