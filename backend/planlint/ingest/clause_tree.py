"""Pure clause-tree builders: parsed PDF content → RegulationClause hierarchy.

Two entry points share the dotted-prefix hierarchy logic:

- ``build_clause_tree(text_blocks)``: regex-driven, for plain text extraction
  (the simple PyMuPDF path). Clause ids like '404.2.3' encode their own
  ancestry.
- ``build_clause_tree_from_structure(items)``: structure-driven, for
  layout-aware parsers (Docling) that already know which lines are section
  headers and which blobs are tables. No regex guessing about what starts a
  section — the layout model decided that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from planlint.models import BBox, RegulationClause

_CLAUSE_START = re.compile(r"^(?P<id>\d+(?:\.\d+)*)\s+(?P<rest>\S.*)$", re.DOTALL)
# Title runs up to the first period that ends a word (not a numbering dot).
_TITLE = re.compile(r"^(?P<title>[^.]{1,80}?)\.(?:\s|$)")
# Dot leaders mark a table-of-contents entry, not a section header.
_TOC_LEADER = re.compile(r"\.{4,}")
# A block that is nothing but a dotted section number ("1.2") — Word-layout
# PDFs often emit the number and the title as separate blocks.
_ID_ONLY = re.compile(r"^\d+(?:\.\d+)+$")
# A line that could begin a section header: a dotted/short number alone, or a
# number followed by an uppercase title. Used to split blocks whose text glues
# a heading onto the tail of the previous paragraph.
_HEADER_LINE = re.compile(r"^\d+(?:\.\d+)*(?:\s*$|\s+[A-Z])")
# CFR-style section marks: "§ 35.151 …" / "§§ 36.407-36.499 …". Stripped
# before number matching so the section number becomes the clause id.
_SECTION_MARK = re.compile(r"^§+\s*")
# A citation-style running title ("28 CFR part 35.151 …") — the leading
# number is the CFR title, never a section id.
_CFR_CITATION = re.compile(r"^CFR\b", re.IGNORECASE)
# Per-page running heads citing the enclosing division ("Section 35.151 of
# 28 CFR Part 35", "Subpart D of 28 CFR Part 36"). Matching one as a header
# would merge into the real clause at every page break, splitting its content
# and resetting paragraph scope — they must vanish entirely.
_RUNNING_CITATION = re.compile(
    r"^(?:section|subpart|chapter|part|appendix)\s+\S{1,12}\s+of\s+\d+\s+CFR\b",
    re.IGNORECASE,
)


def _clause_start_match(text: str) -> re.Match | None:
    """Match a segment that starts a numbered clause, with the guards both
    builders rely on. None when the segment is body text:

    - a dotless "id" longer than 3 digits is a year or a page number, not a
      section (real sections look like "404", "404.2.3");
    - section titles start with an uppercase letter ("404.2.3 Clear Width");
      a number followed by anything else is a measurement ("1.2 metres",
      "30 inches", "1 space for every 5 Units");
    - dot leaders on the title line mean a table-of-contents entry, which
      would otherwise claim the id long before the real body section appears.
    """
    text = _SECTION_MARK.sub("", text)
    match = _CLAUSE_START.match(text)
    if match is None:
        return None
    if "." not in match.group("id") and len(match.group("id")) > 3:
        return None
    first = match.group("rest")[0]
    if not (first.isalpha() and first.isupper()):
        return None
    if _TOC_LEADER.search(match.group("rest").splitlines()[0]):
        return None
    return match


def _split_header_segments(text: str) -> list[str]:
    """Split a block's text at interior header-looking lines. PyMuPDF often
    merges a section heading into the tail of the preceding paragraph's
    block; without this split such sections could never start a clause."""
    segments: list[list[str]] = [[]]
    for line in text.splitlines():
        if _HEADER_LINE.match(line.strip()) and any(s.strip() for s in segments[-1]):
            segments.append([])
        segments[-1].append(line)
    return ["\n".join(seg).strip() for seg in segments if any(s.strip() for s in seg)]


@dataclass(frozen=True)
class TextBlock:
    text: str
    page: int
    bbox: BBox | None = None


def _parent_id(clause_id: str, existing: dict[str, RegulationClause]) -> str | None:
    """Longest existing dotted prefix, e.g. 404.2.3 -> 404.2 (or 404 if 404.2 missing)."""
    parts = clause_id.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:cut])
        if candidate in existing:
            return candidate
    return None


def _breadcrumb(parent: str | None, by_id: dict[str, RegulationClause]) -> str:
    parts: list[str] = []
    ancestor = parent
    while ancestor is not None:
        node = by_id[ancestor]
        parts.insert(0, node.text.splitlines()[0][:80])
        ancestor = node.parent_clause_id
    return " › ".join(parts)


def build_clause_tree(text_blocks: list[TextBlock]) -> list[RegulationClause]:
    """Group text blocks into clauses keyed by dotted numbering.

    Blocks that don't start a clause (exceptions, advisories, continuation
    paragraphs) are appended to the most recent clause's text. Leading
    material before the first clause is dropped.
    """
    by_id: dict[str, RegulationClause] = {}
    order: list[str] = []
    current: RegulationClause | None = None
    pending_id: str | None = None

    for block in text_blocks:
        for text in _split_header_segments(block.text):
            if _ID_ONLY.fullmatch(text):
                pending_id = text  # stitch onto the next segment's text
                continue
            if pending_id is not None:
                text = f"{pending_id} {text}"
                pending_id = None
            match = _clause_start_match(text)
            if match:
                clause_id = match.group("id")
                if clause_id in by_id:
                    # Re-encountered id (amendment or repeated numbering):
                    # merge into the existing clause rather than overwriting
                    # it — the emitted clause count must equal the nodes
                    # actually written, and the first occurrence must survive.
                    merged = by_id[clause_id].model_copy(
                        update={"text": by_id[clause_id].text + "\n" + text}
                    )
                    by_id[clause_id] = merged
                    current = merged
                    continue
                rest = match.group("rest").strip()
                title_match = _TITLE.match(rest)
                title = title_match.group("title").strip() if title_match else ""
                parent = _parent_id(clause_id, by_id)
                clause = RegulationClause(
                    clause_id=clause_id,
                    title=title,
                    hierarchy_path=_breadcrumb(parent, by_id),
                    text=text,
                    page=block.page,
                    bbox=block.bbox,
                    parent_clause_id=parent,
                )
                by_id[clause_id] = clause
                order.append(clause_id)
                current = clause
            elif current is not None:
                current = current.model_copy(update={"text": current.text + "\n" + text})
                by_id[current.clause_id] = current

    return [by_id[cid] for cid in order]


# --------------------------------------------------------------------------
# Structure-driven building (layout-aware parsers)

@dataclass(frozen=True)
class StructuredItem:
    """Neutral shape a layout-aware parser emits: the parser already decided
    what is a section header, body text, or a table."""

    kind: Literal["header", "text", "table"]
    text: str
    page: int
    bbox: BBox | None = None
    # Outline depth of a header (1 = '#', 2 = '##', …) when the parser exposes
    # it (VLM markdown). Used only as a fallback when the clause number gives no
    # parent; None means "hierarchy from the number alone".
    depth: int | None = None


# "SECTION 10 – ZONING" / "Chapter 4: Accessible Routes" style headers.
_SECTION_WORD = re.compile(
    r"^(?:SECTION|CHAPTER)\s+(?P<id>\d+(?:\.\d+)*)\b[\s:–—-]*(?P<title>.*)$", re.IGNORECASE
)
# Short zone/appendix codes that headline a section on their own line ("AG",
# "O", "PMD2") — joined with the next header, which carries the title.
_CODE_ONLY = re.compile(r"^[A-Z]{1,4}\d{0,2}$")
_TOC_TITLE = re.compile(
    r"^(?:(?:table\s+of\s+)?contents|index(?:\s+and\s+list\s+of\s+figures)?)$",
    re.IGNORECASE,
)
# "(1) PERMITTED USES" — numbered subsection of the enclosing section. These
# repeat across zones, so they are scoped under their parent's id.
_PAREN_NUMBER = re.compile(r"^\((?P<n>\d{1,3})\)\s*(?P<title>.*)$", re.DOTALL)
# "(a) Design and construction." — CFR letter paragraph, scoped under its
# section so 35.151(a) becomes clause 35.151.a.
_PAREN_LETTER = re.compile(r"^\((?P<letter>[a-z])\)\s*(?P<title>.*)$", re.DOTALL)
# Zone code embedded in a title: "RURAL RESIDENTIAL (RR) ZONE" -> RR.
_CODE_IN_TITLE = re.compile(r"\(([A-Z]{1,5}\d{0,2})\)")


def _numbered_identity(text: str) -> tuple[str, str] | None:
    """(clause_id, title) when the text is a *numbered* section heading —
    '404.2.3 Clear Width', '§ 35.151 …', 'SECTION 10 – ZONING'. None
    otherwise. The strict subset of _header_identity that is allowed to end a
    TOC/index run."""
    text = _SECTION_MARK.sub("", text)
    numbered = _CLAUSE_START.match(text)
    if numbered and _CFR_CITATION.match(numbered.group("rest").lstrip()):
        return None  # "28 CFR part 35.151 …" — running title, not section 28
    if numbered and (
        "." in numbered.group("id") or len(numbered.group("id")) <= 3
    ):
        return numbered.group("id"), numbered.group("rest").strip().splitlines()[0][:120]
    section = _SECTION_WORD.match(text)
    if section:
        title = section.group("title").strip()
        if re.match(r"of\b", title, re.IGNORECASE):
            return None  # "Section 5 of the Act" — a citation, not a heading
        return section.group("id"), title[:120]
    return None


def _header_identity(text: str) -> tuple[str, str] | None:
    """Derive (clause_id, title) from a header's text; None when the header
    doesn't identify a section (e.g. a document title)."""
    identity = _numbered_identity(text)
    if identity is not None:
        return identity
    # Unnumbered section ("OPEN SPACE (O) ZONE"): codebooks set these in caps.
    # Mixed-case unnumbered headers are document titles/subtitles, not sections.
    first_line = text.strip().splitlines()[0]
    if first_line.isupper():
        code = _CODE_IN_TITLE.search(first_line)
        clause_id = code.group(1) if code else first_line[:48]
        return clause_id, first_line[:120]
    return None


