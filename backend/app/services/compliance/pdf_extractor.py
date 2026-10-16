"""Extract clauses from building code PDFs (NBC, CCQ, municipal bylaws)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

# Clause numbering patterns per jurisdiction
_NBC_CLAUSE_RE = re.compile(
    r"(?:^|\n)\s*((?:Part\s+)?\d+\.\d+\.\d+\.\d+\.?)\s",
)
_NBC_SENTENCE_RE = re.compile(
    r"Sentence\s+(\d+\.\d+\.\d+\.\d+\.?\(\d+\))",
)
_CCQ_CLAUSE_RE = re.compile(
    r"(?:^|\n)\s*(?:Article\s+)?(\d+(?:\.\d+)*)\s",
)
_MUNICIPAL_CLAUSE_RE = re.compile(
    r"(?:^|\n)(\d+)\.\s",
)

# Cross-reference patterns
_XREF_PATTERNS = [
    re.compile(r"article\s+(\d+(?:\.\d+)*)", re.IGNORECASE),
    re.compile(r"(?:Sentence|clause)\s+(\d+\.\d+\.\d+\.\d+(?:\.\(\d+\))?)", re.IGNORECASE),
    re.compile(r"se\s+reporter\s+(?:à|au)\s+(\d+(?:\.\d+)*)", re.IGNORECASE),
    re.compile(r"(?:voir|see)\s+(\d+(?:\.\d+)*)", re.IGNORECASE),
    re.compile(r"conformément\s+(?:à l'article|au)\s+(\d+(?:\.\d+)*)", re.IGNORECASE),
]


class BuildingCodeExtractor:
    """Extract clauses from building code PDFs.

    Supports NBC (National Building Code), CCQ (Quebec Construction Code),
    and municipal bylaws. Chunks by article/section boundaries, not fixed-size.
    """

    def extract_clauses(
        self,
        pdf_path: Path,
        jurisdiction: str,
    ) -> list[dict[str, Any]]:
        """Extract all clauses from a PDF.

        Args:
            pdf_path: Path to the PDF file.
            jurisdiction: One of "NBC", "CCQ", "municipal".

        Returns:
            List of clause dicts with keys:
                id, title, text, page, chapter, cross_refs, jurisdiction, source_file
        """
        try:
            import pdfplumber
        except ImportError:
            logger.error("pdfplumber_not_installed")
            return []

        clauses: list[dict] = []
        source_file = pdf_path.name

        with pdfplumber.open(pdf_path) as pdf:
            pages_text: list[tuple[int, str]] = []
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                pages_text.append((i + 1, text))

                # Also extract tables
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        table_text = _format_table(table)
                        if table_text:
                            pages_text.append((i + 1, table_text))

        # Combine all text
        full_text = "\n".join(text for _, text in pages_text)

        # Build page lookup (char offset → page number)
        page_map = _build_page_map(pages_text)

        # Detect clause pattern and split
        clause_pattern = self._detect_clause_pattern(full_text, jurisdiction)

        if clause_pattern:
            raw_clauses = self._split_by_pattern(full_text, clause_pattern)
        else:
            # Fallback: split by paragraph
            raw_clauses = self._split_by_paragraph(full_text)

        current_chapter = ""
        for clause_id, clause_text in raw_clauses:
            # Detect chapter headers
            chapter = _detect_chapter(clause_text) or current_chapter
            if chapter:
                current_chapter = chapter

            # Find page for this clause
            page_num = _find_page(full_text, clause_text, page_map)

            # Extract cross-references
            cross_refs = self._extract_cross_references(clause_text)

            # Extract title (first line or sentence)
            title = _extract_title(clause_text, clause_id)

            clauses.append({
                "id": f"{jurisdiction}:{clause_id}",
                "title": title,
                "text": clause_text.strip(),
                "page": page_num,
                "chapter": current_chapter,
                "cross_refs": cross_refs,
                "jurisdiction": jurisdiction,
                "source_file": source_file,
            })

        logger.info(
            "clauses_extracted",
            jurisdiction=jurisdiction,
            count=len(clauses),
            source=source_file,
        )
        return clauses

    def _detect_clause_pattern(self, text: str, jurisdiction: str) -> re.Pattern | None:
        """Auto-detect the clause numbering pattern."""
        if jurisdiction.upper() == "NBC":
            # NBC: "9.8.1.1." format
            if len(_NBC_CLAUSE_RE.findall(text[:5000])) >= 3:
                return _NBC_CLAUSE_RE
        elif jurisdiction.upper() == "CCQ":
            # CCQ: "Article 123" or numbered sections
            if len(_CCQ_CLAUSE_RE.findall(text[:5000])) >= 3:
                return _CCQ_CLAUSE_RE
        elif jurisdiction.lower() == "municipal":
            # Municipal: "123. " format
            if len(_MUNICIPAL_CLAUSE_RE.findall(text[:5000])) >= 3:
                return _MUNICIPAL_CLAUSE_RE

        # Auto-detect: try each pattern
        for pat in [_NBC_CLAUSE_RE, _CCQ_CLAUSE_RE, _MUNICIPAL_CLAUSE_RE]:
            if len(pat.findall(text[:10000])) >= 5:
                return pat
        return None

    def _split_by_pattern(
        self, text: str, pattern: re.Pattern
    ) -> list[tuple[str, str]]:
        """Split text into clauses using the detected pattern."""
        matches = list(pattern.finditer(text))
        clauses = []
        for i, m in enumerate(matches):
            clause_id = m.group(1).strip().rstrip(".")
            start = m.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            clause_text = text[start:end]
            if len(clause_text.strip()) > 10:
                clauses.append((clause_id, clause_text))
        return clauses

    def _split_by_paragraph(self, text: str) -> list[tuple[str, str]]:
        """Fallback: split into numbered paragraphs."""
        paragraphs = text.split("\n\n")
        clauses = []
        for i, para in enumerate(paragraphs):
            para = para.strip()
            if len(para) > 20:
                clauses.append((str(i + 1), para))
        return clauses

    def _extract_cross_references(self, text: str) -> list[str]:
        """Find all article/clause references within text."""
        refs = set()
        for pat in _XREF_PATTERNS:
            for m in pat.finditer(text):
                refs.add(m.group(1))
        return sorted(refs)


def extract_definitions(clauses: list[dict], jurisdiction: str) -> list[dict]:
    """Extract legal definitions from Article 1 / glossary sections.

    Definitions typically appear as:
      - "«term» : definition text"  (French style)
      - "term means ..." (English style)
    """
    definitions = []
    for clause in clauses:
        text = clause.get("text", "")
        # French-style definitions: «term» or « term »
        for m in re.finditer(r"[««]\s*(.+?)\s*[»»]\s*:\s*(.+?)(?=[«»]|\Z)", text, re.DOTALL):
            term = m.group(1).strip().lower()
            defn = m.group(2).strip()
            if term and len(defn) > 5:
                definitions.append({
                    "term": term,
                    "text": defn[:500],  # cap length
                    "jurisdiction": jurisdiction,
                    "article_id": clause["id"],
                })
    return definitions


def _format_table(table: list[list[str | None]]) -> str:
    """Format a pdfplumber table as text."""
    if not table:
        return ""
    rows = []
    for row in table:
        cells = [str(c).strip() if c else "" for c in row]
        rows.append(" | ".join(cells))
    return "\n".join(rows)


def _build_page_map(pages_text: list[tuple[int, str]]) -> list[tuple[int, int, int]]:
    """Build a (start_offset, end_offset, page_num) map."""
    result = []
    offset = 0
    for page_num, text in pages_text:
        end = offset + len(text) + 1  # +1 for join newline
        result.append((offset, end, page_num))
        offset = end
    return result


def _find_page(
    full_text: str, clause_text: str, page_map: list[tuple[int, int, int]]
) -> int:
    """Find the page number for a clause."""
    idx = full_text.find(clause_text[:80])
    if idx < 0:
        return 0
    for start, end, page_num in page_map:
        if start <= idx < end:
            return page_num
    return 0


def _detect_chapter(text: str) -> str | None:
    """Detect chapter/part headers."""
    patterns = [
        re.compile(r"(?:CHAPITRE|CHAPTER|Part)\s+(\w+(?:\s+\w+)*)", re.IGNORECASE),
        re.compile(r"SECTION\s+(\d+(?:\.\d+)*)", re.IGNORECASE),
    ]
    for pat in patterns:
        m = pat.search(text[:200])
        if m:
            return m.group(0).strip()
    return None


def _extract_title(text: str, clause_id: str) -> str:
    """Extract a short title from the clause text."""
    # Take first line, trimmed
    first_line = text.strip().split("\n")[0][:120]
    # Remove the clause ID prefix if present
    first_line = first_line.replace(clause_id, "").strip().lstrip(".").strip()
    return first_line if first_line else clause_id
