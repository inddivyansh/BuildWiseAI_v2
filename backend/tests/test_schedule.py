"""Schedule parsing: door/window rows become a mark→size index entry from the
SIZE column; materials/fixture schedules and size-less tables yield nothing.
Tests the pure row logic (`_index_rows`) — no PyMuPDF table detection needed —
plus the positioned-text index path."""

from __future__ import annotations

import pymupdf

from planlint.ingest.schedule import _index_rows, parse_schedule_index, parse_size
from planlint.models import AssetType


def _widths(specs):
    return {s.mark: s.width_in for s in specs}


def test_door_schedule_rows_yield_door_widths():
    rows = [
        ["MARK", "TYPE", "SIZE", "NOTES"],
        ["D1", "EXTERIOR DOOR", "3'-0\" x 6'-8\"", ""],
        ["D2", "INTERIOR DOOR", "2'-8\" x 6'-8\"", ""],
    ]
    specs = _index_rows(rows)
    assert [s.kind for s in specs] == [AssetType.DOOR, AssetType.DOOR]
    # The MARK cell keys the opening (the canonical identifier joined to the plan).
    assert _widths(specs) == {"D1": 36.0, "D2": 32.0}


def test_window_rows_detected_by_mark():
    # Rows carry only a W# mark, no literal 'window' word.
    rows = [["MARK", "SIZE"], ["W1", "2'-0\" x 3'-0\""], ["W2", "36\""]]
    specs = _index_rows(rows)
    assert [s.kind for s in specs] == [AssetType.WINDOW, AssetType.WINDOW]
    assert _widths(specs) == {"W1": 24.0, "W2": 36.0}


def test_materials_schedule_yields_nothing():
    # A real materials schedule (this sample's p25): a SIZE column, but the
    # items are bricks and molding, not openings.
    rows = [
        ["MARK", "ITEM", "SIZE", "NOTES"],
        ["F-00", "BRICK - SMOOTH", "6\"", ""],
        ["F-20", "FLOOR DECKING", "1x4", ""],
    ]
    assert _index_rows(rows) == []


def test_table_without_size_column_is_ignored():
    rows = [["MARK", "ITEM", "NOTES"], ["D1", "DOOR", "paint white"]]
    assert _index_rows(rows) == []


def test_door_row_without_a_parseable_size_is_skipped():
    rows = [["MARK", "TYPE", "SIZE"], ["D1", "DOOR", "SEE PLAN"]]
    assert _index_rows(rows) == []


# ------------------------------------------------- positioned-text schedule index

def test_parse_size():
    assert parse_size('36"x84"') == (36.0, 84.0)
    assert parse_size("3'-0\"x6'-8\"") == (36, 80)
    assert parse_size('30" X 60"') == (30.0, 60.0)  # spaced, capital X
    assert parse_size("COMPOSITE") is None


def _schedule_page(rows_by_section):
    """A one-page PDF whose door/window schedule is *positioned text* (no ruled
    table), mirroring real CAD sets that `find_tables` can't recover."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    y = 100
    for title, rows in rows_by_section:
        page.insert_text((100, y), title, fontsize=10)
        y += 18
        page.insert_text((100, y), "MARK   SIZE   MATERIAL", fontsize=9)
        y += 18
        for mark, size in rows:
            page.insert_text((100, y), mark, fontsize=9)
            page.insert_text((165, y), size, fontsize=9)  # SIZE column, right of MARK
            y += 18
        y += 12
    return doc, page


def test_parse_schedule_index_positioned_text():
    doc, page = _schedule_page([
        ("DOOR SCHEDULE", [("A", '36"x84"'), ("C", '24"x80"')]),
        ("WINDOW SCHEDULE", [("1", '30"x60"'), ("2", '24"x48"')]),
    ])
    idx = parse_schedule_index(page)
    doc.close()
    got = {m: (s.kind, s.width_in, s.height_in) for m, s in idx.items()}
    assert got == {
        "A": (AssetType.DOOR, 36.0, 84.0),
        "C": (AssetType.DOOR, 24.0, 80.0),
        "1": (AssetType.WINDOW, 30.0, 60.0),
        "2": (AssetType.WINDOW, 24.0, 48.0),
    }


def test_materials_schedule_index_is_empty():
    # 'MATERIALS SCHEDULE' is not an openings section → its rows (even a sized
    # one like window shutters '15"x60"') never enter the opening index.
    doc, page = _schedule_page([("MATERIALS SCHEDULE", [("F-90", '15"x60"')])])
    idx = parse_schedule_index(page)
    doc.close()
    assert idx == {}


def test_parse_schedule_index_ruled_table():
    # A ruled table PyMuPDF recovers feeds the same mark→size index.
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    xs, ys = [80, 180, 320, 460], [100, 130, 160, 190]
    for x in xs:
        page.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        page.draw_line((xs[0], y), (xs[-1], y))
    rows = [("MARK", "TYPE", "SIZE"), ("D1", "DOOR", "3'-0\" x 6'-8\""),
            ("W1", "WINDOW", "2'-0\" x 3'-0\"")]
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            page.insert_text((xs[c] + 4, ys[r] + 20), val, fontsize=8)
    idx = parse_schedule_index(page)
    doc.close()
    got = {m: (s.kind, s.width_in, s.height_in) for m, s in idx.items()}
    assert got == {
        "D1": (AssetType.DOOR, 36.0, 80.0),
        "W1": (AssetType.WINDOW, 24.0, 36.0),
    }
