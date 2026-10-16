#!/usr/bin/env python3
"""Extract zone spec grids from the RCA PDF and generate spec.json for each zone.

Reads the full RCA PDF (R.C.A.3V.Q.4.pdf), finds all "GRILLE DE SPÉCIFICATIONS"
pages, extracts structured data from each grid table, and writes spec.json files
into the rca-zones directory.

Usage:
    cd backend
    python scripts/extract_zone_grids.py --pdf PATH_TO_RCA.pdf --zones-dir PATH --output summary.json
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pdfplumber

from app.services.compliance.zone_spec_parser import parse_zone_spec

# Use class identifiers
_ZONE_ID_RE = re.compile(r"\b(\d{5}[A-Z][a-z])\b")
_USE_CODES = {"H1", "H2", "H3", "C1", "C2", "C3", "C4", "C5", "C6", "C7",
              "C8", "C9", "C10", "C11", "C12", "C13", "C14", "C15", "C16",
              "C17", "C18", "C19", "C20", "P1", "P2", "P3", "P4", "P5",
              "P6", "P7", "P8", "P9", "P10", "R1", "R2", "R3", "R4", "R5",
              "I1", "I2", "I3"}

_NUMBER_RE = re.compile(r"(\d+(?:[.,]\d+)?)")


def _cell(val):
    """Clean a cell value."""
    if val is None:
        return ""
    return str(val).strip().replace("\n", " ")


def _parse_num(s: str) -> float | None:
    """Extract a number from a string."""
    if not s:
        return None
    m = _NUMBER_RE.search(s.replace(",", ".").replace("\u00a0", "").replace(" ", ""))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _extract_meter(s: str) -> float | None:
    """Extract a value in meters from a cell like '6 m'."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*m\b", s.replace(",", "."))
    if m:
        return float(m.group(1))
    return None


def _extract_pct(s: str) -> float | None:
    """Extract a percentage from a cell like '30 %'."""
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*%", s.replace(",", "."))
    if m:
        return float(m.group(1))
    return None


