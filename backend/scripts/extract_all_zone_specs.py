#!/usr/bin/env python3
"""Batch-extract zone specs from all zone directories.

Reads spec.json from each zone directory, parses structured data,
and writes a summary JSON for quick reference.

Usage:
    cd backend
    python scripts/extract_all_zone_specs.py [--zones-dir PATH] [--output PATH]
"""

import argparse
import json
import sys
from pathlib import Path

# Add parent to path so we can import app modules
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.compliance.zone_spec_parser import parse_zone_spec


def main():
    parser = argparse.ArgumentParser(description="Extract all zone specs")
    parser.add_argument(
        "--zones-dir",
        type=Path,
        default=Path("data/building_codes/quebec/sainte-foy"),
        help="Directory containing zone subdirectories",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/building_codes/quebec/zone_specs_summary.json"),
        help="Output summary JSON path",
    )
    args = parser.parse_args()

    if not args.zones_dir.exists():
        print(f"Error: zones directory not found: {args.zones_dir}")
        sys.exit(1)

    results = {}
    errors = []
    count = 0

    for zone_dir in sorted(args.zones_dir.iterdir()):
        if not zone_dir.is_dir():
            continue
        spec_file = zone_dir / "spec.json"
        if not spec_file.exists():
            continue

        try:
            with open(spec_file, encoding="utf-8") as f:
                data = json.load(f)
            zs = parse_zone_spec(data)
            results[zs.zone_id] = {
                "zone_id": zs.zone_id,
                "authorized_uses": zs.authorized_uses,
                "max_height_m": zs.max_height_m,
                "front_margin_m": zs.front_margin_m,
                "side_margin_m": zs.side_margin_m,
                "rear_margin_m": zs.rear_margin_m,
                "min_lot_area_sqm": zs.min_lot_area_sqm,
                "min_green_area_pct": zs.min_green_area_pct,
                "max_density_units_per_ha": zs.max_density_units_per_ha,
                "parking_type": zs.parking_type,
                "referenced_articles": zs.referenced_articles,
            }
            count += 1
        except Exception as e:
            errors.append(f"{zone_dir.name}: {e}")

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"Extracted {count} zone specs → {args.output}")
    if errors:
        print(f"Errors ({len(errors)}):")
        for e in errors[:10]:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
