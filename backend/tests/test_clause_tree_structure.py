"""Tests for the structure-driven clause builder (layout-aware parser path),
plus a real-Docling parse of the bundled ADA sample."""

from pathlib import Path

import pytest

from planlint.ingest.clause_tree import StructuredItem, build_clause_tree_from_structure

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def header(text: str, page: int = 0) -> StructuredItem:
    return StructuredItem(kind="header", text=text, page=page)


def text(content: str, page: int = 0) -> StructuredItem:
    return StructuredItem(kind="text", text=content, page=page)


def table(content: str, page: int = 0) -> StructuredItem:
    return StructuredItem(kind="table", text=content, page=page)


def dheader(text: str, depth: int, page: int = 0) -> StructuredItem:
    return StructuredItem(kind="header", text=text, page=page, depth=depth)


def test_depth_nests_chapter_and_section():
    # 'Chapter 4' → id '4', '404 Doors' → id '404'; they share no dotted prefix,
    # so numbering alone leaves 404 a root. Heading depth nests it under Chapter 4.
    clauses = build_clause_tree_from_structure(
        [
            dheader("CHAPTER 4 – ACCESSIBLE ROUTES", depth=1),
            dheader("404 Doors", depth=2),
            text("Doors shall comply with 404."),
        ]
    )
    by_id = {c.clause_id: c for c in clauses}
    assert by_id["404"].parent_clause_id == "4"


def test_depth_nests_unnumbered_outline():
    clauses = build_clause_tree_from_structure(
        [
            dheader("PART A GENERAL PROVISIONS", depth=1),
            dheader("ADMINISTRATION", depth=2),
            dheader("SCOPE", depth=3),
        ]
    )
    by_id = {c.clause_id: c for c in clauses}
    assert by_id["ADMINISTRATION"].parent_clause_id == "PART A GENERAL PROVISIONS"
    assert by_id["SCOPE"].parent_clause_id == "ADMINISTRATION"


def test_numeric_parent_wins_over_misleading_depth():
    # 404.2.3 has a real numeric parent (404.2); a flat/misleading depth must not
    # override it.
    clauses = build_clause_tree_from_structure(
        [
            dheader("404.2 Manual Doors", depth=1),
            dheader("404.2.3 Clear Width", depth=1),
        ]
    )
    by_id = {c.clause_id: c for c in clauses}
    assert by_id["404.2.3"].parent_clause_id == "404.2"


def test_depth_none_preserves_flat_behavior():
    # Backward-compat: without depth, Chapter 4 and 404 stay siblings (roots) —
    # exactly today's behavior.
    clauses = build_clause_tree_from_structure(
        [header("CHAPTER 4 – ACCESSIBLE ROUTES"), header("404 Doors"), text("body")]
    )
    by_id = {c.clause_id: c for c in clauses}
    assert by_id["404"].parent_clause_id is None


def test_numbered_headers_with_body_and_tables():
    clauses = build_clause_tree_from_structure(
        [
            header("1.1 \nSHORT TITLE"),
            text("These Regulations may be cited as the Regulations."),
            header("1.2 INTERPRETATION"),
            text("Words and phrases shall have the meaning ascribed in Section 2."),
            table("| Lot Area (minimum) | 450 metres square |"),
        ]
    )
    assert [c.clause_id for c in clauses] == ["1.1", "1.2"]
    assert "may be cited" in clauses[0].text
    assert "Lot Area (minimum)" in clauses[1].text  # table attached, not a section


def test_section_word_header_provides_parent():
    clauses = build_clause_tree_from_structure(
        [
            header("SECTION 1 – TITLE AND APPLICATION"),
            header("1.1 SHORT TITLE"),
            text("These Regulations..."),
        ]
    )
    assert [c.clause_id for c in clauses] == ["1", "1.1"]
    assert clauses[0].title == "TITLE AND APPLICATION"
    assert clauses[1].parent_clause_id == "1"


