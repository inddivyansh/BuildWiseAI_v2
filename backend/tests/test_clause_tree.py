"""Tests for the clause tree builder and the simple codebook parser."""

from planlint.ingest.clause_tree import TextBlock, build_clause_tree


def blocks(*texts_pages) -> list[TextBlock]:
    return [
        TextBlock(text=text, page=page, bbox=(0.0, float(i * 20), 100.0, float(i * 20 + 15)))
        for i, (text, page) in enumerate(texts_pages)
    ]


def test_three_level_nesting():
    clauses = build_clause_tree(
        blocks(
            ("404 Doors, Doorways, and Gates", 1),
            ("404.2 Manual Doors. Manual doors shall comply with 404.2.", 1),
            (
                "404.2.3 Clear Width. Door openings shall provide a clear width of "
                "32 inches (815 mm) minimum.",
                1,
            ),
        )
    )
    by_id = {c.clause_id: c for c in clauses}
    assert set(by_id) == {"404", "404.2", "404.2.3"}
    assert by_id["404"].parent_clause_id is None
    assert by_id["404.2"].parent_clause_id == "404"
    assert by_id["404.2.3"].parent_clause_id == "404.2"


def test_hierarchy_path_breadcrumb():
    clauses = build_clause_tree(
        blocks(
            ("404 Doors, Doorways, and Gates", 1),
            ("404.2 Manual Doors.", 1),
            ("404.2.3 Clear Width. 32 inches minimum.", 2),
        )
    )
    deep = next(c for c in clauses if c.clause_id == "404.2.3")
    assert deep.hierarchy_path == "404 Doors, Doorways, and Gates › 404.2 Manual Doors."
    assert deep.page == 2


def test_title_extracted():
    clauses = build_clause_tree(blocks(("404.2.3 Clear Width. Door openings shall...", 1)))
    assert clauses[0].title == "Clear Width"


def test_preamble_attaches_to_previous_clause():
    clauses = build_clause_tree(
        blocks(
            ("404.2.3 Clear Width. Openings shall provide 32 inches minimum.", 1),
            ("EXCEPTION: Door openings within hospital patient rooms.", 1),
        )
    )
    assert len(clauses) == 1
    assert "EXCEPTION" in clauses[0].text


def test_leading_preamble_without_clause_is_dropped():
    clauses = build_clause_tree(
        blocks(
            ("2010 ADA Standards for Accessible Design", 1),
            ("404 Doors, Doorways, and Gates", 1),
        )
    )
    assert [c.clause_id for c in clauses] == ["404"]


def test_gap_in_hierarchy_uses_longest_prefix():
    """404.2.3 with no 404.2 present parents to 404."""
    clauses = build_clause_tree(
        blocks(
            ("404 Doors", 1),
            ("404.2.3 Clear Width. 32 inches minimum.", 1),
        )
    )
    deep = next(c for c in clauses if c.clause_id == "404.2.3")
    assert deep.parent_clause_id == "404"


def test_numeric_table_values_are_not_clause_headers():
    """Zoning-style standards tables ('1.2 metres', '0 metres', '30 metres')
    must not start new clauses — they are measurement values belonging to the
    current clause's text."""
    clauses = build_clause_tree(
        blocks(
            ("1.2 INTERPRETATION", 1),
            ("1.2 metres and 1.2 metres", 1),
            ("0\n0 metres", 1),
            ("30 metres", 1),
            ("1 space for every 5 Units", 1),
        )
    )
    assert [c.clause_id for c in clauses] == ["1.2"]
    assert "0 metres" in clauses[0].text
    assert "30 metres" in clauses[0].text


def test_duplicate_clause_id_merges_instead_of_duplicating():
    """A re-encountered clause id (amended/repeated numbering) must neither
    produce duplicate output entries (the emitted clause count must equal the
    number of nodes written) nor silently discard the first occurrence."""
    clauses = build_clause_tree(
        blocks(
            ("7.6 Landscaping And Screening. Original requirements.", 1),
            ("7.6 Landscaping And Screening. Amended requirements.", 9),
        )
    )
    assert len(clauses) == 1
    assert "Original requirements" in clauses[0].text
    assert "Amended requirements" in clauses[0].text


def test_table_of_contents_lines_are_not_clause_headers():
    """TOC entries ('10.3 INTERPRETATION ......... 10-5') must not claim a
    section id before the real body section appears."""
    clauses = build_clause_tree(
        blocks(
            ("10.3 INTERPRETATION OF ZONE BOUNDARIES ................ 10-5", 1),
            ("1.2 \nINTERPRETATION ................................. 1-1", 1),
            ("10.3 INTERPRETATION OF ZONE BOUNDARIES. Where a zone boundary...", 40),
        )
    )
    assert [c.clause_id for c in clauses] == ["10.3"]
    assert clauses[0].page == 40  # the body section, not the TOC line


def test_clause_id_in_its_own_block_joins_next_block():
    """Word-layout PDFs often emit the section number and the section title
    as separate text blocks; they must be stitched into one header."""
    clauses = build_clause_tree(
        blocks(
            ("1.1 \nSHORT TITLE\nThese Regulations may be cited...", 1),
            ("1.2", 1),
            ("INTERPRETATION\n(1) Words and phrases used in these Regulations...", 1),
        )
    )
    assert [c.clause_id for c in clauses] == ["1.1", "1.2"]
    assert "Words and phrases" in clauses[1].text


def test_header_glued_to_previous_paragraph_still_starts_clause():
    """PyMuPDF often merges a section heading into the tail of the previous
    paragraph's block; the heading must still start its own clause."""
    clauses = build_clause_tree(
        blocks(
            ("1.1 \nSHORT TITLE", 1),
            ("These Regulations may be cited as the Regulations. \n \n1.2 \nINTERPRETATION", 1),
            ("(1) Words and phrases used in these Regulations shall have meaning.", 1),
        )
    )
    assert [c.clause_id for c in clauses] == ["1.1", "1.2"]
    assert "may be cited" in clauses[0].text
    assert "Words and phrases" in clauses[1].text


def test_provenance_preserved():
    clauses = build_clause_tree(blocks(("403.5.1 Clear Width. 36 inches minimum.", 7)))
    c = clauses[0]
    assert c.page == 7
    assert c.bbox is not None
