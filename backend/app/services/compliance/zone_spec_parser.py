"""Parse zone spec.json Markdown tables into structured ZoneSpec data."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class ZoneSpec:
    """Structured zoning specification for a single zone."""

    zone_id: str
    source_file: str = ""
    authorized_uses: list[str] = field(default_factory=list)
    min_lot_area_sqm: float | None = None
    max_height_m: float | None = None
    min_stories: int | None = None
    max_stories: int | None = None
    front_margin_m: float | None = None
    side_margin_m: float | None = None
    rear_margin_m: float | None = None
    min_green_area_pct: float | None = None
    max_density_units_per_ha: float | None = None
    min_density_units_per_ha: float | None = None
    parking_type: str | None = None
    min_underground_parking_pct: float | None = None
    referenced_articles: list[str] = field(default_factory=list)
    special_provisions: list[str] = field(default_factory=list)
    raw_tables: list[str] = field(default_factory=list)


# Regex patterns for extracting numeric values from table cells
_NUMBER_RE = re.compile(r"(\d+(?:[.,]\d+)?)")
_ARTICLE_RE = re.compile(r"article\s+(\d+(?:\.\d+)*)", re.IGNORECASE)

# Use class identifiers (H1, H2, C1, C2, C20, P5, R1, etc.)
_USE_CLASS_RE = re.compile(
    r"\b(H[1-9]\d?|C[1-9]\d?|C20|P[1-9]\d?|R[1-9]\d?|I[a-z]?\d?|"
    r"HABITATION|COMMERCE|PUBLIQUE|RÉCRÉATION|INDUSTRIE)\b",
    re.IGNORECASE,
)


def parse_zone_spec(spec_json: dict) -> ZoneSpec:
    """Parse a spec.json dict (zone_id + tables list) into a structured ZoneSpec.

    The tables field contains Markdown-formatted table strings extracted from
    the zone PDF by pdfplumber. Each table is a single string with pipe-delimited
    columns and newline-delimited rows.
    """
    zone_id = spec_json.get("zone_id", "unknown")
    source_file = spec_json.get("source_file", "")
    tables = spec_json.get("tables", [])

    zs = ZoneSpec(zone_id=zone_id, source_file=source_file)
    zs.raw_tables = tables

    # Flatten all table text for extraction
    full_text = "\n".join(tables) if tables else ""

    zs.authorized_uses = _extract_uses(full_text)
    zs.max_height_m = _extract_height(full_text)
    zs.front_margin_m = _extract_margin(full_text, "avant")
    zs.side_margin_m = _extract_margin(full_text, "latérale")
    zs.rear_margin_m = _extract_margin(full_text, "arrière")
    zs.min_lot_area_sqm = _extract_lot_area(full_text)
    zs.min_green_area_pct = _extract_green_area(full_text)
    zs.max_density_units_per_ha = _extract_density(full_text, "maximal")
    zs.min_density_units_per_ha = _extract_density(full_text, "minimal")
    zs.min_stories, zs.max_stories = _extract_stories(full_text)
    zs.parking_type = _extract_parking_type(full_text)
    zs.min_underground_parking_pct = _extract_underground_parking(full_text)
    zs.referenced_articles = _extract_articles(full_text)
    zs.special_provisions = _extract_special_provisions(full_text)

    return zs


def _extract_uses(text: str) -> list[str]:
    """Extract authorized use classes (H1, C2, P5, etc.)."""
    uses = set()
    for m in _USE_CLASS_RE.finditer(text):
        val = m.group(1).upper()
        # Filter out generic headers
        if val not in {"HABITATION", "COMMERCE", "PUBLIQUE", "RÉCRÉATION", "INDUSTRIE"}:
            uses.add(val)
    return sorted(uses)


def _extract_height(text: str) -> float | None:
    """Extract maximum height in meters.

    The real spec table has "Hauteur" in one row and "maximale | 22 m" in the
    next row. We search near the "Hauteur" keyword for a standalone number
    followed by "m" that's in a table cell (i.e., between | delimiters).
    """
    # Strategy 1: Find "Hauteur" then look for "NN m" in nearby table cells.
    # Skip matches inside "DISPOSITIONS PARTICULIÈRES" text (those are article refs).
    hauteur_idx = text.find("Hauteur")
    if hauteur_idx < 0:
        hauteur_idx = text.lower().find("hauteur")

    if hauteur_idx >= 0:
        # Only search in the table area, not in DISPOSITIONS text
        disp_idx = text.find("DISPOSITIONS", hauteur_idx)
        search_end = disp_idx if disp_idx > hauteur_idx else hauteur_idx + 500
        chunk = text[hauteur_idx:search_end]

        # Look for "| NN m |" or "| NN m " pattern (table cell values)
        for m in re.finditer(r"\|\s*(\d+(?:[.,]\d+)?)\s*m\s*(?:\||$)", chunk):
            val = _parse_number(m.group(1))
            if val and 3 <= val <= 200:
                return val

    # Strategy 2: Explicit "hauteur maximale NN m" pattern
    m = re.search(
        r"(?:hauteur|height)\s+maximale\s+(\d+(?:[.,]\d+)?)\s*m",
        text, re.IGNORECASE,
    )
    if m:
        return _parse_number(m.group(1))

    return None


def _extract_margin(text: str, margin_type: str) -> float | None:
    """Extract a margin (marge) value in meters.

    In the real spec tables, the label "Marge avant" is in a header row and
    the value "6 m" is in the next row below, both as pipe-delimited cells.
    We look for the label, then find the nearest "| NN m" value cell below it.
    """
    # Normalize accented variants
    margin_lower = margin_type.lower()
    search_variants = [margin_lower]
    if margin_lower == "arrière":
        search_variants.append("arriere")
    if margin_lower == "latérale":
        search_variants.append("laterale")

    for variant in search_variants:
        idx = text.lower().find(f"marge {variant}")
        if idx < 0:
            continue

        # The value is typically in the next row. In the real spec tables, the
        # header and value rows can be 1000+ chars apart (wide pipe-delimited
        # rows). Search forward in a large window.
        chunk = text[idx:idx + 2000]
        # Stop at next major section to avoid picking up unrelated numbers
        for stop_word in ("NORMES", "DISPOSITIONS", "STATIONNEMENT", "ENSEIGNE",
                          "Hauteur", "HAUTEUR", "DENSIT"):
            stop_idx = chunk.find(stop_word, 10)
            if stop_idx > 0:
                chunk = chunk[:stop_idx]

        # Look for "| NN m |" or "| NN m" table cell pattern.
        # For "arrière", the header row contains BOTH "Marge avant" and
        # "Marge arrière" columns. When searching from "Marge arrière",
        # the first "NN m" match might be the front margin value.
        # Strategy: count the pipe-column index of "Marge arrière" in the
        # header row, then find the value at a similar pipe position.
        matches = list(re.finditer(r"\|\s*(\d+(?:[.,]\d+)?)\s*m\s*(?:\||$)", chunk))
        if margin_lower in ("arrière", "arriere") and len(matches) >= 2:
            # In wide tables (1000+ char rows), the 1st "NN m" match is the
            # front margin value from an earlier column; the 2nd is the rear.
            # In simple format (values on separate lines, close together),
            # the 1st match IS the correct rear margin.
            gap = matches[1].start() - matches[0].start()
            if gap > 200:
                # Wide table: front and rear in same row, use 2nd match
                val = _parse_number(matches[1].group(1))
            else:
                # Simple format: first match right after label is correct
                val = _parse_number(matches[0].group(1))
            if val and 0.5 <= val <= 50:
                return val
        # For simpler formats or single match, take the first one
        for m in matches:
            val = _parse_number(m.group(1))
            if val and 0.5 <= val <= 50:
                return val

        # Fallback: "NN m" near the label (within 200 chars)
        for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*m\b", chunk[:200]):
            val = _parse_number(m.group(1))
            if val and 0.5 <= val <= 50:
                return val

    return None


def _extract_lot_area(text: str) -> float | None:
    """Extract minimum lot area in sqm.

    The lot area is in the "NORMES DE LOTISSEMENT" section, not in the
    "Superficie maximale de plancher" columns which refer to floor area per use.
    """
    # Look in the LOTISSEMENT section for lot surface minimale
    lot_idx = text.find("LOTISSEMENT")
    if lot_idx < 0:
        lot_idx = text.lower().find("dimensions g")  # "DIMENSIONS GÉNÉRALES"
    if lot_idx < 0:
        lot_idx = 0

    chunk = text[lot_idx:lot_idx + 5000]
    # Look for "NNNN m²" where NNNN >= 100 (lot sizes are typically 100+ sqm)
    for m in re.finditer(r"(\d[\d\s]*)\s*m[²\u00b2]", chunk):
        val = _parse_number(m.group(1).replace(" ", ""))
        if val and val >= 100:  # lot areas are at least 100 sqm
            return val
    return None


def _extract_green_area(text: str) -> float | None:
    """Extract minimum green area percentage."""
    # Look near "aire verte" label
    idx = text.lower().find("aire verte")
    if idx >= 0:
        chunk = text[idx:idx + 2000]
        for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*%", chunk):
            val = _parse_number(m.group(1))
            if val and 1 <= val <= 100:
                return val

    # Fallback: general pattern
    m = re.search(
        r"(?:aire\s+verte|verdure|green)[^|]*?(\d+(?:[.,]\d+)?)\s*%",
        text, re.IGNORECASE,
    )
    if m:
        return _parse_number(m.group(1))
    return None


def _extract_density(text: str, qualifier: str) -> float | None:
    """Extract density in units per hectare."""
    # Look near "logements à l'hectare" or "log/ha" labels
    for anchor in ("log/ha", "hectare", "densité"):
        idx = text.lower().find(anchor)
        if idx < 0:
            continue
        # Search in a window around the anchor for the qualifier + number
        start = max(0, idx - 300)
        chunk = text[start:idx + 200]
        # Look for "Minimal|Maximal" then "NN log/ha"
        q_idx = chunk.lower().find(qualifier.lower())
        if q_idx >= 0:
            sub = chunk[q_idx:q_idx + 200]
            m = re.search(r"(\d+(?:[.,]\d+)?)\s*log/ha", sub, re.IGNORECASE)
            if m:
                return _parse_number(m.group(1))

    # Fallback: any "NN log/ha" in the text
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s*log/ha", text, re.IGNORECASE):
        val = _parse_number(m.group(1))
        if val and val > 0:
            return val
    return None


def _extract_stories(text: str) -> tuple[int | None, int | None]:
    """Extract min/max number of stories."""
    min_stories = None
    max_stories = None

    idx = text.lower().find("étage")
    if idx < 0:
        idx = text.lower().find("stories")
    if idx >= 0:
        chunk = text[idx:idx + 300]
        m_min = re.search(r"minimal[^|]*?(\d+)", chunk, re.IGNORECASE)
        m_max = re.search(r"maximal[^|]*?(\d+)", chunk, re.IGNORECASE)
        if m_min:
            min_stories = int(m_min.group(1))
        if m_max:
            max_stories = int(m_max.group(1))

    return min_stories, max_stories


def _extract_parking_type(text: str) -> str | None:
    """Extract parking type designation."""
    pat = re.compile(r"TYPE\s+(.+?)(?:\n|\|)", re.IGNORECASE)
    for m in pat.finditer(text):
        val = m.group(1).strip()
        if val and "enseigne" not in val.lower():
            return val
    return None


def _extract_underground_parking(text: str) -> float | None:
    """Extract minimum underground parking percentage."""
    pat = re.compile(
        r"souterrain(?:e)?s?\s+(?:est\s+de\s+|:?\s*)(\d+(?:[.,]\d+)?)\s*%",
        re.IGNORECASE,
    )
    m = pat.search(text)
    if m:
        return _parse_number(m.group(1))
    return None


def _extract_articles(text: str) -> list[str]:
    """Extract all referenced article numbers."""
    articles = set()
    for m in _ARTICLE_RE.finditer(text):
        articles.add(m.group(1))
    return sorted(articles)


def _extract_special_provisions(text: str) -> list[str]:
    """Extract special provisions text blocks."""
    provisions = []
    # Look for "DISPOSITIONS PARTICULIÈRES" sections
    pat = re.compile(
        r"DISPOSITIONS\s+PARTICULI[ÈE]RES\s*(.+?)(?=\n[A-Z]{2,}|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    for m in pat.finditer(text):
        prov = m.group(1).strip()
        # Clean up pipe chars from table format
        prov = re.sub(r"\|+", " ", prov).strip()
        if prov and len(prov) > 10:
            provisions.append(prov)
    return provisions


def _parse_number(s: str) -> float | None:
    """Parse a French/English number string."""
    if not s:
        return None
    s = s.replace(",", ".").replace(" ", "").strip()
    try:
        return float(s)
    except ValueError:
        return None
