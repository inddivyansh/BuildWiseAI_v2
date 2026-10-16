"""Dimension harvest: read grounded dimensions off a detail region. Conservative —
a value is kept only when it's plausible for exactly one wanted parameter and the
sole such candidate, so non-compliant values are caught and ambiguity is skipped.
(The end-to-end asset enrichment via a referenced detail lives in test_details.py.)"""

from __future__ import annotations

import pymupdf

from planlint.ingest.harvest import harvest_measurements
from planlint.models import Parameter

# 3/4" = 1'-0"  →  scale = 16/72 in/pt  →  1 in = 4.5 pt.
SCALE_TEXT = 'SCALE: 3/4" = 1\'-0"'


def _detail_page(dims):
    """A page with a scale label and, per (text, value_in), a horizontal dimension
    line of the right length with the value printed just above it."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((60, 60), SCALE_TEXT, fontsize=9)
    y = 200
    for text, value in dims:
        page.draw_line((100, y), (100 + value * 4.5, y))
        page.insert_text((100, y - 6), text, fontsize=8)
        y += 60
    return doc, page


def test_harvest_single_grounded_value():
    doc, page = _detail_page([('7"', 7.0)])
    got = harvest_measurements(page, {Parameter.RISER_HEIGHT})
    doc.close()
    assert got == {Parameter.RISER_HEIGHT: 7.0}


def test_harvest_skips_ambiguous_riser_tread():
    # riser and tread ranges overlap — magnitude can't tell them apart, so a stair
    # carrying both harvests neither (→ NEEDS_REVIEW), never a mis-attributed number.
    doc, page = _detail_page([('7"', 7.0), ('11"', 11.0)])
    got = harvest_measurements(page, {Parameter.RISER_HEIGHT, Parameter.TREAD_DEPTH})
    doc.close()
    assert got == {}


def test_harvest_separates_width_from_height():
    # width and height ranges do NOT overlap, so both are attributed unambiguously.
    doc, page = _detail_page([('30"', 30.0), ('84"', 84.0)])
    got = harvest_measurements(page, {Parameter.CLEAR_WIDTH, Parameter.OPENING_HEIGHT})
    doc.close()
    assert got == {Parameter.CLEAR_WIDTH: 30.0, Parameter.OPENING_HEIGHT: 84.0}


def test_harvest_keeps_noncompliant_value():
    # a 24" door leaf is a real, sub-code width — it must be harvested so the checker
    # can flag the violation, NOT dropped for being out of a "typical" band.
    doc, page = _detail_page([('24"', 24.0)])
    got = harvest_measurements(page, {Parameter.CLEAR_WIDTH, Parameter.OPENING_HEIGHT})
    doc.close()
    assert got == {Parameter.CLEAR_WIDTH: 24.0}


def test_harvest_skips_when_ambiguous():
    # two values both in the riser range → can't tell which → nothing harvested
    doc, page = _detail_page([('7"', 7.0), ('6"', 6.0)])
    got = harvest_measurements(page, {Parameter.RISER_HEIGHT})
    doc.close()
    assert got == {}


def test_harvest_rejects_misread_only():
    doc, page = _detail_page([('200"', 200.0)])  # a misread, not a plausible riser
    got = harvest_measurements(page, {Parameter.RISER_HEIGHT})
    doc.close()
    assert got == {}


def test_harvest_needs_scale():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.draw_line((100, 200), (131.5, 200))
    page.insert_text((100, 194), '7"', fontsize=8)  # no SCALE label on the page
    got = harvest_measurements(page, {Parameter.RISER_HEIGHT})
    doc.close()
    assert got == {}
