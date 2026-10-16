"""Cross-sheet references: deterministic grounding, proximity binding, and the
end-to-end asset→target-sheet link through offline ingestion."""

from __future__ import annotations

import pymupdf

from planlint.ingest import spatial
from planlint.ingest.references import ground_reference, nearest_asset
from planlint.ingest.sheet_type import SheetType
from planlint.models import AssetType, PhysicalAsset


# ------------------------------------------------------------------ grounding

def test_ground_reference_high_confidence_when_corroborated():
    r = ground_reference("section", "1", "A3.0", (10, 10, 20, 20), "1/A3.0 SECTION", {"A3.0": "SECTIONS"})
    assert r is not None and r.target_sheet_number == "A3.0" and r.confidence == 0.9


def test_ground_reference_medium_confidence_text_only():
    r = ground_reference("detail", "3", "A1.7", (0, 0, 5, 5), "SEE 3/A1.7", {})
    assert r is not None and r.confidence == 0.6


def test_ground_reference_rejects_ungrounded_target():
    assert ground_reference("detail", "3", "A9.9", (0, 0, 5, 5), "nothing here", {}) is None


def test_ground_reference_rejects_non_sheet_string():
    assert ground_reference("detail", "3", "see plan", (0, 0, 5, 5), "x", {"A1.7": "t"}) is None


# ------------------------------------------------------------------ proximity

def test_nearest_asset_binds_within_radius_only():
    door = PhysicalAsset(type=AssetType.DOOR, label="D1", bbox=(100, 100, 140, 110))
    far = PhysicalAsset(type=AssetType.WINDOW, label="", bbox=(500, 500, 520, 520))
    assert nearest_asset((120, 112, 130, 122), [door, far]) is door  # ~2pt away
    assert nearest_asset((300, 300, 310, 310), [door, far]) is None  # nothing in range


# --------------------------------------------------------- end-to-end linking

async def test_reference_links_asset_to_target_sheet(tmp_path, fake_repo, monkeypatch):
    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", True)
    pdf = tmp_path / "set.pdf"
    doc = pymupdf.open()
    p0 = doc.new_page(width=612, height=792)
    p0.insert_text((300, 200), 'D1 36"', fontsize=8)   # a door
    p0.insert_text((305, 214), "3/A1.7", fontsize=8)   # a detail callout beside it
    p1 = doc.new_page(width=612, height=792)
    p1.insert_text((100, 100), "STAIR DETAIL", fontsize=14)
    p1.insert_text((520, 740), "A1.7", fontsize=12)    # the target sheet's own number
    doc.save(str(pdf))
    doc.close()

    order = [SheetType.FLOOR_PLAN, SheetType.DETAIL]
    seen = {"i": 0}

    def fake_classify(page):
        t = order[min(seen["i"], len(order) - 1)]
        seen["i"] += 1
        return t

    monkeypatch.setattr(spatial, "classify_sheet", fake_classify)
    row = {"id": "doc-1", "project_id": "proj-1", "kind": "floorplan",
           "filename": "set.pdf", "path": str(pdf), "ingested": False}
    fake_repo.documents["doc-1"] = row

    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None)

    assert len(fake_repo.references) == 1
    ref = fake_repo.references[0]
    assert ref["target_sheet_number"] == "A1.7" and ref["kind"] == "detail"
    door = next(a for a in fake_repo.assets.values() if a["type"] == "door")
    assert ref["source_asset_id"] == door["id"]
