"""Schedule parsing: build a mark→size index from a door/window schedule.

A door/window schedule lists each opening's size keyed by a mark ('D1', 'A').
`parse_schedule_index` turns that into a mark→OpeningSpec map — from a ruled
table when PyMuPDF recovers one, else from positioned text for line-art
schedules — so a plan opening's callout can be joined to its printed size
(see spatial.py). A schedule row has no location on a plan, so it is never
emitted as a standalone asset; it only supplies a width to the real opening it
tags, and the pure-Python checker still owns every verdict. Materials/fixture
schedules (no size column) yield nothing rather than guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from planlint.ingest import vector_geometry as geometry
from planlint.models import AssetType

_HEADER_SIZE = re.compile(r"\b(SIZE|W\s*[x×]\s*H|WIDTH|DIMENSION)\b", re.IGNORECASE)
_DOOR = re.compile(r"\bDOOR\b", re.IGNORECASE)
_WINDOW = re.compile(r"\b(WINDOW|WDW|WIN)\b", re.IGNORECASE)
# A schedule mark like 'D1'/'D-01' (door) or 'W2' (window).
_MARK = re.compile(r"^(?P<kind>[DW])[-\s]?\d", re.IGNORECASE)


def _column(header: list[str], pattern: re.Pattern) -> int | None:
    for i, cell in enumerate(header):
        if cell and pattern.search(cell):
            return i
    return None


def _row_asset_type(cells: list[str]) -> AssetType | None:
    """Door vs window for one row, from an explicit word or a D#/W# mark."""
    joined = " ".join(c for c in cells if c)
    if _DOOR.search(joined):
        return AssetType.DOOR
    if _WINDOW.search(joined):
        return AssetType.WINDOW
    for cell in cells:
        mark = _MARK.match(cell.strip())
        if mark:
            return AssetType.DOOR if mark.group("kind").upper() == "D" else AssetType.WINDOW
    return None


# ------------------------------------------------------- positioned-text index

@dataclass(frozen=True)
class OpeningSpec:
    """One door/window row from a schedule, keyed by its mark. `width_in` is the
    schedule's first size figure — the nominal opening size, recorded as the
    clear width when joined to a plan opening (a 36" door ≈ 34" clear, still far
    truer than a measured wall gap)."""

    kind: AssetType
    mark: str
    width_in: float
    height_in: float | None = None
    type_desc: str = ""


# A 'W x H' size token: two dimension figures joined by x/×. Loose char class so
# '36"x84"', "3'-0\"x6'-8\"", and '30" X 60"' all match.
_SIZE_TOKEN = re.compile(r"[\d'\"\-]+\s*[x×X]\s*[\d'\"\-]+")
_X_SPLIT = re.compile(r"\s*[x×X]\s*")
# A schedule mark cell: a short alphanumeric tag ('A', 'D1', '2').
_MARK_CELL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9\-]{0,3}$")


def parse_size(text: str) -> tuple[float, float | None] | None:
    """Parse a 'W x H' size ('36"x84"', "3'-0\"x6'-8\"") into (width_in, height_in).
    Height is None if the second figure is unreadable; None when there is no W×H."""
    m = _SIZE_TOKEN.search(text)
    if not m:
        return None
    parts = _X_SPLIT.split(m.group(0), maxsplit=1)
    if len(parts) != 2:
        return None
    width = geometry.parse_dimension_label(parts[0])
    if width is None:
        return None
    return (width, geometry.parse_dimension_label(parts[1]))


def _normalize_mark(text: str) -> str | None:
    """A schedule/callout mark reduced to its bare alphanumerics ('Ⓐ'→'A',
    'D-01'→'D01'), uppercased, so a plan callout and a schedule row match."""
    out: list[str] = []
    for ch in text.strip():
        o = ord(ch)
        if 0x2460 <= o <= 0x2468:  # ①..⑨
            out.append(str(o - 0x2460 + 1))
        elif 0x24B6 <= o <= 0x24CF:  # Ⓐ..
            out.append(chr(ord("A") + o - 0x24B6))
        elif ch.isalnum():
            out.append(ch.upper())
    return "".join(out) or None