def test_zone_code_header_joins_title_header():
    clauses = build_clause_tree_from_structure(
        [
            header("10.95 ZONE TABLES"),
            header("AG"),
            header("AGRICULTURE (AG) ZONE"),
            text("(1) PERMITTED USES"),
            table("| Accessory Building | Public Use |"),
            header("O"),
            header("OPEN SPACE (O) ZONE"),
            text("(3) ZONE STANDARDS SHALL BE IN THE DISCRETION OF COUNCIL."),
        ]
    )
    ids = [c.clause_id for c in clauses]
    assert ids == ["10.95", "AG", "O"]
    ag = clauses[1]
    assert ag.title == "AGRICULTURE (AG) ZONE"
    assert "PERMITTED USES" in ag.text
    assert "Accessory Building" in ag.text


def test_toc_section_and_dot_leader_headers_are_skipped():
    clauses = build_clause_tree_from_structure(
        [
            header("Table of Contents"),
            text("1.1 SHORT TITLE ................. 1-1"),
            header("1.2 INTERPRETATION .............. 1-1"),  # misclassified TOC line
            header("1.1 SHORT TITLE"),
            text("These Regulations may be cited..."),
        ]
    )
    assert [c.clause_id for c in clauses] == ["1.1"]
    assert clauses[0].page == 0
    assert "may be cited" in clauses[0].text
    assert "1-1" not in clauses[0].text  # TOC entries never attached


def test_addresses_in_body_text_do_not_become_sections():
    clauses = build_clause_tree_from_structure(
        [
            header("10.7 SITE-SPECIFIC AMENDMENTS"),
            text("485 Topsail Road (PID #46960). 52 metres (2024-07-19)"),
            text("1 Bull\n1000 Broiler Chickens or roasters (1.8-2.3 kg each)"),
        ]
    )
    assert [c.clause_id for c in clauses] == ["10.7"]
    assert "485 Topsail Road" in clauses[0].text


def test_paren_numbered_headers_scope_under_their_zone():
    """'(1) PERMITTED USES' repeats in every zone — each must become a child
    of its own zone, never merge across zones."""
    clauses = build_clause_tree_from_structure(
        [
            header("RURAL RESIDENTIAL (RR) ZONE"),
            header("(1) PERMITTED USES"),
            text("Single Detached Dwelling"),
            header("(2) DISCRETIONARY USES"),
            text("Home Occupation"),
            header("RURAL RESIDENTIAL INFILL (RRI) ZONE"),
            header("(1) PERMITTED USES"),
            text("Accessory Building"),
        ]
    )
    assert [c.clause_id for c in clauses] == ["RR", "RR.1", "RR.2", "RRI", "RRI.1"]
    assert clauses[1].parent_clause_id == "RR"
    assert clauses[4].parent_clause_id == "RRI"
    assert "Single Detached" in clauses[1].text
    assert "Accessory Building" in clauses[4].text


def test_duplicate_header_ids_merge():
    clauses = build_clause_tree_from_structure(
        [
            header("7.6 LANDSCAPING AND SCREENING"),
            text("Original requirements."),
            header("7.6 LANDSCAPING AND SCREENING"),
            text("Amended requirements."),
        ]
    )
    assert len(clauses) == 1
    assert "Original requirements" in clauses[0].text
    assert "Amended requirements" in clauses[0].text


def test_run_in_numbered_sections_split_out_of_body_text():
    """Docling emits ADA-style run-in headings ('306.3.1 General. …') as plain
    text; each numbered section must still become its own clause — the
    smallest numbered item is the clause."""
    clauses = build_clause_tree_from_structure(
        [
            header("306.3 Knee Clearance."),
            text(
                "306.3.1 General. Space under an element between 9 inches (230 mm) "
                "and 27 inches (685 mm) shall be considered knee clearance.\n"
                "306.3.2 Maximum Depth. Knee clearance shall extend 25 inches "
                "(635 mm) maximum under an element."
            ),
        ]
    )
    assert [c.clause_id for c in clauses] == ["306.3", "306.3.1", "306.3.2"]
    assert clauses[1].parent_clause_id == "306.3"
    assert clauses[2].parent_clause_id == "306.3"
    assert clauses[1].title == "General"
    assert "knee clearance" in clauses[1].text
    assert "Maximum Depth" not in clauses[1].text  # bodies don't bleed


