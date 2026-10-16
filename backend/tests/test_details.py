"""Detail localization + region-scoped harvest: the VLM boxes each numbered detail,
its number is grounded against the callout, and dimensions are read from that
detail's region only — isolating it from the other details on the sheet."""

from __future__ import annotations

import pymupdf
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from planlint.ingest import spatial
from planlint.ingest.details import detect_details
from planlint.ingest.harvest import harvest_measurements
from planlint.ingest.sheet_type import SheetType
from planlint.models import AssetType, Parameter

SCALE_TEXT = 'SCALE: 3/4" = 1\'-0"'  # 1 in = 4.5 pt


def _png_100x80() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 80))
    pix.clear_with(255)
    return pix.tobytes("png")


async def test_detect_details_boxes_to_points():
    args = {"details": [{"number": "3", "title": "STAIR SECTION", "box": [100, 100, 300, 400]}]}
    model = FunctionModel(lambda m, i: ModelResponse(parts=[ToolCallPart(i.output_tools[0].name, args)]))
    ds = await detect_details(_png_100x80(), model)
    assert len(ds) == 1 and ds[0].number == "3" and ds[0].title == "STAIR SECTION"
    # [ymin,xmin,ymax,xmax] 0-1000 → pixels(100x80) → points(zoom 2)
    assert ds[0].box == (5.0, 4.0, 20.0, 12.0)


def _two_region_page():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((60, 60), SCALE_TEXT, fontsize=9)
    page.draw_line((100, 300), (100 + 30 * 4.5, 300))  # detail A: 30" width
    page.insert_text((100, 294), '30"', fontsize=8)
    page.draw_line((350, 300), (350 + 36 * 4.5, 300))  # detail B: 36" width
    page.insert_text((350, 294), '36"', fontsize=8)
    return doc, page


def test_region_scoped_harvest_isolates_a_detail():
    doc, page = _two_region_page()
    # whole sheet: two clear-width candidates (30 and 36) → ambiguous → nothing
    assert harvest_measurements(page, {Parameter.CLEAR_WIDTH}) == {}
    # scoped to detail A's region → only its 30" is read
    got = harvest_measurements(page, {Parameter.CLEAR_WIDTH}, region=(90, 280, 260, 320))
    doc.close()
    assert got == {Parameter.CLEAR_WIDTH: 30.0}


async def test_detail_links_and_harvests_door(tmp_path, fake_repo, monkeypatch):
    from planlint.ingest.details import VlmDetail
    from planlint.ingest.vlm import VlmEntity, VlmPage, VlmReference

    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", False)
    pdf = tmp_path / "set.pdf"
    doc = pymupdf.open()
    p0 = doc.new_page(width=612, height=792)  # plan: a door + a callout to 3/A1.7
    p0.insert_text((305, 262), "3/A1.7", fontsize=8)
    p1 = doc.new_page(width=612, height=792)  # A1.7 detail sheet, detail 3 = a 30" width
    p1.insert_text((60, 60), SCALE_TEXT, fontsize=9)
    p1.insert_text((520, 740), "A1.7", fontsize=12)
    p1.draw_line((120, 300), (120 + 30 * 4.5, 300))
    p1.insert_text((120, 294), '30"', fontsize=8)
    doc.save(str(pdf))
    doc.close()

    async def fake_detect_page(png, model):
        return VlmPage(
            entities=[VlmEntity(entity_type=AssetType.DOOR, name="D1", box=(300, 200, 340, 260))],
            references=[VlmReference(kind="detail", detail_num="3", target_sheet="A1.7", box=(305, 262, 320, 272))],
        )

    async def fake_detect_details(png, model):
        # box already in PDF points (a region on A1.7 containing the 30" dim)
        return [VlmDetail(number="3", title="DOOR JAMB", box=(100, 250, 300, 400))]

    order = [SheetType.FLOOR_PLAN, SheetType.DETAIL]
    seen = {"i": 0}

    def fake_classify(page):
        t = order[min(seen["i"], len(order) - 1)]
        seen["i"] += 1
        return t

    monkeypatch.setattr(spatial, "detect_page", fake_detect_page)
    monkeypatch.setattr(spatial, "detect_details", fake_detect_details)
    monkeypatch.setattr(spatial, "classify_sheet", fake_classify)
    row = {"id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
           "filename": "set.pdf", "path": str(pdf), "ingested": False}
    fake_repo.documents["doc-1"] = row

    await spatial.ingest_floorplan(pdf, row, fake_repo, model="test")

    assert len(fake_repo.details) == 1
    dt = fake_repo.details[0]
    assert dt["sheet_number"] == "A1.7" and dt["number"] == "3"
    assert dt["measurements"] == {"clear_width": 30.0}
    door = next(a for a in fake_repo.assets.values() if a["type"] == "door")
    assert dt["source_asset_id"] == door["id"]
    assert door["measurements"] == {"clear_width": 30.0}
    assert door["source"] == "detail-referenced"
