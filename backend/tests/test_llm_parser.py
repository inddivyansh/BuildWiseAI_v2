"""LLM markdown parser tests: the per-page router, the dimension-exact
verification seam, OCR gate for scans, per-page isolation, and markdown →
clause-tree assembly. FunctionModel-scripted — no live API or OCR.

Live smoke (manual, not CI): set a real GOOGLE_API_KEY, leave
PLANLINT_SEMANTIC_PARSER=auto, re-upload a codebook, and compare the clause tree
against samples/28_CFR_Part_36_Subpart_D.md.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from planlint.ingest import llm_parser
from planlint.ingest.llm_parser import (
    _PagePrep,
    _dimension_tokens,
    _items_from_markdown,
    _text_agreement,
    parse_codebook_llm,
    text_layer_to_markdown,
)

PAGE1_MD = """\
# § 36.401 New construction.

## (a) General.

Except as provided in paragraphs (b) and (c) of this section, discrimination \
includes a failure to design and construct accessible facilities.
"""
PAGE1_LAYER = (
    "§ 36.401 New construction. (a) General. Except as provided in paragraphs "
    "(b) and (c) of this section, discrimination includes a failure to design "
    "and construct accessible facilities."
)


def seq_model(outputs: list[str]) -> FunctionModel:
    """Returns `outputs` in order across calls (repeats the last), so a bad-then-
    good retry — or a persistently-bad model — can be scripted."""
    calls = {"n": 0}

    def model_fn(messages, info):
        i = min(calls["n"], len(outputs) - 1)
        calls["n"] += 1
        return ModelResponse(parts=[TextPart(outputs[i])])

    return FunctionModel(model_fn)


def raises_if_called() -> FunctionModel:
    def model_fn(messages, info):
        raise AssertionError("VLM must not be called for a clean text page")

    return FunctionModel(model_fn)


def fake_prep(monkeypatch, preps: list[_PagePrep]) -> None:
    monkeypatch.setattr(llm_parser, "_page_count", lambda _: len(preps))
    monkeypatch.setattr(llm_parser, "_prepare_page", lambda _p, i: preps[i])


def vlm_page(index: int, text_layer: str) -> _PagePrep:
    return _PagePrep(index, needs_vlm=True, text_layer=text_layer, png=b"png", markdown=None)


# ----------------------------------------------------------------- pure helpers

def test_text_agreement_metric():
    assert _text_agreement("the door is wide", "the door is wide") == 1.0
    assert _text_agreement("unrelated words entirely", "the door is wide") == 0.0
    assert _text_agreement("anything", "") == 1.0  # no reference: nothing to check
    assert _text_agreement("the door is wide", "the door is wide 20") >= 0.8


def test_dimension_tokens():
    assert _dimension_tokens('a clear width of 32" (815 mm) min') == {'32"', "815mm"}
    assert _dimension_tokens("ramp slope 1:12 and 8.3%") == {"1:12", "8.3%"}
    assert _dimension_tokens("36 inches") == {"36inches"}
    # Section ids and bare page numbers carry no unit — never flagged.
    assert _dimension_tokens("Section 404.2 on page 20") == set()


def test_items_from_markdown_blocks():
    md = "## (b) Located in private residences.\n\nText body.\n\n| A | B |\n| 1 | 2 |\n"
    items = _items_from_markdown(md, page_index=3)
    kinds = [i.kind for i in items]
    assert kinds == ["header", "text", "table"]
    assert all(i.page == 3 for i in items)


def test_items_from_markdown_records_heading_depth():
    items = _items_from_markdown("# A\n\n## B\n\n### C", page_index=0)
    assert [i.depth for i in items] == [1, 2, 3]


def test_text_layer_to_markdown_marks_clause_starts():
    text = "404.2 Manual Doors.\nDoors shall comply.\nGeneral prose line."
    md = text_layer_to_markdown(text)
    assert md.splitlines()[0] == "# 404.2 Manual Doors."
    assert "# Doors shall comply." not in md  # ordinary body stays body


# ------------------------------------------------------------------- the router

async def test_clean_page_skips_the_vlm(monkeypatch):
    # needs_vlm=False → built from prebuilt markdown, model never called.
    prep = _PagePrep(0, needs_vlm=False, text_layer="", png=None, markdown=PAGE1_MD)
    fake_prep(monkeypatch, [prep])
    clauses, unverified, failed = await parse_codebook_llm(
        llm_parser.Path("fake.pdf"), raises_if_called()
    )
    assert unverified == 0 and failed == []
    assert [c.clause_id for c in clauses] == ["36.401", "36.401.a"]


async def test_vlm_page_builds_clause_tree(monkeypatch):
    fake_prep(monkeypatch, [vlm_page(0, PAGE1_LAYER)])
    clauses, unverified, failed = await parse_codebook_llm(
        llm_parser.Path("fake.pdf"), seq_model([PAGE1_MD])
    )
    assert unverified == 0 and failed == []
    assert [c.clause_id for c in clauses] == ["36.401", "36.401.a"]


# -------------------------------------------------------- dimension-exact seam

async def test_flipped_dimension_retries_then_falls_back(monkeypatch):
    # Reference says 32"; the model keeps transcribing 36". Guard bounces every
    # retry; the page then falls back to the text layer (correct 32"), and is
    # reported failed — a wrong dimension never reaches the graph.
    layer = "404.2.3 Clear Width. Doors shall provide 32\" minimum clear width."
    bad = "# 404.2.3 Clear Width.\n\nDoors shall provide 36\" minimum clear width."
    fake_prep(monkeypatch, [vlm_page(0, layer)])
    clauses, unverified, failed = await parse_codebook_llm(
        llm_parser.Path("fake.pdf"), seq_model([bad])
    )
    assert failed == [0]
    text = " ".join(c.text for c in clauses)
    assert '32"' in text and '36"' not in text


async def test_matching_dimension_passes(monkeypatch):
    layer = "404.2.3 Clear Width. Doors shall provide 32\" minimum clear width."
    good = "# 404.2.3 Clear Width.\n\nDoors shall provide 32\" minimum clear width."
    fake_prep(monkeypatch, [vlm_page(0, layer)])
    clauses, unverified, failed = await parse_codebook_llm(
        llm_parser.Path("fake.pdf"), seq_model([good])
    )
    assert failed == [] and unverified == 0
    assert any('32"' in c.text for c in clauses)


# --------------------------------------------------------------- scans / OCR

async def test_scanned_page_verifies_against_ocr(monkeypatch):
    # No text layer → OCR supplies the reference; a matching transcription passes
    # and is NOT counted unverified.
    monkeypatch.setattr(llm_parser, "ocr_page_text", lambda _png: PAGE1_LAYER)
    fake_prep(monkeypatch, [_PagePrep(0, True, text_layer="", png=b"scan", markdown=None)])
    clauses, unverified, failed = await parse_codebook_llm(
        llm_parser.Path("fake.pdf"), seq_model([PAGE1_MD])
    )
    assert unverified == 0 and failed == []
    assert [c.clause_id for c in clauses] == ["36.401", "36.401.a"]


async def test_blank_scan_is_counted_unverified(monkeypatch):
    # No text layer AND OCR finds nothing → nothing to check against; the page is
    # kept but flagged unverified.
    monkeypatch.setattr(llm_parser, "ocr_page_text", lambda _png: "")
    fake_prep(monkeypatch, [_PagePrep(0, True, text_layer="", png=b"scan", markdown=None)])
    clauses, unverified, failed = await parse_codebook_llm(
        llm_parser.Path("fake.pdf"), seq_model([PAGE1_MD])
    )
    assert unverified == 1 and failed == []
    assert [c.clause_id for c in clauses] == ["36.401", "36.401.a"]