def test_chapter_header_with_run_in_section_text():
    """User-reported: 'CHAPTER 3' header swallowed '302.3 Openings. …' body.
    The section must split into its own clause; figure captions attach to it."""
    clauses = build_clause_tree_from_structure(
        [
            header("CHAPTER 3: BUILDING BLOCKS"),
            text(
                "302.3 Openings. Openings in floor or ground surfaces shall not "
                "allow passage of a sphere more than 1/2 inch (13 mm) diameter.\n"
                "Figure 302.3 Elongated Openings in Floor or Ground Surfaces"
            ),
        ]
    )
    assert [c.clause_id for c in clauses] == ["3", "302.3"]
    assert "Figure 302.3" in clauses[1].text
    assert "Openings" not in clauses[0].text  # chapter keeps only its own text


def test_text_split_guards_reject_measurements_years_and_toc_lines():
    clauses = build_clause_tree_from_structure(
        [
            header("306.3 Knee Clearance."),
            text("30 inches (760 mm) wide minimum."),
            text("2010 Standards apply to this facility."),
            text("305 Clear Floor or Ground Space ......... 112"),
        ]
    )
    assert [c.clause_id for c in clauses] == ["306.3"]
    assert "30 inches" in clauses[0].text  # appended, not split


def test_contents_header_enters_toc_mode():
    """The 2010 Standards title their TOC 'CONTENTS' — it must be treated as a
    table of contents, not become a clause with leaked entries."""
    clauses = build_clause_tree_from_structure(
        [
            header("CONTENTS"),
            text("28 CFR Part 35\n35.151 New construction and alterations"),
            header("306.3 Knee Clearance."),
            text("Space under an element shall comply."),
        ]
    )
    assert [c.clause_id for c in clauses] == ["306.3"]
    assert "CFR" not in clauses[0].text


def test_section_mark_headers_become_clauses():
    """CFR-style '§ 35.151 …' headers must yield the section number as id."""
    clauses = build_clause_tree_from_structure(
        [
            header("§ 35.151 New construction and alterations."),
            text("Each facility shall be designed and constructed accessibly."),
        ]
    )
    assert [c.clause_id for c in clauses] == ["35.151"]
    assert clauses[0].title == "New construction and alterations."
    assert "designed and constructed" in clauses[0].text


def test_cfr_running_title_does_not_become_clause_28():
    """'28 CFR part 35.151 …' is a citation-style division title, not a
    section numbered 28."""
    clauses = build_clause_tree_from_structure(
        [
            header("§ 35.150 Program accessibility."),
            header("28 CFR part 35.151 New Construction and Alterations"),
            text("Body under the running title."),
        ]
    )
    assert [c.clause_id for c in clauses] == ["35.150"]
    assert "Body under" in clauses[0].text


def test_cfr_letter_and_digit_paragraph_nesting():
    """(a)/(b) letter paragraphs nest under the § section; digit paragraphs
    nest under the active letter — matching CFR citation 35.151(a)(2)."""
    clauses = build_clause_tree_from_structure(
        [
            header("§ 35.151 New construction and alterations."),
            header("(a) Design and construction."),
            text("Each facility shall be readily accessible."),
            header("(2) Exception for structural impracticability."),
            text("Full compliance is not required where structurally impracticable."),
            header("(b) Alterations."),
            text("Each altered facility shall comply."),
        ]
    )
    ids = [c.clause_id for c in clauses]
    assert ids == ["35.151", "35.151.a", "35.151.a.2", "35.151.b"]
    by_id = {c.clause_id: c for c in clauses}
    assert by_id["35.151.a"].parent_clause_id == "35.151"
    assert by_id["35.151.a.2"].parent_clause_id == "35.151.a"
    assert by_id["35.151.b"].parent_clause_id == "35.151"
    assert "readily accessible" in by_id["35.151.a"].text
    assert "structurally impracticable" in by_id["35.151.a.2"].text


