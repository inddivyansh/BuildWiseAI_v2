"""Parse a drawing set's sheet index — the cover-page list of `sheet number →
title` — into a registry.

Mirrors schedule.py: a ruled table when PyMuPDF recovers one, else positioned
text. The registry lets ingestion give each sheet its number (`ingest/sheet_type.
resolve_sheet_number`) and lets reference callouts resolve their target sheet
(`ingest/references.py`). An index row is `<sheet number> <title>` with the number
at the line start, which keeps prose like 'SEE A3.0 FOR DETAILS' out of the index.
"""

from __future__ import annotations

import re

from planlint.ingest.schedule import _group_lines
from planlint.ingest.sheet_type import SHEET_NUMBER

# A cover page mixes the sheet index with a symbols legend ('X-11 FIXTURE CALLOUT',
# 'SECTION MARKER …'); these words in the "title" mark a legend row, not an index row.
_LEGEND_WORDS = re.compile(r"\b(CALLOUT|MARKER|LEGEND|SYMBOL)\b")


def _index_from_line(text: str) -> tuple[str, str] | None:
    """(`sheet_number`, `title`) from an index line whose FIRST token is the number
    — anchoring on the start rejects prose that merely mentions another sheet, and a
    legend-word title rejects the symbols legend that shares the cover page."""
    text = text.strip()
    m = SHEET_NUMBER.match(text.upper())
    if not m:
        return None
    number = m.group(1)
    title = re.sub(r"\s+", " ", text[m.end():].strip(" .-\t")).upper()
    if len(title) < 3 or _LEGEND_WORDS.search(title):
        return None
    return number, title


def parse_sheet_index(page) -> dict[str, str]:
    """Build a `{sheet_number → title}` registry from a cover/index page. Empty
    when the page carries no recognizable index."""
    registry: dict[str, str] = {}
    for table in page.find_tables().tables:
        for raw in table.extract():
            line = " ".join((c or "").strip() for c in raw if c)
            got = _index_from_line(line)
            if got:
                registry.setdefault(got[0], got[1])
    for line in _group_lines(page.get_text("words")):
        got = _index_from_line(" ".join(w[4] for w in line))
        if got:
            registry.setdefault(got[0], got[1])
    return registry
