"""Sheet-type routing in floor-plan ingestion: the plan-view detector runs only
on floor-plan pages, schedules are parsed for widths, and every other sheet is
recorded but not linted. No real vision calls."""

from __future__ import annotations

import pymupdf
import pytest

from planlint.ingest import spatial
from planlint.ingest.sheet_type import SheetType
from planlint.ingest.vlm import VlmEntity, VlmPage
from planlint.models import AssetType, Parameter, PhysicalAsset


def _blank_pdf(path, pages: int) -> None:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)
    doc.save(str(path))
    doc.close()


async def test_only_floor_plans_are_detected_schedules_are_parsed(
    tmp_path, fake_repo, monkeypatch
):
    pdf = tmp_path / "set.pdf"
    _blank_pdf(pdf, pages=3)
    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", False)

    # Page 0 foundation (skip), page 1 floor plan (detect), page 2 schedule
    # (feeds the index, emits no assets of its own).
    order = [SheetType.FOUNDATION, SheetType.FLOOR_PLAN, SheetType.SCHEDULE]
    seen = {"i": 0}

    def fake_classify(page):
        kind = order[seen["i"]]
        seen["i"] += 1
        return kind

    detect_calls = {"n": 0}

    async def fake_detect(png, model):
        detect_calls["n"] += 1
        return VlmPage(entities=[], scale_text=None)

    monkeypatch.setattr(spatial, "classify_sheet", fake_classify)
    monkeypatch.setattr(spatial, "detect_page", fake_detect)

    row = {
        "id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
        "filename": "set.pdf", "path": str(pdf), "ingested": False,
    }
    fake_repo.documents["doc-1"] = row

    events = []

    async def emit(event):
        events.append(event)

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)

    # Detector ran once — only the floor-plan page.
    assert detect_calls["n"] == 1
    # All three pages recorded; the schedule contributes to the index, not assets.
    assert len(fake_repo.sheets) == 3
    assert list(fake_repo.assets.values()) == []
    # The foundation page was recorded but flagged not-linted.
    assert any("not linted" in e.message for e in events)
    assert any("schedule — indexed for callout matching" in e.message for e in events)


async def test_phantom_space_without_name_area_or_walls_is_dropped(
    tmp_path, fake_repo, monkeypatch
):
    # On a blank (wall-less) page, a room with no name and no area has no
    # geometric support — it's unrelated lines boxed as a space, and is dropped.
    # A named room on the same page is kept.
    pdf = tmp_path / "plan.pdf"
    _blank_pdf(pdf, pages=1)
    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", False)
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    async def fake_detect(png, model):
        return VlmPage(
            entities=[
                VlmEntity(entity_type=AssetType.ROOM, name="", box=(50, 50, 200, 200)),
                VlmEntity(entity_type=AssetType.ROOM, name="KITCHEN", box=(60, 60, 210, 210)),
            ],
            scale_text=None,
        )

    monkeypatch.setattr(spatial, "detect_page", fake_detect)

    row = {
        "id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
        "filename": "plan.pdf", "path": str(pdf), "ingested": False,
    }
    fake_repo.documents["doc-1"] = row

    async def emit(event):
        return None

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)

    rooms = [a["label"] for a in fake_repo.assets.values() if a["type"] == "room"]
    assert rooms == ["KITCHEN"]  # the unnamed, wall-less phantom was dropped