def _group_lines(words: list, tol: float = 4.0) -> list[list]:
    """Cluster PyMuPDF words into visual lines by y, each sorted left→right.
    Schedule rows are ~20pt apart, so a small tolerance keeps rows distinct."""
    lines: list[tuple[float, list]] = []
    for w in sorted(words, key=lambda w: w[1]):
        if lines and abs(w[1] - lines[-1][0]) <= tol:
            lines[-1][1].append(w)
        else:
            lines.append((w[1], [w]))
    return [sorted(ln, key=lambda w: w[0]) for _, ln in lines]


def _index_rows(rows: list[list[str]]) -> list[OpeningSpec]:
    """OpeningSpecs from one extracted table's rows (mark + size per opening).
    Pure row logic — testable without PyMuPDF table detection. A materials list
    (no size column) or a row missing a size or mark yields nothing."""
    if len(rows) < 2:
        return []
    header = [(c or "").strip() for c in rows[0]]
    size_col = _column(header, _HEADER_SIZE)
    if size_col is None:
        return []  # not an opening schedule (e.g. a materials list)
    specs: list[OpeningSpec] = []
    for raw in rows[1:]:
        cells = [(c or "").strip() for c in raw]
        kind = _row_asset_type(cells)
        if kind is None or size_col >= len(cells):
            continue
        size = parse_size(cells[size_col])
        width = size[0] if size else geometry.parse_dimension_label(cells[size_col])
        if width is None:
            continue
        # The mark is a short tag ('D1', 'A', 'W2') — never a type word.
        mark = next(
            (
                _normalize_mark(c)
                for c in cells
                if _MARK_CELL.match(c) and not _DOOR.search(c) and not _WINDOW.search(c)
            ),
            None,
        )
        if mark is None:
            continue
        specs.append(
            OpeningSpec(
                kind=kind, mark=mark, width_in=width,
                height_in=size[1] if size else None,
            )
        )
    return specs


def _index_from_tables(page) -> dict[str, OpeningSpec]:
    """Mark→OpeningSpec from ruled schedule tables PyMuPDF can recover.
    Complements the positioned-text scan below for line-art schedules."""
    index: dict[str, OpeningSpec] = {}
    for table in page.find_tables().tables:
        for spec in _index_rows(table.extract()):
            index.setdefault(spec.mark, spec)
    return index


def parse_schedule_index(page) -> dict[str, OpeningSpec]:
    """Build a mark→OpeningSpec index from a schedule, covering both ruled tables
    and schedules drawn as positioned text (line-art PyMuPDF `find_tables` can't
    recover). Door vs window comes from a ruled row's contents or the nearest
    'DOOR SCHEDULE' / 'WINDOW SCHEDULE' title above; each data row's mark is its
    tag, size its 'W×H' figure. Table entries take precedence over text ones."""
    index: dict[str, OpeningSpec] = _index_from_tables(page)
    kind: AssetType | None = None
    for line in _group_lines(page.get_text("words")):
        tokens = [w[4] for w in line]
        upper = " ".join(tokens).upper()
        if "SCHEDULE" in upper and len(tokens) <= 3:  # a short section title
            kind = (
                AssetType.DOOR if _DOOR.search(upper)
                else AssetType.WINDOW if _WINDOW.search(upper)
                else None  # materials/fixture schedule — not openings
            )
            continue
        if kind is None:
            continue
        # The SIZE cell is a single 'W×H' word; the MARK is the column just left
        # of it. Anchoring on the size (not the leftmost token) rejects vertical
        # margin text ('FREE', 'THESE …') that shares a row's y-band.
        size_word = next((w for w in line if _SIZE_TOKEN.search(w[4])), None)
        if size_word is None:
            continue
        size = parse_size(size_word[4])
        if size is None:
            continue
        mark: str | None = None
        for w in line:  # sorted left→right; keep the last valid mark before SIZE
            if w[0] < size_word[0] and _MARK_CELL.match(w[4]):
                mark = _normalize_mark(w[4]) or mark
        if mark is None:
            continue
        index.setdefault(
            mark, OpeningSpec(kind=kind, mark=mark, width_in=size[0], height_in=size[1])
        )
    return index
