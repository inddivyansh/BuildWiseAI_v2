"""Sheet-type classification from drawing titles. Uses the real title strings
observed in a 25-page construction set, so the router is exercised against
actual title-block text without needing the PDF."""

from __future__ import annotations

import pymupdf
import pytest

from planlint.ingest.sheet_type import SheetType, classify_sheet, classify_titles


@pytest.mark.parametrize(
    "titles, expected",
    [
        (["1ST FLOOR PLAN"], SheetType.FLOOR_PLAN),
        (["2ND FLOOR PLAN"], SheetType.FLOOR_PLAN),
        (["FOUNDATION PLAN"], SheetType.FOUNDATION),
        (["ROOF PLAN"], SheetType.ROOF),
        (["SITE / LANDSCAPE PLAN"], SheetType.SITE),
        (
            ["1ST FLOOR - REFLECTED CEILING PLAN & ELECTRICAL PLAN"],
            SheetType.RCP_ELECTRICAL,
        ),
        (["BACK ELEVATION (NORTH)"], SheetType.ELEVATION),
        (["BATHROOM ELEVATION", "BATHROOM ELEVATION"], SheetType.ELEVATION),
        (["LONGITUDINAL SECTION"], SheetType.SECTION),
        (["TRANSVERSE SECTION THROUGH CENTRAL HALL"], SheetType.SECTION),
        (["STAIR SECTION DETAIL", "FOUNDATION DETAIL"], SheetType.DETAIL),
        (["MATERIALS SCHEDULE", "FIXTURE & EQUIPMENT SCHEDULE"], SheetType.SCHEDULE),
        # Plural detail titles (a plain \bDETAIL\b would miss the trailing S).
        (["TYPICAL DETAILS"], SheetType.DETAIL),
        (["DECORATIVE DETAILS"], SheetType.DETAIL),
        # An openings schedule titled by its contents, not the word "schedule".
        (["DOORS & WINDOWS"], SheetType.SCHEDULE),
        (["DOOR & WINDOW SCHEDULE"], SheetType.SCHEDULE),
        # A line-broken title arrives as two title lines.
        (["DOORS &", "WINDOWS"], SheetType.SCHEDULE),
        (["GENERAL NOTES"], SheetType.COVER_NOTES),
    ],
)
def test_classifies_real_titles(titles, expected):
    assert classify_titles(titles) == expected


def _titled_pdf(path, title: str, callouts: list[str]) -> None:
    """A one-page PDF with a large title and small callouts, so the font-tier
    title extraction is exercised end to end."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), title, fontsize=18)
    for i, callout in enumerate(callouts):
        page.insert_text((72, 300 + i * 20), callout, fontsize=8)
    doc.save(str(path))
    doc.close()


def test_plural_detail_title_is_picked_over_small_callouts(tmp_path):
    pdf = tmp_path / "d.pdf"
    _titled_pdf(pdf, "TYPICAL DETAILS", ["DOOR", "SEE 3/A4.1", "WINDOW"])
    doc = pymupdf.open(str(pdf))
    assert classify_sheet(doc[0]) == SheetType.DETAIL


def test_doors_and_windows_title_routes_to_schedule(tmp_path):
    pdf = tmp_path / "dw.pdf"
    _titled_pdf(pdf, "DOORS & WINDOWS", ["D1", "W2"])
    doc = pymupdf.open(str(pdf))
    assert classify_sheet(doc[0]) == SheetType.SCHEDULE


def test_notes_prose_is_not_mistaken_for_a_schedule(tmp_path):
    # A general-notes sheet's body sentences mention doors/windows at the same
    # font as the title; prose must not turn it into a schedule.
    pdf = tmp_path / "notes.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for i, line in enumerate(
        [
            "GENERAL NOTES",
            "6. DOORS OR FRAMED OPENINGS SHALL BE LABELED.",
            "30. DO NOT USE NON-OPERABLE WINDOW SHUTTERS.",
        ]
    ):
        page.insert_text((72, 100 + i * 20), line, fontsize=10)
    doc.save(str(pdf))
    doc.close()
    assert classify_sheet(pymupdf.open(str(pdf))[0]) == SheetType.COVER_NOTES


def test_schedule_wins_over_details_on_a_shared_sheet():
    # p24: a window schedule alongside window-detail drawings must parse as a
    # schedule so its table is mined, not skipped as a detail sheet.
    titles = [
        "WINDOW SCHEDULE",
        "WINDOW ELEVATION DETAILS",
        "WINDOW DETAIL @ JAMB",
        "WINDOW DETAIL @ HEAD & SILL",
    ]
    assert classify_titles(titles) == SheetType.SCHEDULE


def test_cover_marker_beats_thumbnail_titles():
    # A cover/index page can show floor-plan thumbnails; the SHEET INDEX marker
    # keeps the detector off it.
    assert (
        classify_titles(["FIRST FLOOR PLAN"], "... SHEET INDEX A0.0 COVER PAGE ...")
        == SheetType.COVER_NOTES
    )


def test_no_titles_is_other():
    assert classify_titles([]) == SheetType.OTHER


def test_ocr_title_lines_keep_largest_tier_and_classify():
    # OCR of a no-text sheet: the big title line vs small callouts. Only the
    # largest-tier keyword line survives, and it classifies the sheet.
    from planlint.ingest.sheet_type import title_lines_from_ocr

    lines = [
        (28.0, "1ST FLOOR RCP / ELECTRICAL PLAN"),  # the title (tall)
        (9.0, "SEE PLAN A2.1"),                       # a small callout
        (9.0, "REFERENCE NOTES"),                     # small furniture
    ]
    titles = title_lines_from_ocr(lines)
    assert titles == ["1ST FLOOR RCP / ELECTRICAL PLAN"]
    assert classify_titles(titles) == SheetType.RCP_ELECTRICAL


def test_electrical_plan_title_beats_the_word_plan():
    # 'REFLECTED CEILING PLAN & ELECTRICAL PLAN' and the abbreviation 'ELEC. PLAN'
    # both carry 'PLAN', but an electrical/RCP sheet must route to skip, not to the
    # plan detector — RCP has vote priority and 'ELEC' is recognized.
    assert (
        classify_titles(
            ["1ST FLOOR - REFLECTED CEILING PLAN & ELECTRICAL PLAN", "ELEC. PLAN"]
        )
        == SheetType.RCP_ELECTRICAL
    )
    assert classify_titles(["ELEC. PLAN"]) == SheetType.RCP_ELECTRICAL


def test_ocr_title_lines_empty_when_no_keyword():
    from planlint.ingest.sheet_type import title_lines_from_ocr

    assert title_lines_from_ocr([(20.0, "AMERICAN FARMHOUSE"), (8.0, "12-25-2020")]) == []
