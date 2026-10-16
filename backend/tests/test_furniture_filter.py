"""Margin-furniture detection: page headers/footers the layout model failed
to classify, caught by their position in the page's margin bands."""

from planlint.ingest.semantic import _is_margin_furniture

PAGE_H = 792.0


def test_footer_line_in_bottom_band_is_furniture():
    # Real leaked footer: y 744-755 on a 792pt page.
    assert _is_margin_furniture("Titles II and III - 2010 Standards - 21", 744, 755, PAGE_H)


def test_running_head_in_top_band_is_furniture():
    assert _is_margin_furniture("Department of Justice", 20, 34, PAGE_H)
    assert _is_margin_furniture("Section 35.151 of 28 CFR Part 35", 40, 54, PAGE_H)


def test_body_text_mid_page_is_kept():
    assert not _is_margin_furniture("Department of Justice", 300, 314, PAGE_H)


def test_long_paragraph_low_on_page_is_kept():
    text = (
        "The obligation to provide an accessible path of travel may not be evaded "
        "by performing a series of small alterations to the area served by a single "
        "path of travel if those alterations could have been performed together."
    )
    assert not _is_margin_furniture(text, 730, 780, PAGE_H)


def test_clause_heading_low_on_page_is_kept():
    # A section heading can be typeset at the very bottom of a page; position
    # alone must not delete it.
    assert not _is_margin_furniture("404.2.3 Clear Width.", 745, 756, PAGE_H)


def test_multi_line_block_in_band_is_kept():
    assert not _is_margin_furniture("line one\nline two\nline three", 740, 780, PAGE_H)
