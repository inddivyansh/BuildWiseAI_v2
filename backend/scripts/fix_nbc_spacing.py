#!/usr/bin/env python3
"""Post-process NBC clauses JSON to fix concatenated text (missing spaces).

The pikepdf extraction concatenated TJ text fragments without inserting
spaces between them. This script uses dynamic-programming word segmentation
with French word frequencies (via `wordfreq`) to re-insert spaces.

Usage:
    cd backend
    python scripts/fix_nbc_spacing.py
    # or specify paths:
    python scripts/fix_nbc_spacing.py --input PATH --output PATH
"""

import argparse
import json
import math
import re
import sys
import time
from pathlib import Path

from wordfreq import word_frequency

# ---------------------------------------------------------------------------
# PDF escape decoding
# ---------------------------------------------------------------------------

# Common octal escapes from PDF content streams
_PDF_ESCAPES = {
    "\\351": "é", "\\350": "è", "\\340": "à", "\\342": "â",
    "\\347": "ç", "\\356": "î", "\\364": "ô", "\\353": "ë",
    "\\371": "ù", "\\373": "û", "\\374": "ü",
    "\\253": "«", "\\273": "»", "\\205": "…",
    "\\260": "°", "\\311": "É", "\\300": "À", "\\307": "Ç",
    "\\310": "È", "\\302": "Â", "\\316": "Î", "\\324": "Ô",
    "\\331": "Ù",
}


def _decode_pdf_escapes(text: str) -> str:
    """Replace remaining \\NNN octal escapes with actual characters."""
    for esc, char in _PDF_ESCAPES.items():
        text = text.replace(esc, char)
    # Catch any remaining \\NNN patterns
    def _replace_octal(m):
        try:
            return chr(int(m.group(1), 8))
        except (ValueError, OverflowError):
            return m.group(0)
    text = re.sub(r"\\(\d{3})", _replace_octal, text)
    return text


# ---------------------------------------------------------------------------
# Word segmentation
# ---------------------------------------------------------------------------

# Cache word costs to avoid repeated log calls
_cost_cache: dict[str, float] = {}


def _word_cost(word: str) -> float:
    """Negative log10 frequency cost for a French word."""
    low = word.lower()
    if low in _cost_cache:
        return _cost_cache[low]
    freq = word_frequency(low, "fr")
    if freq > 0:
        cost = -math.log10(freq)
    else:
        # Unknown word — heavy penalty scaled by length
        cost = 15 + len(word) * 2
    _cost_cache[low] = cost
    return cost


def segment_french(text: str, max_word_len: int = 30) -> str:
    """Split concatenated French text into words using DP + word frequencies."""
    n = len(text)
    if n <= 1:
        return text

    # DP: best[i] = (cumulative_cost, backpointer_j) for text[:i]
    best = [(0.0, 0)]
    for i in range(1, n + 1):
        candidates = []
        for j in range(max(0, i - max_word_len), i):
            word = text[j:i]
            cost = best[j][0] + _word_cost(word)
            candidates.append((cost, j))
        best.append(min(candidates, key=lambda x: x[0]))

    # Backtrack to recover words
    words = []
    i = n
    while i > 0:
        j = best[i][1]
        words.append(text[j:i])
        i = j
    words.reverse()
    return " ".join(words)


# ---------------------------------------------------------------------------
# Token classification — decide what to segment vs. preserve
# ---------------------------------------------------------------------------

# Patterns that should NOT be segmented (clause refs, code refs, bracket tags)
_PRESERVE_PATTERNS = [
    re.compile(r"^\[.*\]$"),                          # [F20-OS2.1]
    re.compile(r"^\d+\.\d+\.\d+(\.\d+)?\.?$"),       # 9.3.1.1.
    re.compile(r"^[A-Z]{2,5}\d"),                     # CSA A23.1
    re.compile(r"^\d+[a-z]?\\"),                      # 2\ (numbered list with escape)
]

# Minimum length to attempt segmentation
_MIN_SEGMENT_LEN = 8


def _should_segment(token: str) -> bool:
    """Return True if a space-delimited token looks like concatenated words."""
    if len(token) < _MIN_SEGMENT_LEN:
        return False
    for pat in _PRESERVE_PATTERNS:
        if pat.match(token):
            return False
    # If it's all digits/punctuation, skip
    if re.match(r"^[\d.,;:!/()\\°«»]+$", token):
        return False
    # Must contain lowercase letters to be candidate French text
    if not re.search(r"[a-zéèàâçîôëùûü]", token):
        return False
    return True


# ---------------------------------------------------------------------------
# Pre/post-processing
# ---------------------------------------------------------------------------

