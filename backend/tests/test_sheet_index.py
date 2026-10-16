"""Sheet-index parsing: a cover page's `number → title` list becomes a registry.
Also covers the line anchor that keeps prose ('SEE A3.0 …') out of the index."""

from __future__ import annotations

import pymupdf

from planlint.ingest.sheet_index import _index_from_line, parse_sheet_index


def test_index_from_line():
    assert _index_from_line("A1.2 1ST FLOOR PLAN") == ("A1.2", "1ST FLOOR PLAN")
    assert _index_from_line("A0.0   COVER PAGE") == ("A0.0", "COVER PAGE")
    assert _index_from_line("SEE A3.0 FOR SECTION") is None  # number not at line start
    assert _index_from_line("A5.0") is None  # a bare number with no title


def _index_page(rows):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    y = 100
    for number, title in rows:
        page.insert_text((100, y), number, fontsize=9)
        page.insert_text((165, y), title, fontsize=9)  # title column, right of the number
        y += 18
    return doc, page


def test_parse_sheet_index_positioned_text():
    doc, page = _index_page(
        [("A0.0", "COVER"), ("A1.2", "1ST FLOOR PLAN"), ("A3.0", "SECTIONS")]
    )
    reg = parse_sheet_index(page)
    doc.close()
    assert reg == {"A0.0": "COVER", "A1.2": "1ST FLOOR PLAN", "A3.0": "SECTIONS"}