def test_toc_table_is_dropped_but_content_table_attaches():
    clauses = build_clause_tree_from_structure(
        [
            header("§ 36.406 Standards for new construction and alterations."),
            table(
                "| (a) Accessibility standards......……………..................26 |\n"
                "| (b) Scope of coverage......…….........................……......27 |\n"
                "| (c) Places of lodging......…….........................…........28 |"
            ),
            table("| Compliance Dates | Applicable Standards |\n| On or after March 15, 2012 | 2010 Standards |"),
        ]
    )
    assert len(clauses) == 1
    assert "Places of lodging" not in clauses[0].text  # TOC table dropped
    assert "Applicable Standards" in clauses[0].text  # content table kept


def test_toc_title_as_text_item_enters_toc_mode():
    clauses = build_clause_tree_from_structure(
        [
            header("239 Miniature Golf Facilities"),
            text("TABLE OF CONTENTS"),
            text("101 Purpose\n102 Dimensions for Adults and Children"),
            header("240 Play Areas"),
            text("Play areas shall comply."),
        ]
    )
    assert [c.clause_id for c in clauses] == ["239", "240"]
    assert "101 Purpose" not in clauses[0].text
    assert "shall comply" in clauses[1].text


def test_running_head_citation_does_not_merge_or_reset_scope():
    """'Section 35.151 of 28 CFR Part 35' is a per-page running head. It must
    not merge into clause 35.151 (splitting content and resetting letter
    scope at every page break) — it must vanish entirely."""
    clauses = build_clause_tree_from_structure(
        [
            header("§ 35.151 New construction and alterations."),
            header("(a) Design and construction."),
            text("Each facility shall be accessible."),
            header("(b) Alterations."),
            text("Each altered facility shall comply."),
            header("(c) Accessibility standards and compliance date."),
            text("If physical construction commences after July 26, 1992, comply."),
            header("Section 35.151 of 28 CFR Part 35"),  # page-break running head
            text("If physical construction commences on or after September 15, 2010, comply."),
            header("(5) Noncomplying new construction and alterations."),
            text("Newly constructed facilities shall be made accessible."),
        ]
    )
    ids = [c.clause_id for c in clauses]
    assert ids == ["35.151", "35.151.a", "35.151.b", "35.151.c", "35.151.c.5"]
    by_id = {c.clause_id: c for c in clauses}
    assert "September 15, 2010" in by_id["35.151.c"].text
    assert "September 15, 2010" not in by_id["35.151"].text
    assert "Section 35.151 of 28 CFR Part 35" not in "".join(c.text for c in clauses)


def test_index_pages_are_dropped_until_a_numbered_section():
    """Back-of-book index: mixed-case entry headers, letter dividers, and
    entry text must all be dropped; only a real numbered heading resumes."""
    clauses = build_clause_tree_from_structure(
        [
            header("§ 36.406 Standards for new construction and alterations."),
            text("Standards body."),
            header("Index and List of Figures"),
            header("Remodeling"),
            text("Application and Scoping 21, 23, 44"),
            header("T"),  # alphabetical divider — must not become a zone code
            header("Team or Player Seating"),
            text("Application and Scoping 64, 79, 80"),
            header("§ 36.401 New construction."),
            text("New construction body."),
        ]
    )
    assert [c.clause_id for c in clauses] == ["36.406", "36.401"]
    joined = "\n".join(c.text for c in clauses)
    assert "Application and Scoping" not in joined
    assert "New construction body" in clauses[1].text


def test_letter_paragraphs_must_advance_alphabetically():
    """Roman-numeral parens ('(v)') must not masquerade as letter paragraphs:
    a letter clause forms only when it continues the a, b, c… sequence."""
    clauses = build_clause_tree_from_structure(
        [
            header("§ 35.151 New construction and alterations."),
            header("(a) Design and construction."),
            text("A body."),
            header("(b) Alterations."),
            text("B body."),
            header("(v) Series of smaller alterations."),  # roman (b)(4)(v)
            text("Roman body."),
        ]
    )
    ids = [c.clause_id for c in clauses]
    assert ids == ["35.151", "35.151.a", "35.151.b"]
    assert "Roman body" in clauses[2].text  # attached to (b), not a clause