# Separate clause number prefix from concatenated text
# e.g. "9.3.1.1.Généralités" → "9.3.1.1." + "Généralités"
_CLAUSE_PREFIX_RE = re.compile(
    r"^(\d+\.\d+\.\d+(?:\.\d+)?\.?)(.*)", re.DOTALL
)


def _split_clause_prefix(token: str) -> tuple[str, str]:
    """Split leading clause number from rest of token."""
    m = _CLAUSE_PREFIX_RE.match(token)
    if m and m.group(2):
        return m.group(1), m.group(2)
    return "", token


# Fix spaces accidentally inserted inside clause numbers: "9 .10 .4.4" → "9.10.4.4"
_BROKEN_CLAUSE_NUM_RE = re.compile(r"(\d) \.(\d)")


def _fix_broken_clause_numbers(text: str) -> str:
    """Collapse spaces within clause numbers like '9 .10 .4.4' → '9.10.4.4'."""
    # Repeatedly apply until stable (handles multi-part numbers)
    prev = None
    while prev != text:
        prev = text
        text = _BROKEN_CLAUSE_NUM_RE.sub(r"\1.\2", text)
    return text


# ---------------------------------------------------------------------------
# Full text processing
# ---------------------------------------------------------------------------

def fix_text_spacing(text: str) -> str:
    """Fix spacing in a single clause text string."""
    # First decode remaining PDF escapes
    text = _decode_pdf_escapes(text)

    tokens = text.split(" ")
    result = []
    for token in tokens:
        if not token:
            result.append(token)
            continue

        # Split leading clause number from concatenated text
        prefix, rest = _split_clause_prefix(token)

        if _should_segment(rest):
            # Handle tokens that contain brackets mid-stream like "parois[F20"
            parts = re.split(r"(\[.*?\])", rest)
            fixed_parts = []
            for part in parts:
                if part.startswith("["):
                    fixed_parts.append(part)
                elif _should_segment(part):
                    fixed_parts.append(segment_french(part))
                else:
                    fixed_parts.append(part)
            segmented = " ".join(p for p in fixed_parts if p)
            if prefix:
                result.append(f"{prefix} {segmented}")
            else:
                result.append(segmented)
        else:
            result.append(token)

    text = " ".join(result)

    # Post-process: fix broken clause numbers
    text = _fix_broken_clause_numbers(text)

    # Add space after commas/semicolons when followed directly by a letter
    text = re.sub(r",([A-Za-zÀ-ÿ])", r", \1", text)
    text = re.sub(r";([A-Za-zÀ-ÿ])", r"; \1", text)

    # Collapse multiple spaces
    text = re.sub(r"  +", " ", text)

    return text.strip()


def fix_clause(clause: dict) -> dict:
    """Fix spacing in a clause's text and title fields."""
    clause = clause.copy()
    clause["text"] = fix_text_spacing(clause["text"])
    if clause.get("title"):
        clause["title"] = fix_text_spacing(clause["title"])
    return clause


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fix NBC clause text spacing")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("data/building_codes/nbc/nbc_clauses.json"),
        help="Input clauses JSON",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/building_codes/nbc/nbc_clauses.json"),
        help="Output clauses JSON (overwrites input by default)",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        default=True,
        help="Create .bak backup before overwriting",
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: input not found: {args.input}")
        sys.exit(1)

    with open(args.input, encoding="utf-8") as f:
        clauses = json.load(f)

    print(f"Loaded {len(clauses)} clauses from {args.input}")

    # Backup
    if args.backup and args.output == args.input:
        bak = args.input.with_suffix(".json.bak")
        with open(bak, "w", encoding="utf-8") as f:
            json.dump(clauses, f, indent=2, ensure_ascii=False)
        print(f"Backup: {bak}")

    t0 = time.time()
    fixed = []
    for i, clause in enumerate(clauses):
        fixed.append(fix_clause(clause))
        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            print(f"  Processed {i + 1}/{len(clauses)} ({elapsed:.1f}s)")

    elapsed = time.time() - t0
    print(f"Done in {elapsed:.1f}s")

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(fixed, f, indent=2, ensure_ascii=False)
    print(f"Output: {args.output}")

    # Show before/after samples
    print("\n--- Before/After Samples ---")
    for i in [0, 2, 5]:
        if i < len(clauses):
            print(f"\n{clauses[i]['id']}:")
            print(f"  BEFORE: {clauses[i]['text'][:150]}...")
            print(f"  AFTER:  {fixed[i]['text'][:150]}...")


if __name__ == "__main__":
    main()
