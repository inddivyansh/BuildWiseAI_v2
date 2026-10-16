"""Sheet-type classification for multi-sheet drawing sets.

A construction set bundles many sheet types — floor plans, elevations, sections,
schedules, details, cover/notes. Only some carry plan-view geometry the spatial
detector can read, so ingestion classifies each page first and routes it to the
right handler (plan-view detector, schedule parser, or skip).

Classification is deterministic and title-driven. A drawing's title ("2ND FLOOR
PLAN", "BACK ELEVATION (NORTH)", "LONGITUDINAL SECTION", "WINDOW SCHEDULE") is
typeset larger than the reference callouts that mention other sheets, so we key
off the largest title-tier text lines rather than any keyword anywhere on the
page — sheet-type words co-occur heavily in title blocks and callouts, so a
plain page-text scan is far too noisy to classify with.
"""

from __future__ import annotations

import re
from collections import Counter
from enum import Enum


class SheetType(str, Enum):
    """The kind of drawing a sheet holds, decided from its title block."""

    FLOOR_PLAN = "floor_plan"
    ELEVATION = "elevation"
    SECTION = "section"
    SCHEDULE = "schedule"
    FOUNDATION = "foundation"
    ROOF = "roof"
    SITE = "site"
    RCP_ELECTRICAL = "rcp_electrical"
    DETAIL = "detail"
    COVER_NOTES = "cover_notes"
    OTHER = "other"


# Sheet-type words that can appear in a drawing title. Plural-aware ("DETAILS",
# "ELEVATIONS" — a plain \bDETAIL\b misses the trailing S) and broadened to
# opening lists ("DOORS & WINDOWS") and note/legend/index sheets, so titles
# beyond the core plan/elevation/section set are still recognized as titles.
_TITLE_KEYWORD = re.compile(
    r"\b(PLANS?|ELEVATIONS?|SECTIONS?|SCHEDULES?|DETAILS?|DOORS?|WINDOWS?"
    r"|NOTES?|LEGENDS?|SPECIFICATIONS?|ABBREVIATIONS?|INDEX)\b"
)

# An openings schedule titled by its contents rather than the word "schedule":
# "DOORS & WINDOWS", "DOOR & WINDOW". Plural/combined only, so a lone singular
# "DOOR" callout that surfaces as a title falls through to OTHER (→ detector),
# never gets mis-routed to the schedule parser.
_OPENINGS = re.compile(r"\bDOORS\b|\bWINDOWS\b|\bDOOR\s*(?:&|AND|/)\s*WINDOW")

# Function words that pepper note/spec prose but essentially never appear in a
# drawing title. On a notes sheet the body text is set at the title font, so a
# keyword alone isn't enough — a title must also read like a noun phrase, not a
# sentence. ("AND" is excluded: "DOOR AND WINDOW SCHEDULE" is a real title.)
_PROSE = re.compile(r"\b(OR|ARE|IS|BE|SHALL|TO|OF|THE|AS|IN|ON|THAT|SHOULD|NOT|WILL|MAY)\b")


def _looks_like_title(text: str) -> bool:
    """A short noun phrase, not a note sentence: no trailing period, no leading
    list number, no prose function words, not too long."""
    text = text.strip()
    if not text or len(text) > 70:
        return False
    if text.endswith("."):
        return False
    if re.match(r"^\d+[.)]", text):  # a numbered note item ('6.', '30)')
        return False
    return not _PROSE.search(text.upper())

# Unambiguous markers of a non-drawing sheet (cover, index, project info). Kept
# tight on purpose: a real floor plan can carry a "GENERAL NOTES" corner, so
# that phrase is deliberately excluded to avoid skipping a plan.
_COVER_MARKERS = (
    "SHEET INDEX",
    "DRAWING INDEX",
    "PROJECT INFORMATION",
    "PROJECT DESCRIPTION",
    "COVER PAGE",
)

# Types that win over a plain-majority vote when present in a page's titles: a
# page carrying any real schedule should be parsed as one even when detail
# drawings share the sheet; and a page titled as an RCP/electrical plan is an
# electrical sheet even though "…ELECTRICAL PLAN" also carries the word PLAN.
_PRIORITY = (SheetType.SCHEDULE, SheetType.RCP_ELECTRICAL)