def extract_zone_from_table(rows: list[list], page_text: str) -> dict | None:
    """Extract zone spec data from a pdfplumber table rows."""
    # Find zone ID from page text
    zone_ids = _ZONE_ID_RE.findall(page_text)
    if not zone_ids:
        return None
    zone_id = zone_ids[0]

    spec = {
        "zone_id": zone_id,
        "source_file": f"{zone_id}.pdf",
        "authorized_uses": [],
        "max_height_m": None,
        "min_height_m": None,
        "front_margin_m": None,
        "side_margin_m": None,
        "rear_margin_m": None,
        "min_green_area_pct": None,
        "min_stories": None,
        "max_stories": None,
        "parking_type": None,
        "min_lot_area_sqm": None,
        "max_density_units_per_ha": None,
        "min_density_units_per_ha": None,
        "referenced_articles": [],
        "special_provisions": [],
    }

    # Extract authorized uses from use rows
    for row in rows:
        text = " ".join(_cell(c) for c in row)
        for code in _USE_CODES:
            if re.search(rf"\b{code}\b", text):
                if code not in spec["authorized_uses"]:
                    spec["authorized_uses"].append(code)

    spec["authorized_uses"].sort()

    # Strategy: find key rows by content, then use column-index mapping.
    # The RCA grid has a fixed column layout:
    #   Row with "Hauteur" + "Nombre d'étages" headers (cols 5, 11)
    #   Next row: sub-headers "minimale"(5), "maximale"(8), "minimal"(11), "maximal"(14)
    #   Next row: values at those column indices
    #   Row with "Marge avant"(1), "Marge latérale"(2), "Marge arrière"(11),
    #            "POS minimal"(14), "% aire verte"(15)
    #   Next row: values at those indices

    for i, row in enumerate(rows):
        cells = [_cell(c) for c in row]
        row_text = " ".join(cells)

        # --- Height & Stories ---
        # Find the sub-header row dynamically by looking for "minimale" + "maximale"
        # Column positions vary between zones, so we find them by content.
        col_h_min = col_h_max = col_s_min = col_s_max = -1
        for j, c in enumerate(cells):
            cl = c.lower()
            if cl == "minimale":
                col_h_min = j
            elif cl == "maximale":
                col_h_max = j
            elif cl == "minimal":
                col_s_min = j
            elif cl == "maximal":
                col_s_max = j
        if col_h_min >= 0 and col_h_max >= 0:
            # Check the row above has "Hauteur" to confirm this is dimensions
            prev_text = " ".join(_cell(c) for c in rows[i - 1]) if i > 0 else ""
            if "Hauteur" in prev_text or "tage" in prev_text:
                if i + 1 < len(rows):
                    vals = [_cell(c) for c in rows[i + 1]]
                    if col_h_min < len(vals):
                        v = _extract_meter(vals[col_h_min]) or _parse_num(vals[col_h_min])
                        if v and 3 <= v <= 200:
                            spec["min_height_m"] = v
                    if col_h_max < len(vals):
                        v = _extract_meter(vals[col_h_max]) or _parse_num(vals[col_h_max])
                        if v and 3 <= v <= 200:
                            spec["max_height_m"] = v
                    if col_s_min >= 0 and col_s_min < len(vals):
                        v = _parse_num(vals[col_s_min])
                        if v and 1 <= v <= 50:
                            spec["min_stories"] = int(v)
                    if col_s_max >= 0 and col_s_max < len(vals):
                        v = _parse_num(vals[col_s_max])
                        if v and 1 <= v <= 50:
                            spec["max_stories"] = int(v)

        # --- Margins + Green Area ---
        # Find columns dynamically by searching for "Marge avant", "latérale", "arrière"
        col_front = col_side = col_rear = col_green = -1
        for j, c in enumerate(cells):
            cl = c.lower()
            if "marge avant" in cl or "marge  avant" in cl:
                col_front = j
            elif "lat" in cl and "marge" in cl:
                col_side = j
            elif "arri" in cl and "marge" in cl:
                col_rear = j
            elif "aire verte" in cl or "aire  verte" in cl:
                col_green = j
        if col_front >= 0:
            if i + 1 < len(rows):
                vals = [_cell(c) for c in rows[i + 1]]
                if col_front < len(vals):
                    v = _extract_meter(vals[col_front])
                    if v:
                        spec["front_margin_m"] = v
                if col_side >= 0 and col_side < len(vals):
                    v = _extract_meter(vals[col_side])
                    if v:
                        spec["side_margin_m"] = v
                if col_rear >= 0 and col_rear < len(vals):
                    v = _extract_meter(vals[col_rear])
                    if v:
                        spec["rear_margin_m"] = v
                if col_green >= 0 and col_green < len(vals):
                    v = _extract_pct(vals[col_green])
                    if v:
                        spec["min_green_area_pct"] = v

        # --- Density ---
        if "hectare" in row_text.lower():
            # Sub-header row (Minimal/Maximal) is i+1, values at i+2
            if i + 2 < len(rows):
                hdr_cells = [_cell(c) for c in rows[i + 1]]
                val_cells = [_cell(c) for c in rows[i + 2]]
                for j, vc in enumerate(val_cells):
                    v = _parse_num(vc)
                    if v and v > 0:
                        hdr = hdr_cells[j] if j < len(hdr_cells) else ""
                        if "minimal" in hdr.lower():
                            spec["min_density_units_per_ha"] = v
                        elif "maximal" in hdr.lower():
                            spec["max_density_units_per_ha"] = v

        # --- Lot area ---
        if "lotissement" in row_text.lower() or "superficie minimale" in row_text.lower():
            for c in cells:
                m = re.search(r"(\d[\d\s]*)\s*m[²\u00b2]", c.replace(",", "."))
                if m:
                    val = float(m.group(1).replace(" ", ""))
                    if val >= 100:
                        spec["min_lot_area_sqm"] = val

        # --- Parking type ---
        if row_text.startswith("TYPE") or (len(cells) > 0 and cells[0].startswith("TYPE")):
            for c in cells:
                if "TYPE" in c and "enseigne" not in c.lower():
                    rest = c.replace("TYPE", "").strip()
                    if rest:
                        spec["parking_type"] = rest

        # --- Referenced articles ---
        if "article" in row_text.lower():
            for m in re.finditer(r"article\s+(\d+(?:\.\d+)*)", row_text, re.IGNORECASE):
                art = m.group(1)
                if art not in spec["referenced_articles"]:
                    spec["referenced_articles"].append(art)

        # --- Special provisions ---
        if "DISPOSITIONS PARTICULI" in row_text:
            prov_text = row_text.replace("DISPOSITIONS PARTICULI\u00c8RES", "").strip()
            prov_text = prov_text.replace("DISPOSITIONS PARTICULIERES", "").strip()
            if prov_text and len(prov_text) > 10:
                spec["special_provisions"].append(prov_text)

    # If only min height set (value in "minimale" column), treat as max height.
    # In practice, PDF table alignment sometimes puts the single height value
    # under "minimale" even when it's the zone's max height.
    if spec["min_height_m"] and not spec["max_height_m"]:
        spec["max_height_m"] = spec["min_height_m"]
        spec["min_height_m"] = None

    spec["referenced_articles"].sort()
    return spec