def test_letter_gap_is_conservative():
    """A letter that skips the sequence (missing header for (b)) stays in the
    body — under-splitting beats inventing a wrong clause id."""
    clauses = build_clause_tree_from_structure(
        [
            header("§ 36.402 Alterations."),
            header("(a) General."),
            text("A body."),
            header("(c) To the maximum extent feasible."),
            text("C body."),
        ]
    )
    assert [c.clause_id for c in clauses] == ["36.402", "36.402.a"]
    assert "C body" in clauses[1].text


def test_no_headers_returns_empty_for_fallback():
    clauses = build_clause_tree_from_structure(
        [text("404.2.3 Clear Width. Door openings shall provide 32 inches minimum.")]
    )
    assert clauses == []


@pytest.mark.docling
def test_docling_parses_ada_sample_end_to_end():
    """Real Docling over the bundled ADA excerpt: the layout-aware path must
    recover the numbered sections the pipeline's golden flow relies on."""
    pytest.importorskip("docling")
    from planlint.ingest.semantic import parse_codebook

    pdf = SAMPLES / "ada_excerpt.pdf"
    if not pdf.exists():
        pytest.skip("sample PDFs not generated")
    clauses = parse_codebook(pdf, "docling")
    ids = {c.clause_id for c in clauses}
    assert {"404.2.3", "404.2.5", "404.2.9", "403.5.1"} <= ids
    by_id = {c.clause_id: c for c in clauses}
    assert "32 inches" in by_id["404.2.3"].text
    assert len(ids) == len(clauses)  # no duplicates


@pytest.mark.docling
def test_docling_chunked_parse_matches_unchunked():
    """Chunked conversion (the memory-bounding path for large codebooks) must
    yield the same clause tree as a single-call parse — a chunk boundary can
    never change what gets ingested."""
    pytest.importorskip("docling")
    from planlint.ingest.clause_tree import build_clause_tree_from_structure
    from planlint.ingest.semantic import _docling_items

    pdf = SAMPLES / "ada_excerpt.pdf"
    if not pdf.exists():
        pytest.skip("sample PDFs not generated")
    # The sample is 2 pages: chunk_pages=1 forces a chunk boundary mid-document.
    chunked = build_clause_tree_from_structure(_docling_items(pdf, chunk_pages=1))
    whole = build_clause_tree_from_structure(_docling_items(pdf, chunk_pages=10_000))
    assert [(c.clause_id, c.page, c.text) for c in chunked] == [
        (c.clause_id, c.page, c.text) for c in whole
    ]
    # Worker windows concatenate the same way (per-worker page_span slices).
    split = _docling_items(pdf, page_span=(1, 1)) + _docling_items(pdf, page_span=(2, 2))
    windowed = build_clause_tree_from_structure(split)
    assert [(c.clause_id, c.page, c.text) for c in windowed] == [
        (c.clause_id, c.page, c.text) for c in whole
    ]


@pytest.mark.docling
def test_run_in_sections_split_on_real_2010_standards():
    """The user-reported under-segmentation: on the real 2010 ADA Standards,
    302.3 must not vanish into CHAPTER 3, and 306.3 must not swallow its
    306.3.x children — the smallest numbered item is the clause."""
    pytest.importorskip("docling")
    from planlint.ingest.semantic import _docling_items

    pdf = SAMPLES / "2010-design-standards.pdf"
    if not pdf.exists():
        pytest.skip("2010-design-standards.pdf not present")
    items = _docling_items(pdf, page_span=(105, 118))
    ids = {c.clause_id for c in build_clause_tree_from_structure(items)}
    assert "302.3" in ids
    assert {"306.3.1", "306.3.2", "306.3.3", "306.3.4", "306.3.5"} <= ids


