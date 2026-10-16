#!/usr/bin/env python3
"""Extract NBC Part 9 clauses from the PDF using pikepdf.

The NBC PDF has DRM text-scrambling that defeats pdfplumber, but pikepdf
can read the raw content streams which contain the actual text.

Usage:
    cd backend
    python scripts/extract_nbc_clauses.py --pdf PATH --output nbc_clauses.json
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path


def extract_text_from_page(page) -> str:
    """Extract text from a PDF page's content stream using pikepdf."""
    import pikepdf

    contents = page.get("/Contents")
    if not contents:
        return ""
    if isinstance(contents, pikepdf.Array):
        data = b"".join(s.read_bytes() for s in contents)
    else:
        data = contents.read_bytes()

    text = data.decode("latin-1")
    parts = []

    # TJ arrays: [(text)kern(text)...]TJ
    for m in re.finditer(r"\[(.*?)\]TJ", text, re.DOTALL):
        for sm in re.finditer(r"\(([^)]*)\)", m.group(1)):
            s = sm.group(1)
            # Decode common PDF escapes
            s = s.replace("\\(", "(").replace("\\)", ")")
            s = s.replace("\\351", "\xe9")  # é
            s = s.replace("\\350", "\xe8")  # è
            s = s.replace("\\340", "\xe0")  # à
            s = s.replace("\\342", "\xe2")  # â
            s = s.replace("\\347", "\xe7")  # ç
            s = s.replace("\\356", "\xee")  # î
            s = s.replace("\\364", "\xf4")  # ô
            s = s.replace("\\353", "\xeb")  # ë
            s = s.replace("\\371", "\xf9")  # ù
            s = s.replace("\\205", "\u2026")  # …
            parts.append(s)
        parts.append(" ")

    # Single Tj: (text) Tj
    for m in re.finditer(r"\(([^)]*)\)\s*Tj", text):
        s = m.group(1)
        s = s.replace("\\(", "(").replace("\\)", ")")
        parts.append(s)
        parts.append(" ")

    return "".join(parts)


# NBC clause number pattern: 9.X.Y.Z.
_CLAUSE_RE = re.compile(r"(9\.\d+\.\d+\.\d+\.?)")


def split_into_clauses(page_text: str, page_num: int) -> list[dict]:
    """Split page text into individual clauses by clause number pattern."""
    clauses = []
    positions = list(_CLAUSE_RE.finditer(page_text))

    for idx, match in enumerate(positions):
        clause_id = match.group(1).rstrip(".")
        start = match.start()
        end = positions[idx + 1].start() if idx + 1 < len(positions) else len(page_text)
        text = page_text[start:end].strip()

        # Skip if too short (probably just a reference, not a clause definition)
        if len(text) < 50:
            continue

        # Try to extract a title (text between clause number and first sentence)
        title_match = re.match(
            r"9\.\d+\.\d+\.\d+\.?\s*([A-ZÀ-Ü][^.]{3,60})",
            text,
        )
        title = title_match.group(1).strip() if title_match else ""

        clauses.append({
            "id": f"NBC:{clause_id}",
            "clause_number": clause_id,
            "title": title,
            "text": text,
            "page": page_num,
            "jurisdiction": "NBC",
            "source": "NBC 2020 Volume 2",
            "chapter": f"Part 9",
        })

    return clauses


def extract_cross_references(text: str) -> list[str]:
    """Extract all clause references from text."""
    refs = set()
    for m in re.finditer(r"(\d+\.\d+\.\d+\.\d+\.?)", text):
        refs.add(m.group(1).rstrip("."))
    return sorted(refs)


def main():
    parser = argparse.ArgumentParser(description="Extract NBC Part 9 clauses")
    parser.add_argument("--pdf", type=Path, required=True, help="Path to NBC PDF")
    parser.add_argument(
        "--output", type=Path,
        default=Path("data/building_codes/nbc/nbc_clauses.json"),
        help="Output JSON path",
    )
    parser.add_argument("--start-page", type=int, default=850, help="Start page (0-indexed)")
    parser.add_argument("--end-page", type=int, default=0, help="End page (0=auto)")
    args = parser.parse_args()

    if not args.pdf.exists():
        print(f"Error: PDF not found: {args.pdf}")
        sys.exit(1)

    import pikepdf
    print(f"Opening PDF: {args.pdf}")
    pdf = pikepdf.open(str(args.pdf))
    total_pages = len(pdf.pages)
    print(f"Total pages: {total_pages}")

    end_page = args.end_page if args.end_page > 0 else total_pages
    t0 = time.time()

    all_clauses = []
    page_count = 0

    for pg_idx in range(args.start_page, min(end_page, total_pages)):
        text = extract_text_from_page(pdf.pages[pg_idx])
        if not text or len(text) < 100:
            continue

        # Only process Part 9 pages
        if "9." not in text[:50] and "DivisionB" not in text[:50]:
            continue

        clauses = split_into_clauses(text, pg_idx + 1)
        if clauses:
            # Add cross-references
            for clause in clauses:
                refs = extract_cross_references(clause["text"])
                # Remove self-reference
                refs = [r for r in refs if r != clause["clause_number"]]
                clause["cross_references"] = refs

            all_clauses.extend(clauses)
            page_count += 1

        if page_count % 50 == 0 and page_count > 0:
            elapsed = time.time() - t0
            print(f"  Processed {page_count} pages, {len(all_clauses)} clauses ({elapsed:.1f}s)")

    pdf.close()

    # Deduplicate by clause ID (same clause might span pages)
    seen = {}
    for clause in all_clauses:
        cid = clause["id"]
        if cid in seen:
            # Merge text if different pages
            if clause["text"] not in seen[cid]["text"]:
                seen[cid]["text"] += " " + clause["text"]
        else:
            seen[cid] = clause
    unique_clauses = list(seen.values())

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(unique_clauses, f, indent=2, ensure_ascii=False)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")
    print(f"Pages processed: {page_count}")
    print(f"Clauses extracted: {len(unique_clauses)}")
    print(f"Output: {args.output}")

    # Show sample clauses
    part9_5 = [c for c in unique_clauses if c["clause_number"].startswith("9.5.")]
    part9_9 = [c for c in unique_clauses if c["clause_number"].startswith("9.9.")]
    print(f"\nPart 9.5 clauses: {len(part9_5)}")
    print(f"Part 9.9 clauses: {len(part9_9)}")
    for c in part9_5[:5]:
        print(f"  {c['id']}: {c['title'][:60]}... ({len(c['text'])} chars)")


if __name__ == "__main__":
    main()