def build_spec_json(spec_data: dict) -> dict:
    """Build a spec.json dict with tables field for the zone_spec_parser."""
    # Build pipe-delimited table text from structured data
    lines = []
    if spec_data.get("authorized_uses"):
        lines.append("| USAGES AUTORISÉS |")
        for use in spec_data["authorized_uses"]:
            lines.append(f"| {use} |")
    if spec_data.get("front_margin_m"):
        lines.append(f"| Marge avant | {spec_data['front_margin_m']} m |")
    if spec_data.get("side_margin_m"):
        lines.append(f"| Marge latérale | {spec_data['side_margin_m']} m |")
    if spec_data.get("rear_margin_m"):
        lines.append(f"| Marge arrière | {spec_data['rear_margin_m']} m |")
    if spec_data.get("max_height_m"):
        lines.append(f"| Hauteur maximale | {spec_data['max_height_m']} m |")
    if spec_data.get("min_green_area_pct"):
        lines.append(f"| Pourcentage d'aire verte minimale | {spec_data['min_green_area_pct']} % |")
    if spec_data.get("max_density_units_per_ha"):
        lines.append(f"| {spec_data['max_density_units_per_ha']} log/ha |")
    if spec_data.get("min_lot_area_sqm"):
        lines.append(f"| Superficie minimale | {spec_data['min_lot_area_sqm']} m² |")
    if spec_data.get("parking_type"):
        lines.append(f"| TYPE {spec_data['parking_type']} |")
    if spec_data.get("min_stories"):
        lines.append(f"| Nombre d'étages minimal {spec_data['min_stories']} |")
    if spec_data.get("max_stories"):
        lines.append(f"| Nombre d'étages maximal {spec_data['max_stories']} |")
    for prov in spec_data.get("special_provisions", []):
        lines.append(f"| DISPOSITIONS PARTICULIÈRES {prov} |")
    for art in spec_data.get("referenced_articles", []):
        lines.append(f"| article {art} |")

    return {
        "zone_id": spec_data["zone_id"],
        "source_file": spec_data.get("source_file", ""),
        "tables": ["\n".join(lines)] if lines else [],
    }


def main():
    parser = argparse.ArgumentParser(description="Extract zone grids from RCA PDF")
    parser.add_argument("--pdf", type=Path, required=True, help="Path to R.C.A.3V.Q.4.pdf")
    parser.add_argument("--zones-dir", type=Path, required=True, help="rca-zones directory")
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/building_codes/quebec/zone_specs_summary.json"),
        help="Output summary JSON path",
    )
    parser.add_argument("--start-page", type=int, default=0, help="Start page (0-indexed)")
    parser.add_argument("--max-pages", type=int, default=0, help="Max pages to process (0=all)")
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"Error: PDF not found: {args.pdf}")
        sys.exit(1)

    print(f"Opening PDF: {args.pdf}")
    pdf = pdfplumber.open(str(args.pdf))
    total_pages = len(pdf.pages)
    print(f"Total pages: {total_pages}")

    results = {}
    errors = []
    skipped = 0
    t0 = time.time()

    end_page = total_pages
    if args.max_pages > 0:
        end_page = min(args.start_page + args.max_pages, total_pages)

    for page_idx in range(args.start_page, end_page):
        page = pdf.pages[page_idx]
        text = page.extract_text() or ""

        # Only process zone grid pages
        if "GRILLE DE SP" not in text:
            skipped += 1
            continue

        tables = page.extract_tables()
        if not tables:
            skipped += 1
            continue

        try:
            spec_data = extract_zone_from_table(tables[0], text)
            if not spec_data:
                skipped += 1
                continue

            zone_id = spec_data["zone_id"]

            # Build and write spec.json
            spec_json = build_spec_json(spec_data)
            zone_dir = args.zones_dir / zone_id
            zone_dir.mkdir(parents=True, exist_ok=True)
            spec_path = zone_dir / "spec.json"
            with open(spec_path, "w", encoding="utf-8") as f:
                json.dump(spec_json, f, indent=2, ensure_ascii=False)

            # Also store in results summary (using structured data directly)
            results[zone_id] = {
                "zone_id": zone_id,
                "authorized_uses": spec_data["authorized_uses"],
                "max_height_m": spec_data["max_height_m"],
                "front_margin_m": spec_data["front_margin_m"],
                "side_margin_m": spec_data["side_margin_m"],
                "rear_margin_m": spec_data["rear_margin_m"],
                "min_lot_area_sqm": spec_data["min_lot_area_sqm"],
                "min_green_area_pct": spec_data["min_green_area_pct"],
                "max_density_units_per_ha": spec_data["max_density_units_per_ha"],
                "min_stories": spec_data["min_stories"],
                "max_stories": spec_data["max_stories"],
                "parking_type": spec_data["parking_type"],
                "referenced_articles": spec_data["referenced_articles"],
            }

            if len(results) % 50 == 0:
                elapsed = time.time() - t0
                print(f"  Extracted {len(results)} zones ({page_idx + 1}/{end_page} pages, {elapsed:.1f}s)")

        except Exception as e:
            errors.append(f"page {page_idx + 1}: {e}")

    pdf.close()

    # Write summary
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"Extracted: {len(results)} zones")
    print(f"Skipped: {skipped} non-grid pages")
    print(f"Errors: {len(errors)}")
    print(f"Summary: {args.output}")
    if errors:
        print(f"\nFirst 10 errors:")
        for e in errors[:10]:
            print(f"  - {e}")

    # Quick validation: check 31001Ma if it was extracted
    if "31001Ma" in results:
        r = results["31001Ma"]
        print(f"\nValidation — 31001Ma:")
        print(f"  Uses: {r['authorized_uses']}")
        print(f"  Height: {r['max_height_m']}m")
        print(f"  Front: {r['front_margin_m']}m, Side: {r['side_margin_m']}m, Rear: {r['rear_margin_m']}m")
        print(f"  Green: {r['min_green_area_pct']}%")
        print(f"  Stories: {r['min_stories']}-{r['max_stories']}")


if __name__ == "__main__":
    main()