@pytest.mark.docling
def test_cfr_sections_and_furniture_on_real_2010_standards():
    """User-reported: § sections mangled and running heads leaked. On the real
    CFR pages, § 35.151 and its letter paragraphs must be clauses, the junk
    '28' clause must not exist, and furniture must not pollute clause text."""
    pytest.importorskip("docling")
    from planlint.ingest.semantic import _docling_items

    pdf = SAMPLES / "2010-design-standards.pdf"
    if not pdf.exists():
        pytest.skip("2010-design-standards.pdf not present")
    clauses = build_clause_tree_from_structure(_docling_items(pdf, page_span=(10, 16)))
    ids = {c.clause_id for c in clauses}
    assert "35.151" in ids
    assert "35.151.a" in ids
    assert "28" not in ids
    joined = "\n".join(c.text for c in clauses)
    assert "Department of Justice" not in joined
    assert "2010 Standards: Title II" not in joined


@pytest.mark.docling
def test_footer_not_in_clause_text_on_real_2010_standards():
    pytest.importorskip("docling")
    from planlint.ingest.semantic import _docling_items

    pdf = SAMPLES / "2010-design-standards.pdf"
    if not pdf.exists():
        pytest.skip("2010-design-standards.pdf not present")
    clauses = build_clause_tree_from_structure(_docling_items(pdf, page_span=(54, 56)))
    joined = "\n".join(c.text for c in clauses)
    assert "202.4" in {c.clause_id for c in clauses}
    assert "Titles II and III - 2010 Standards" not in joined


@pytest.mark.docling
def test_running_heads_and_scope_on_real_cfr_pages():
    """Pages 13–15 carry the 'Section 35.151 of 28 CFR Part 35' running head
    at each page top: (c)'s paragraphs must stay under 35.151.c and (5) must
    become 35.151.c.5, not 35.151.5."""
    pytest.importorskip("docling")
    from planlint.ingest.semantic import _docling_items

    pdf = SAMPLES / "2010-design-standards.pdf"
    if not pdf.exists():
        pytest.skip("2010-design-standards.pdf not present")
    clauses = build_clause_tree_from_structure(_docling_items(pdf, page_span=(10, 15)))
    ids = {c.clause_id for c in clauses}
    assert "35.151.c" in ids
    assert "35.151.c.5" in ids
    assert "35.151.5" not in ids
    by_id = {c.clause_id: c for c in clauses}
    assert "September 15, 2010" not in by_id["35.151"].text


@pytest.mark.docling
def test_index_pages_do_not_leak_on_real_2010_standards():
    pytest.importorskip("docling")
    from planlint.ingest.semantic import _docling_items

    pdf = SAMPLES / "2010-design-standards.pdf"
    if not pdf.exists():
        pytest.skip("2010-design-standards.pdf not present")
    clauses = build_clause_tree_from_structure(_docling_items(pdf, page_span=(268, 270)))
    joined = "\n".join(c.text for c in clauses)
    assert "Application and Scoping" not in joined


def test_isolated_simple_parse_stays_in_process():
    """The lightweight simple parser must not pay the subprocess tax — and its
    output must match a direct parse."""
    from planlint.ingest.semantic import parse_codebook, parse_codebook_isolated

    pdf = SAMPLES / "ada_excerpt.pdf"
    if not pdf.exists():
        pytest.skip("sample PDFs not generated")
    direct = parse_codebook(pdf, "simple")
    isolated = parse_codebook_isolated(pdf, "simple")
    assert [(c.clause_id, c.text) for c in isolated] == [
        (c.clause_id, c.text) for c in direct
    ]


@pytest.mark.docling
def test_isolated_docling_parse_round_trips_through_subprocess():
    """The Docling path runs in a spawn subprocess (memory isolation): clauses
    must pickle back intact across the process boundary."""
    pytest.importorskip("docling")
    from planlint.ingest.semantic import parse_codebook_isolated

    pdf = SAMPLES / "ada_excerpt.pdf"
    if not pdf.exists():
        pytest.skip("sample PDFs not generated")
    clauses = parse_codebook_isolated(pdf, "docling")
    ids = {c.clause_id for c in clauses}
    assert {"404.2.3", "404.2.5", "404.2.9", "403.5.1"} <= ids
