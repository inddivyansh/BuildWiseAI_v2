"""Fixture/finish specs: parse a schedule into a code→Spec index, detect those
codes on a drawing (grounded against the index), and link them to the asset each
sits on through ingestion."""

from __future__ import annotations

import pymupdf

from planlint.ingest import spatial
from planlint.ingest.sheet_type import SheetType
from planlint.ingest.specs import detect_spec_codes, normalize_code, parse_spec_index
from planlint.models import AssetType, Spec


def test_normalize_code():
    assert normalize_code("X-40") == "X40"
    assert normalize_code("F60") == "F60"
    assert normalize_code("D1") is None       # one digit → a door mark, not a spec
    assert normalize_code("KITCHEN") is None


def _spec_page(sections):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    y = 100
    for title, rows in sections:
        page.insert_text((100, y), title, fontsize=10)
        y += 18
        for code, desc in rows:
            page.insert_text((100, y), code, fontsize=9)
            page.insert_text((165, y), desc, fontsize=9)
            y += 18
        y += 12
    return doc, page


def test_parse_spec_index():
    doc, page = _spec_page([
        ("FIXTURE SCHEDULE", [("X-00", "Blomberg BRFB1042SS"), ("X-40", "Jotul F3 CB")]),
        ("FINISH SCHEDULE", [("F-60", "Hardwood Floor")]),
    ])
    idx = parse_spec_index(page)
    doc.close()
    got = {c: (s.category, s.description) for c, s in idx.items()}
    assert got == {
        "X00": ("fixture", "Blomberg BRFB1042SS"),
        "X40": ("fixture", "Jotul F3 CB"),
        "F60": ("finish", "Hardwood Floor"),
    }


def test_detect_spec_codes_grounded():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((200, 300), "F-60", fontsize=9)  # a real code
    page.insert_text((200, 320), "Z-99", fontsize=9)  # not in the index → ignored
    idx = {"F60": Spec(code="F60", category="finish", description="Hardwood Floor")}
    got = detect_spec_codes(page, idx)
    doc.close()
    assert [c for c, _ in got] == ["F60"]


async def test_spec_links_asset(tmp_path, fake_repo, monkeypatch):
    from planlint.ingest.vlm import VlmEntity, VlmPage

    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", True)
    pdf = tmp_path / "set.pdf"
    doc = pymupdf.open()
    p0 = doc.new_page(width=612, height=792)  # plan with a finish code inside a room
    p0.insert_text((320, 300), "F-60", fontsize=9)
    p1 = doc.new_page(width=612, height=792)  # finish schedule
    p1.insert_text((100, 100), "FINISH SCHEDULE", fontsize=10)
    p1.insert_text((100, 140), "F-60", fontsize=9)
    p1.insert_text((165, 140), "Hardwood Floor", fontsize=9)
    doc.save(str(pdf))
    doc.close()

    def fake_from_labels(labels):
        return VlmPage(
            entities=[VlmEntity(entity_type=AssetType.ROOM, name="KITCHEN", box=(280, 260, 380, 340))]
        )

    order = [SheetType.FLOOR_PLAN, SheetType.SCHEDULE]
    seen = {"i": 0}

    def fake_classify(page):
        t = order[min(seen["i"], len(order) - 1)]
        seen["i"] += 1
        return t

    monkeypatch.setattr(spatial, "detect_from_labels", fake_from_labels)
    monkeypatch.setattr(spatial, "classify_sheet", fake_classify)
    row = {"id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
           "filename": "set.pdf", "path": str(pdf), "ingested": False}
    fake_repo.documents["doc-1"] = row

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None)

    assert len(fake_repo.spec_links) == 1
    link = fake_repo.spec_links[0]
    assert link["code"] == "F60" and link["description"] == "Hardwood Floor"
    room = next(a for a in fake_repo.assets.values() if a["type"] == "room")
    assert link["asset_id"] == room["id"]
