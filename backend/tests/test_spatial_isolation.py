"""Per-page isolation in floor-plan ingestion: a page whose detection blows up
(an elevation, a section, or any page the VLM can't box) is skipped with a
warning and recorded as an empty sheet — it never aborts the whole document.
No real vision calls; detect_page is monkeypatched."""

from __future__ import annotations

import pymupdf
import pytest

from planlint.ingest import spatial
from planlint.ingest.sheet_type import SheetType
from planlint.ingest.vlm import VlmPage


def _blank_pdf(path, pages: int = 3) -> None:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)
    doc.save(str(path))
    doc.close()


def _doc_row(pdf) -> dict:
    return {
        "id": "doc-1",
        "project_id": "proj-1",
        "kind": "floorplan",
        "filename": "set.pdf",
        "path": str(pdf),
        "ingested": False,
    }


async def _ingest(pdf, fake_repo, emit):
    row = _doc_row(pdf)
    fake_repo.documents[row["id"]] = row  # mark_ingested looks the row up at the end
    await spatial.ingest_floorplan(pdf, row, fake_repo, model=None, emit=emit)


async def test_one_bad_page_does_not_abort_the_run(tmp_path, fake_repo, monkeypatch):
    pdf = tmp_path / "set.pdf"
    _blank_pdf(pdf, pages=3)
    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", False)
    # Force the plan-view branch so detection runs on every (blank) page.
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    calls = {"n": 0}

    async def fake_detect(png, model):
        index = calls["n"]
        calls["n"] += 1
        if index == 1:  # the middle page mimics an exhausted-retries failure
            raise RuntimeError("Exceeded maximum output retries (2)")
        return VlmPage(entities=[], scale_text=None)

    monkeypatch.setattr(spatial, "detect_page", fake_detect)

    events = []

    async def emit(event):
        events.append(event)

    await _ingest(pdf, fake_repo, emit)

    # Every page is recorded — including the one that failed — and the document
    # is marked ingested despite the mid-run failure.
    assert len(fake_repo.sheets) == 3
    assert fake_repo.documents["doc-1"]["ingested"] is True

    warnings = [e for e in events if e.level == "warning"]
    assert any("Page 2" in w.message and "skipped" in w.message for w in warnings)
    # The skipped page has no scale and no assets; the others were still handled.
    skipped = next(s for s in fake_repo.sheets.values() if s["page_number"] == 1)
    assert skipped["scale_in_per_point"] is None


async def test_all_pages_failing_still_completes(tmp_path, fake_repo, monkeypatch):
    pdf = tmp_path / "set.pdf"
    _blank_pdf(pdf, pages=3)
    monkeypatch.setattr(spatial.settings, "planlint_offline_sample", False)
    monkeypatch.setattr(spatial, "classify_sheet", lambda page: SheetType.FLOOR_PLAN)

    async def always_fail(png, model):
        raise RuntimeError("boom")

    monkeypatch.setattr(spatial, "detect_page", always_fail)

    events = []

    async def emit(event):
        events.append(event)

    # No exception escapes: a document of all-unreadable pages is a clean,
    # asset-free ingest, not a crashed run.
    await _ingest(pdf, fake_repo, emit)

    assert len(fake_repo.sheets) == 3
    assert fake_repo.assets == {}
    assert fake_repo.documents["doc-1"]["ingested"] is True
    assert sum(1 for e in events if e.level == "warning") == 3