def title_lines(page) -> list[str]:
    """The drawing-title lines on a page: keyword-bearing lines typeset at the
    largest font among such lines. Relative (per-page) rather than an absolute
    size threshold, so it adapts to each firm's title font."""
    candidates: list[tuple[float, str]] = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            spans = line["spans"]
            text = "".join(s["text"] for s in spans).strip()
            if _looks_like_title(text) and _TITLE_KEYWORD.search(text.upper()):
                candidates.append((max(s["size"] for s in spans), text))
    if not candidates:
        return []
    top = max(size for size, _ in candidates)
    return [text for size, text in candidates if size >= top - 0.5]


def title_lines_from_ocr(lines: list[tuple[float, str]]) -> list[str]:
    """Drawing-title lines from OCR output — (text_height, text) pairs. The OCR
    analogue of `title_lines` for a page with no text layer (a flattened or scanned
    PDF): keyword-bearing title-like lines set at the largest tier, keyed on OCR box
    height instead of font size (relative, so it adapts to the page's scan scale)."""
    candidates = [
        (h, t.strip())
        for h, t in lines
        if _looks_like_title(t) and _TITLE_KEYWORD.search(t.upper())
    ]
    if not candidates:
        return []
    top = max(h for h, _ in candidates)
    return [t for h, t in candidates if h >= 0.7 * top]


# A sheet number: a discipline letter (or two) then a dotted or dashed number —
# 'A0.0', 'A1.2', 'A5.0', 'S-101'. Used both to read a sheet's OWN number from its
# title block and to recognize the target of a reference callout ('1/A3.0').
SHEET_NUMBER = re.compile(r"\b([A-Z]{1,2}\d{1,2}\.\d{1,2}|[A-Z]{1,2}-\d{2,3})\b")


def _norm_title(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().upper())


def sheet_number_from_titleblock(page) -> str | None:
    """The sheet's own number, read from the title block (bottom-right quadrant,
    where the number is typeset prominently). The largest-font match wins so a
    small `N/…` callout elsewhere can't be mistaken for the sheet's own number."""
    w, h = page.rect.width, page.rect.height
    best: tuple[float, str] | None = None
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                x0, y0, _, _ = span["bbox"]
                if x0 < w * 0.6 or y0 < h * 0.6:  # title block sits bottom-right
                    continue
                m = SHEET_NUMBER.search(span["text"].upper())
                if m and (best is None or span["size"] > best[0]):
                    best = (span["size"], m.group(1))
    return best[1] if best else None


def resolve_sheet_number(page, titles: list[str], registry: dict[str, str]) -> str | None:
    """A sheet's number: its printed title-block number, else matched from the
    sheet-index registry ({number → title}) by this page's drawing title."""
    printed = sheet_number_from_titleblock(page)
    if printed:
        return printed
    page_titles = {_norm_title(t) for t in titles}
    for number, title in registry.items():
        if _norm_title(title) in page_titles:
            return number
    return None


def _classify_title(title: str) -> SheetType:
    t = title.upper()
    if "SCHEDULE" in t:
        return SheetType.SCHEDULE
    if "DETAIL" in t:
        return SheetType.DETAIL
    if "ELEVATION" in t:
        return SheetType.ELEVATION
    if "SECTION" in t:
        return SheetType.SECTION
    if "REFLECTED CEILING" in t or "ELECTRICAL" in t or "RCP" in t or re.search(r"\bELEC\b", t):
        return SheetType.RCP_ELECTRICAL
    if "ROOF" in t:
        return SheetType.ROOF
    if "FOUNDATION" in t:
        return SheetType.FOUNDATION
    if "SITE" in t or "LANDSCAPE" in t:
        return SheetType.SITE
    if "PLAN" in t:
        return SheetType.FLOOR_PLAN
    if _OPENINGS.search(t):  # 'DOORS & WINDOWS' — an opening schedule sheet
        return SheetType.SCHEDULE
    if any(w in t for w in ("NOTE", "LEGEND", "SPECIFICATION", "ABBREVIATION", "INDEX")):
        return SheetType.COVER_NOTES
    return SheetType.OTHER


def classify_titles(titles: list[str], full_text_upper: str = "") -> SheetType:
    """Pure classification from a page's title lines (and full text for the
    cover/notes markers). Split out from `classify_sheet` so it can be tested
    against real title strings without a PDF."""
    if any(marker in full_text_upper for marker in _COVER_MARKERS):
        return SheetType.COVER_NOTES
    if not titles:
        return SheetType.OTHER
    votes = [_classify_title(t) for t in titles]
    for winner in _PRIORITY:
        if winner in votes:
            return winner
    return Counter(votes).most_common(1)[0][0]


def classify_sheet(page) -> SheetType:
    """Classify one PyMuPDF page by its drawing title(s)."""
    return classify_titles(title_lines(page), page.get_text().upper())