def _room_pdf(path):
    """A page whose only geometry is four walls forming a box in PDF points."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_line((100, 100), (300, 100))  # top
    page.draw_line((100, 200), (300, 200))  # bottom
    page.draw_line((100, 100), (100, 200))  # left
    page.draw_line((300, 100), (300, 200))  # right
    doc.save(str(path))
    doc.close()


async def _ingest_entity(pdf, fake_repo, monkeypatch, entity, scale_text=None):
    """Ingest a single-page plan whose detector returns exactly `entity`; return
    all asset rows written to the repo."""
    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", False)
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    async def fake_detect(png, model):
        return VlmPage(entities=[entity], scale_text=scale_text)

    monkeypatch.setattr(spatial, "detect_page", fake_detect)
    row = {
        "id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
        "filename": "plan.pdf", "path": str(pdf), "ingested": False,
    }
    fake_repo.documents["doc-1"] = row

    async def emit(event):
        return None

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)
    return list(fake_repo.assets.values())


async def test_wall_snapped_but_nameless_room_is_dropped(tmp_path, fake_repo, monkeypatch):
    # A nameless, area-less box that snaps to real walls (vector-snapped) is
    # still dropped — for rooms, geometry alone isn't enough.
    pdf = tmp_path / "plan.pdf"
    _room_pdf(pdf)
    entity = VlmEntity(entity_type=AssetType.ROOM, name="", box=(105, 105, 295, 195))
    assets = await _ingest_entity(pdf, fake_repo, monkeypatch, entity)
    assert assets == []


async def test_unnamed_room_with_printed_area_is_kept(tmp_path, fake_repo, monkeypatch):
    # The "OR printed area" branch: an unnamed room whose area is *printed on the
    # sheet* (grounding the VLM value) survives and carries that area.
    pdf = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_line((100, 100), (300, 100))
    page.draw_line((100, 200), (300, 200))
    page.draw_line((100, 100), (100, 200))
    page.draw_line((300, 100), (300, 200))
    page.insert_text((180, 160), "18 m2", fontsize=8)  # printed area grounds the value
    doc.save(str(pdf))
    doc.close()
    entity = VlmEntity(
        entity_type=AssetType.ROOM, name="", floor_area_m2=18.0, box=(105, 105, 295, 195)
    )
    assets = await _ingest_entity(pdf, fake_repo, monkeypatch, entity)
    assert len(assets) == 1
    assert assets[0]["measurements"]["area_m2"] == 18.0


async def test_fabricated_room_area_is_dropped(tmp_path, fake_repo, monkeypatch):
    # No area is printed on the sheet, so the VLM's floor_area_m2 is a guess and
    # is NOT turned into a measurement. An unnamed room then has nothing checkable
    # and is dropped — never carries an invented area.
    pdf = tmp_path / "plan.pdf"
    _room_pdf(pdf)
    entity = VlmEntity(
        entity_type=AssetType.ROOM, name="", floor_area_m2=18.0, box=(105, 105, 295, 195)
    )
    assets = await _ingest_entity(pdf, fake_repo, monkeypatch, entity)
    assert assets == []


async def test_room_gets_no_clear_width(tmp_path, fake_repo, monkeypatch):
    # A room must never receive a clear width. Even with long wall segments inside
    # its box (which the longest-segment heuristic would grab × scale), the room
    # carries no clear_width — that heuristic is for openings, not spaces.
    pdf = tmp_path / "plan.pdf"
    _room_pdf(pdf)  # 200pt walls sit inside the room box as long segments
    entity = VlmEntity(entity_type=AssetType.ROOM, name="LOUNGE", box=(105, 105, 295, 195))
    assets = await _ingest_entity(
        pdf, fake_repo, monkeypatch, entity, scale_text='SCALE: 1/4" = 1\'-0"'
    )
    assert len(assets) == 1
    assert "clear_width" not in assets[0]["measurements"]


async def test_bare_vlm_guess_is_dropped(tmp_path, fake_repo, monkeypatch):
    # A window the VLM boxed on a blank page: no name, no measurement, and no
    # geometry it snapped to (vlm-only). It is noise — dropped.
    pdf = tmp_path / "plan.pdf"
    _blank_pdf(pdf, pages=1)
    entity = VlmEntity(entity_type=AssetType.WINDOW, name="", box=(50.0, 50.0, 120.0, 60.0))
    assets = await _ingest_entity(pdf, fake_repo, monkeypatch, entity)
    assert assets == []


async def test_snapped_opening_prefers_printed_dimension(tmp_path, fake_repo, monkeypatch):
    # A printed dimension label near the opening (3'-0" = 36") wins over the
    # geometric gap width (36pt × 48/72 = 24").
    pdf = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_line((200, 300), (350, 300))
    page.draw_line((386, 300), (520, 300))  # 36pt gap at 350..386
    page.insert_text((352, 297), "3'-0\"", fontsize=8)  # dimension inside the gap
    doc.save(str(pdf))
    doc.close()

    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", False)
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    async def fake_detect(png, model):
        return VlmPage(
            entities=[VlmEntity(entity_type=AssetType.WINDOW, name="", box=(345, 292, 391, 308))],
            scale_text='SCALE: 1/4" = 1\'-0"',
        )

    monkeypatch.setattr(spatial, "detect_page", fake_detect)
    row = {"id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
           "filename": "plan.pdf", "path": str(pdf), "ingested": False}
    fake_repo.documents["doc-1"] = row

    async def emit(event):
        return None

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)
    window = next(a for a in fake_repo.assets.values() if a["type"] == "window")
    assert window["measurements"]["clear_width"] == 36.0  # the label, not the 24" gap


async def test_ramp_slope_read_from_label(tmp_path, fake_repo, monkeypatch):
    pdf = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((110, 230), "SLOPE 1:12", fontsize=8)  # slope annotation on the ramp
    doc.save(str(pdf))
    doc.close()

    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", False)
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    async def fake_detect(png, model):
        return VlmPage(
            entities=[VlmEntity(entity_type=AssetType.RAMP, name="RAMP", box=(100, 200, 220, 260))],
            scale_text=None,
        )

    monkeypatch.setattr(spatial, "detect_page", fake_detect)
    row = {"id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
           "filename": "plan.pdf", "path": str(pdf), "ingested": False}
    fake_repo.documents["doc-1"] = row

    async def emit(event):
        return None

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)
    ramp = next(a for a in fake_repo.assets.values() if a["type"] == "ramp")
    assert ramp["measurements"]["slope"] == pytest.approx(0.083, abs=0.001)


async def test_snapped_door_without_name_or_measurement_is_kept(tmp_path, fake_repo, monkeypatch):
    # A door in a real wall gap snaps (vector-snapped) and is kept even without a
    # name or measurement — geometry confirms it, so it is not a bare guess.
    pdf = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_line((100.0, 200.0), (250.0, 200.0))  # wall left of the door
    page.draw_line((286.0, 200.0), (440.0, 200.0))  # wall right of the door (gap 250..286)
    doc.save(str(pdf))
    doc.close()
    entity = VlmEntity(entity_type=AssetType.DOOR, name="", box=(245.0, 192.0, 291.0, 208.0))
    assets = await _ingest_entity(pdf, fake_repo, monkeypatch, entity)
    assert len(assets) == 1
    assert assets[0]["type"] == "door"
    assert assets[0]["source"] == "vector-snapped"


async def test_room_area_from_dimension_grid(tmp_path, fake_repo, monkeypatch):
    # A room bounded by walls, with an overall H dimension (12'-0") above it and a
    # V dimension (8'-0") beside it, gets its area from width×depth off the grid —
    # the dimension lines sit *outside* the room, associated by matching span.
    pdf = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    # Room walls: 216pt (=12'-0" @ 1/4"=1') wide, 144pt (=8'-0") tall.
    page.draw_line((100, 100), (316, 100))   # top wall
    page.draw_line((100, 244), (316, 244))   # bottom wall
    page.draw_line((100, 100), (100, 244))   # left wall
    page.draw_line((316, 100), (316, 244))   # right wall
    page.draw_line((100, 72), (316, 72))     # H dimension line, above the room
    page.draw_line((72, 100), (72, 244))     # V dimension line, left of the room
    page.insert_text((190, 70), "12'-0\"", fontsize=8)  # on the H dim line
    page.insert_text((52, 175), "8'-0\"", fontsize=8)    # on the V dim line
    doc.save(str(pdf))
    doc.close()

    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", False)
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    async def fake_detect(png, model):
        return VlmPage(
            entities=[VlmEntity(entity_type=AssetType.ROOM, name="OFFICE", box=(108, 108, 308, 236))],
            scale_text='SCALE: 1/4" = 1\'-0"',
        )

    monkeypatch.setattr(spatial, "detect_page", fake_detect)
    row = {"id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
           "filename": "plan.pdf", "path": str(pdf), "ingested": False}
    fake_repo.documents["doc-1"] = row

    async def emit(event):
        return None

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)
    room = next(a for a in fake_repo.assets.values() if a["type"] == "room")
    # 144" × 96" = 13824 in² → 8.92 m².
    assert room["measurements"]["area_m2"] == pytest.approx(8.92, abs=0.05)


async def test_joist_note_is_not_read_as_door_clear_width(tmp_path, fake_repo, monkeypatch):
    # A structural note ("2X12 JOISTS @ 16\" OC") sits next to a door. Its 16"
    # must NOT become the clear width — the door's width comes from the wall gap
    # (36pt × 48/72 = 24"), not the note.
    pdf = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_line((100.0, 200.0), (250.0, 200.0))  # wall left of the door
    page.draw_line((286.0, 200.0), (440.0, 200.0))  # wall right (gap 250..286 = 36pt)
    page.insert_text((255, 188), '2X12 JOISTS @ 16" OC', fontsize=8)  # joist note by the door
    doc.save(str(pdf))
    doc.close()

    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", False)
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    async def fake_detect(png, model):
        return VlmPage(
            entities=[VlmEntity(entity_type=AssetType.DOOR, name="", box=(245.0, 192.0, 291.0, 208.0))],
            scale_text='SCALE: 1/4" = 1\'-0"',
        )

    monkeypatch.setattr(spatial, "detect_page", fake_detect)
    row = {"id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
           "filename": "plan.pdf", "path": str(pdf), "ingested": False}
    fake_repo.documents["doc-1"] = row

    async def emit(event):
        return None

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)
    door = next(a for a in fake_repo.assets.values() if a["type"] == "door")
    assert door["measurements"]["clear_width"] == pytest.approx(24.0, abs=1.0)  # the gap, not 16"


def test_resolve_callout_kind_and_proximity():
    # A door accepts only door (letter) marks, a window only window (number)
    # marks, and only within a tight radius — so a numeric section marker can't
    # pose as a window callout, and a door won't grab a window's number.
    from planlint.ingest.schedule import OpeningSpec
    from planlint.ingest.spatial import _resolve_callout
    from planlint.ingest.vector_geometry import TextLabel

    index = {
        "C": OpeningSpec(AssetType.DOOR, "C", 24.0, 80.0),
        "1": OpeningSpec(AssetType.WINDOW, "1", 30.0, 60.0),
    }
    labels = [TextLabel("C", (100, 100, 110, 110)), TextLabel("1", (300, 300, 310, 310))]
    assert _resolve_callout((95, 95, 115, 115), AssetType.DOOR, labels, index) == "C"
    # a door over the numeric mark does not match it (kind restriction)
    assert _resolve_callout((295, 295, 315, 315), AssetType.DOOR, labels, index) is None
    # a window far from any mark matches nothing (proximity)
    assert _resolve_callout((500, 500, 520, 520), AssetType.WINDOW, labels, index) is None


async def _ingest_two_page_set(tmp_path, fake_repo, monkeypatch, door_callout, schedule_size,
                               scale_text):
    """A 2-page set: a floor-plan door in a wall gap tagged `door_callout`, then a
    door schedule mapping mark 'C' → `schedule_size`. Returns the plan door row."""
    pdf = tmp_path / "set.pdf"
    doc = pymupdf.open()
    p0 = doc.new_page(width=612, height=792)
    p0.draw_line((100, 200), (250, 200))
    p0.draw_line((286, 200), (440, 200))  # 36pt gap → 24" at 1/4"=1'
    if door_callout:
        p0.insert_text((262, 190), door_callout, fontsize=8)  # callout on the door
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((100, 100), "DOOR SCHEDULE", fontsize=10)
    p1.insert_text((100, 140), "C", fontsize=9)
    p1.insert_text((165, 140), schedule_size, fontsize=9)
    doc.save(str(pdf))
    doc.close()

    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", False)
    order = [SheetType.FLOOR_PLAN, SheetType.SCHEDULE]
    seen = {"i": 0}

    def fake_classify(page):
        t = order[min(seen["i"], len(order) - 1)]
        seen["i"] += 1
        return t

    monkeypatch.setattr(spatial, "classify_sheet", fake_classify)

    async def fake_detect(png, model):
        return VlmPage(
            entities=[VlmEntity(entity_type=AssetType.DOOR, name="", box=(245, 192, 291, 208))],
            scale_text=scale_text,
        )

    monkeypatch.setattr(spatial, "detect_page", fake_detect)
    row = {"id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
           "filename": "set.pdf", "path": str(pdf), "ingested": False}
    fake_repo.documents["doc-1"] = row

    async def emit(event):
        return None

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)
    doors = [a for a in fake_repo.assets.values() if a["type"] == "door"]
    # Only the plan door exists now — the schedule feeds the index, not assets.
    return min(doors, key=lambda a: a["bbox"][2] - a["bbox"][0])


async def test_opening_width_from_schedule_callout(tmp_path, fake_repo, monkeypatch):
    # Door tagged 'C', schedule C = 30"x80": the clear width is the schedule size
    # (30"), joined by the callout — overriding the measured 24" gap.
    door = await _ingest_two_page_set(
        tmp_path, fake_repo, monkeypatch,
        door_callout="C", schedule_size='30"x80"', scale_text='SCALE: 1/4" = 1\'-0"',
    )
    assert door["measurements"]["clear_width"] == 30.0
    assert door["source"] == "schedule"


async def test_unmatched_callout_falls_back_to_gap(tmp_path, fake_repo, monkeypatch):
    # Door tagged 'Z' (not in the schedule): no join, so the width falls back to
    # the measured wall gap (36pt × 48/72 = 24"), not the schedule's 30".
    door = await _ingest_two_page_set(
        tmp_path, fake_repo, monkeypatch,
        door_callout="Z", schedule_size='30"x80"', scale_text='SCALE: 1/4" = 1\'-0"',
    )
    assert door["measurements"]["clear_width"] == pytest.approx(24.0, abs=1.0)
    assert door["source"] != "schedule"


async def test_refuted_opening_is_dropped_and_snapped_uses_gap_width(tmp_path, fake_repo, monkeypatch):
    from planlint.ingest.sheet_type import SheetType
    from planlint.ingest.vlm import VlmEntity, VlmPage
    from planlint.models import AssetType

    pdf = tmp_path / "plan.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    # horizontal wall @ y=300 broken between x=350 and x=386 (36pt window gap)
    page.draw_line((200, 300), (350, 300))
    page.draw_line((386, 300), (520, 300))
    page.draw_line((350, 303), (386, 303))  # glazing in the gap
    doc.save(str(pdf))
    doc.close()

    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", False)
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    async def fake_detect(png, model):
        return VlmPage(
            entities=[
                VlmEntity(entity_type=AssetType.WINDOW, name="", box=(210, 292, 500, 308)),
            ],
            scale_text='SCALE: 1/4" = 1\'-0"',
        )

    monkeypatch.setattr(spatial, "detect_page", fake_detect)
    row = {"id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
           "filename": "plan.pdf", "path": str(pdf), "ingested": False}
    fake_repo.documents["doc-1"] = row

    async def emit(event):
        return None

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)
    windows = [a for a in fake_repo.assets.values() if a["type"] == "window"]
    assert len(windows) == 1
    w = windows[0]
    assert w["bbox"][2] - w["bbox"][0] == pytest.approx(36.0, abs=1.0)
    assert w["source"] == "vector-snapped"
