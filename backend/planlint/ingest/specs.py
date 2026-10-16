"""Fixture / finish / material schedules → a code→Spec index, and detection of
those codes where they appear on the drawings.

Same grounded seam as the opening-callout join: a code is linked to an asset only
when it exists in a parsed schedule (`detect_spec_codes` matches against the index),
so a stray token on a drawing that isn't a real spec code is ignored. This is
context enrichment (what a room is finished in, what fixture sits in it), not a
verdict — no Spec ever drives the checker.
"""

from __future__ import annotations

import re

from planlint.ingest.schedule import _group_lines
from planlint.models import BBox, Spec

# A spec code: a letter (or two) + a 2-3 digit number, optionally dashed — 'X-40',
# 'F60', 'X-00'. Door/window marks (D1, W2 — one digit) are schedule.py's job.
_CODE = re.compile(r"^([A-Z]{1,2})-?(\d{2,3})$")
# Prefixes we recognize as specs without a section title; a code under an explicit
# FIXTURE/FINISH/MATERIAL section is accepted regardless of prefix.
_PREFIX_CATEGORY = {"X": "fixture", "F": "finish"}
_SECTION = re.compile(r"\b(FIXTURE|APPLIANCE|EQUIPMENT|FINISH|MATERIAL)S?\b", re.IGNORECASE)


def normalize_code(text: str) -> str | None:
    """A spec code reduced to letter(s)+digits, uppercased ('X-40'→'X40'); None when
    the token is not a code."""
    m = _CODE.match(text.strip().upper())
    return f"{m.group(1)}{m.group(2)}" if m else None


def _section_category(title_upper: str) -> str | None:
    m = _SECTION.search(title_upper)
    if not m:
        return None
    word = m.group(1).upper()
    return "fixture" if word in ("FIXTURE", "APPLIANCE", "EQUIPMENT") else word.lower()


def parse_spec_index(page) -> dict[str, Spec]:
    """Build a `code → Spec` index from a fixture/finish/material schedule drawn as
    positioned text. Category comes from the current section title, else the code's
    prefix; a row is `<code> <description>`."""
    index: dict[str, Spec] = {}
    section: str | None = None
    for line in _group_lines(page.get_text("words")):
        tokens = [w[4] for w in line]
        upper = " ".join(tokens).upper()
        if "SCHEDULE" in upper and len(tokens) <= 5:  # a section title
            section = _section_category(upper)
            continue
        code = None
        idx = 0
        for i, tok in enumerate(tokens):
            c = normalize_code(tok)
            if c:
                code, idx = c, i
                break
        if code is None:
            continue
        category = section or _PREFIX_CATEGORY.get(code[0])
        if category is None:  # an unrecognized code with no owning section — skip
            continue
        description = " ".join(tokens[idx + 1:]).strip()
        if description:
            index.setdefault(code, Spec(code=code, category=category, description=description))
    return index


def detect_spec_codes(page, spec_index: dict[str, Spec]) -> list[tuple[str, BBox]]:
    """Spec codes printed on a drawing, as (code, bbox) — only codes present in the
    index, so a token that merely looks like a code but isn't specified is ignored."""
    out: list[tuple[str, BBox]] = []
    for w in page.get_text("words"):
        code = normalize_code(w[4])
        if code and code in spec_index:
            out.append((code, (w[0], w[1], w[2], w[3])))
    return out