def _is_toc_table(text: str) -> bool:
    """A table whose rows are mostly dot-leader entries is a rendered table
    of contents, not a content table."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return False
    return sum(1 for line in lines if _TOC_LEADER.search(line)) * 2 >= len(lines)


def build_clause_tree_from_structure(items: list[StructuredItem]) -> list[RegulationClause]:
    """Build clauses from a layout-aware parse: every header starts a clause,
    and numbered run-in sections inside body text ("306.3.1 General. …") start
    clauses too — layout models emit those as plain text, but the smallest
    numbered item must be the clause. Remaining text and tables attach to the
    current clause. Content before the first header (or under a
    table-of-contents header) is dropped. Returns [] when the parse contains
    no headers at all — callers should fall back to the regex builder."""
    by_id: dict[str, RegulationClause] = {}
    order: list[str] = []
    current: RegulationClause | None = None
    pending_code: StructuredItem | None = None
    in_toc = False
    # The id that "(n) …" subsection headers scope under: the most recent
    # clause that was not itself a paren-numbered subsection.
    paren_scope: str | None = None
    # The most recent "(a) …" letter paragraph — digit parens nest under it
    # when active, so 35.151(a)(2) becomes clause 35.151.a.2. last_letter
    # enforces the a, b, c… sequence: a paren letter that doesn't continue it
    # is a roman numeral ("(v)", "(x)") or a stray, not a paragraph.
    letter_scope: str | None = None
    last_letter: str | None = None
    # Outline nesting from header depth ('#'/'##'/…), a fallback for headings the
    # number can't nest (unnumbered, outline, Chapter→section). (depth, clause_id).
    depth_stack: list[tuple[int, str]] = []

    def start_clause(
        clause_id: str,
        title: str,
        body: str,
        page: int,
        bbox: BBox | None,
        depth: int | None = None,
    ) -> None:
        """Create the clause, or merge into it when the id was already seen
        (amendments, repeated numbering) — the first occurrence survives.

        `depth` is a fallback parent signal: when the number gives no parent, the
        clause nests under the nearest shallower heading. Numbering always wins
        when it yields a parent, so the two signals never conflict."""
        nonlocal current
        if clause_id in by_id:
            merged = by_id[clause_id].model_copy(
                update={"text": by_id[clause_id].text + "\n" + body}
            )
            by_id[clause_id] = merged
            current = merged
            return
        parent = _parent_id(clause_id, by_id)
        if parent is None and depth is not None:
            parent = next((cid for d, cid in reversed(depth_stack) if d < depth), None)
        if depth is not None:
            while depth_stack and depth_stack[-1][0] >= depth:
                depth_stack.pop()
            depth_stack.append((depth, clause_id))
        clause = RegulationClause(
            clause_id=clause_id,
            title=title,
            hierarchy_path=_breadcrumb(parent, by_id),
            text=body,
            page=page,
            bbox=bbox,
            parent_clause_id=parent,
        )
        by_id[clause_id] = clause
        order.append(clause_id)
        current = clause

    for item in items:
        text = item.text.strip()
        if not text:
            continue
        is_header = item.kind == "header"
        # A header that is itself a TOC entry (dot leaders) is not a section.
        if is_header and _TOC_LEADER.search(text.splitlines()[0]):
            is_header = False
        if is_header and _RUNNING_CITATION.match(text):
            continue  # per-page running head ("Section 35.151 of 28 CFR Part 35")
        if is_header:
            if _TOC_TITLE.match(text):
                in_toc = True
                current = None
                pending_code = None
                continue
            if in_toc:
                # Only a real numbered section heading ends a TOC/index run —
                # entry headings, letter dividers, and zone-code fragments in
                # the listing all stay dropped.
                if _numbered_identity(text) is None:
                    continue
                in_toc = False
            if _CODE_ONLY.fullmatch(text) and pending_code is None:
                pending_code = item  # zone code; title arrives with the next header
                continue
            if pending_code is not None:
                clause_id = pending_code.text.strip()
                title = text.splitlines()[0][:120]
                page, bbox = pending_code.page, pending_code.bbox
                body = f"{clause_id}\n{text}"
                pending_code = None
                paren_scope = clause_id
                letter_scope = None
                last_letter = None
            else:
                paren = _PAREN_NUMBER.match(text)
                letter = _PAREN_LETTER.match(text)
                if letter is not None:
                    expected = "a" if last_letter is None else chr(ord(last_letter) + 1)
                    if paren_scope is None or letter.group("letter") != expected:
                        is_header = False  # roman numeral, out-of-sequence, or stray
                    else:
                        clause_id = f"{paren_scope}.{letter.group('letter')}"
                        title = letter.group("title").strip().splitlines()[0][:120]
                        page, bbox = item.page, item.bbox
                        body = text
                        letter_scope = clause_id
                        last_letter = letter.group("letter")
                elif paren is not None:
                    scope = letter_scope or paren_scope
                    if scope is None:
                        is_header = False  # stray "(n)" before any section
                    else:
                        clause_id = f"{scope}.{paren.group('n')}"
                        title = paren.group("title").strip().splitlines()[0][:120]
                        page, bbox = item.page, item.bbox
                        body = text
                else:
                    identity = _header_identity(text)
                    if identity is None:
                        is_header = False  # document title etc. — body text
                    else:
                        clause_id, title = identity
                        page, bbox = item.page, item.bbox
                        body = text
                        paren_scope = clause_id
                        letter_scope = None
                        last_letter = None
        if is_header:
            start_clause(clause_id, title, body, page, bbox, item.depth)
        else:
            if _TOC_TITLE.match(text):
                # TOC titles sometimes arrive as body text, not headers.
                in_toc = True
                continue
            if in_toc or current is None:
                continue  # preamble / TOC content is dropped
            if pending_code is not None:
                # A lone code followed by body text, not a title header.
                current = current.model_copy(
                    update={"text": current.text + "\n" + pending_code.text}
                )
                by_id[current.clause_id] = current
                pending_code = None
            if item.kind == "table":
                # Tables attach whole (never header-split); dot-leader tables
                # are tables of contents, not content — dropped.
                if not _is_toc_table(text):
                    current = current.model_copy(
                        update={"text": current.text + "\n" + text}
                    )
                    by_id[current.clause_id] = current
                continue
            # Layout models emit run-in section headings ("306.3.1 General.
            # Space under…") as body text; split them out so each numbered
            # section becomes its own clause instead of vanishing into the
            # enclosing one.
            for segment in _split_header_segments(text):
                if _RUNNING_CITATION.match(segment):
                    continue  # running head that arrived as body text
                match = _clause_start_match(segment)
                # Text-derived splits additionally require a dotted id:
                # dotless section headings ("303 Urinals") come through as
                # real layout headers, while body lines that merely start
                # with a number ("485 Topsail Road", "1 Bull") do not — a
                # dotless number in body text is data, not a section.
                if match and "." not in match.group("id"):
                    match = None
                if match:
                    rest = match.group("rest").strip()
                    title_match = _TITLE.match(rest)
                    start_clause(
                        clause_id=match.group("id"),
                        title=title_match.group("title").strip() if title_match else "",
                        body=segment,
                        page=item.page,
                        bbox=item.bbox,
                    )
                    paren_scope = match.group("id")
                    letter_scope = None
                    last_letter = None
                else:
                    current = current.model_copy(
                        update={"text": current.text + "\n" + segment}
                    )
                    by_id[current.clause_id] = current

    return [by_id[cid] for cid in order]
