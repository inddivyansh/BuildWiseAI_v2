"""LLM markdown parser: a vision model transcribes each codebook page to
markdown, and a deterministic check verifies the transcription against the
PDF's own text layer before anything reaches the clause tree.

This preserves the core invariant — the LLM extracts *structure* only. A page
whose transcription diverges from the embedded text is retried once and then
fails loudly; a wrong rendering of legal text must never silently enter the
graph. Pages with no text layer (pure scans) cannot be verified and are
reported so the caller can surface a warning.

Heading levels in the markdown drive clause identity: the output feeds the
same ``build_clause_tree_from_structure`` used by the Docling path, so all
section/§/paragraph hierarchy logic is shared.
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from pydantic_ai import Agent, BinaryContent, ModelRetry, RunContext

from planlint.ingest.clause_tree import StructuredItem, _clause_start_match
from planlint.ingest.ocr import ocr_page_text
from planlint.ingest.vlm import RENDER_ZOOM
from planlint.models import RegulationClause

_CONCURRENT_PAGES = 4
# Fraction of the reference's tokens that must reappear in the transcription.
# Furniture the model correctly omits (headers, footers, page numbers) costs a
# few tokens per page, so full agreement is never expected.
_AGREEMENT_FLOOR = 0.85
# Scanned pages verify against noisy OCR text, so the floor is looser there.
_AGREEMENT_FLOOR_OCR = 0.6
# A page with at least this much text, single-column and table-free, is parsed
# straight from its text layer — no VLM call (the text layer IS the source).
_MIN_CLEAN_CHARS = 200


@dataclass
class _Verify:
    """Deps for the transcription validator: the ground-truth text to check the
    transcription against (PDF text layer, or OCR text for scans) and the recall
    floor to apply (looser for noisy OCR)."""

    reference: str
    floor: float

_INSTRUCTIONS = """\
You transcribe pages of building codes and regulations to Markdown, verbatim.

Rules:
- Reproduce the body text exactly as printed. Never summarize, paraphrase,
  reorder, or add commentary.
- Mark headings with '#' levels: '#' for numbered or §-numbered section
  headings ('§ 35.151 New construction and alterations.', '404.2 Manual
  Doors'), '##' for lettered paragraph headings ('(a) Design and
  construction.'), '###' for numbered sub-paragraph headings ('(2) Exception
  for structural impracticability.'). Keep the printed numbering in the
  heading text. Heading depth must reflect the outline nesting: use a deeper
  level for a more deeply nested heading, so a document's structure (e.g.
  Chapter > Section > (a) > (2) > (i), or Article > Section > subsection)
  reads as progressively deeper '#' levels even when the numbering scheme is
  unusual or absent.
- Transcribe tables as Markdown tables.
- Transcribe table-of-contents and index pages faithfully, marking their
  title ('CONTENTS', 'Index') as a '#' heading.
- Omit page headers, page footers, page numbers, and running titles
  (e.g. 'Department of Justice', 'Section 35.151 of 28 CFR Part 35').
"""

_HEADING = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<text>\S.*)$")
_TOKEN = re.compile(r"[a-z0-9]+")
# A dimension: a number bound to a unit/quote/percent/ratio. These are the
# compliance-load-bearing tokens; section ids ("404.2") and page numbers ("20")
# carry no unit, so they are excluded and never trigger the numeric guard.
_DIMENSION = re.compile(
    r"""\d+(?:\.\d+)?\s*(?:  ["'’″′]      # 36" / 3'
                          | \s*:\s*\d+                   # 1:12
                          | %                            # 8.3%
                          | \s*(?:in|inch|inches|ft|feet|mm|cm|m)\b )""",
    re.IGNORECASE | re.VERBOSE,
)


def _tokens(text: str) -> Counter:
    return Counter(_TOKEN.findall(text.lower()))


def _text_agreement(markdown: str, reference: str) -> float:
    """Share of the reference's tokens present in the transcription — the coarse
    seam that catches gross omission or a wrong-page transcription."""
    ref = _tokens(reference)
    if not ref:
        return 1.0
    produced = _tokens(markdown)
    overlap = sum(min(count, produced[token]) for token, count in ref.items())
    return overlap / sum(ref.values())


def _dimension_tokens(text: str) -> set[str]:
    """Normalized set of dimension tokens (whitespace and quote glyphs unified)
    so the guard compares 36" vs 36 " vs 36″ as equal."""
    out: set[str] = set()
    for match in _DIMENSION.finditer(text):
        token = re.sub(r"\s+", "", match.group(0).lower())
        token = token.replace("″", '"').replace("′", "'").replace("’", "'")
        out.add(token)
    return out


def build_transcription_agent(model) -> Agent:
    """Page-to-markdown agent. Deps carry the ground-truth reference and floor so
    the validator can (a) reject a transcription that drops the page's prose and
    (b) reject one whose dimensions disagree with the printed text — a flipped
    32"→36" must never reach a verdict."""
    agent = Agent(
        model,
        output_type=str,
        deps_type=_Verify,
        instructions=_INSTRUCTIONS,
        model_settings={"temperature": 0.0},
        retries=2,
    )

    @agent.output_validator
    async def verify_against_reference(ctx: RunContext[_Verify], output: str) -> str:
        reference = ctx.deps.reference
        if not reference.strip():
            return output  # no ground truth (blank scan) — checked upstream
        agreement = _text_agreement(output, reference)
        if agreement < ctx.deps.floor:
            raise ModelRetry(
                f"Only {agreement:.0%} of the page's printed text appears in your "
                "transcription. Transcribe the page verbatim, omitting only page "
                "headers, footers, and page numbers."
            )
        produced = _dimension_tokens(output)
        expected = _dimension_tokens(reference)
        invented = produced - expected
        dropped = expected - produced
        if invented or dropped:
            raise ModelRetry(
                "Dimensions must match the printed page exactly. "
                + (f"Not on the page: {sorted(invented)}. " if invented else "")
                + (f"Missing from your transcription: {sorted(dropped)}. " if dropped else "")
                + "Re-read the measurements and transcribe every one verbatim."
            )
        return output

    return agent


def _items_from_markdown(markdown: str, page_index: int) -> list[StructuredItem]:
    """Markdown → StructuredItems: heading lines become headers, contiguous
    '|' blocks become tables, remaining paragraph blocks become text."""
    items: list[StructuredItem] = []
    paragraph: list[str] = []
    table: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            items.append(StructuredItem("text", "\n".join(paragraph).strip(), page_index))
            paragraph.clear()

    def flush_table() -> None:
        if table:
            items.append(StructuredItem("table", "\n".join(table).strip(), page_index))
            table.clear()

    for line in markdown.splitlines():
        stripped = line.strip()
        heading = _HEADING.match(stripped)
        if heading:
            flush_paragraph()
            flush_table()
            items.append(
                StructuredItem(
                    "header",
                    heading.group("text").strip(),
                    page_index,
                    depth=len(heading.group("hashes")),
                )
            )
        elif stripped.startswith("|"):
            flush_paragraph()
            table.append(stripped)
        elif not stripped:
            flush_paragraph()
            flush_table()
        else:
            flush_table()
            paragraph.append(stripped)
    flush_paragraph()
    flush_table()
    return items


def _has_tables(page) -> bool:
    try:
        return len(page.find_tables().tables) > 0
    except Exception:
        return False


def _is_multicolumn(page) -> bool:
    """Heuristic: a substantial share of text blocks start past the horizontal
    midpoint — the signature of a second column that naive text extraction would
    mis-order. Conservative (single-column pages have blocks hugging the left)."""
    blocks = [b for b in page.get_text("blocks") if b[4].strip()]
    if len(blocks) < 4:
        return False
    mid = page.rect.width * 0.5
    right = sum(1 for b in blocks if b[0] > mid)
    return right >= max(3, len(blocks) * 0.25)


def page_needs_vlm(page) -> bool:
    """True unless the page is clean single-column text with no tables — those
    are parsed straight from the text layer to avoid a needless VLM call. Biased
    to True: cost optimization must never cost fidelity."""
    text = page.get_text().strip()
    if len(text) < _MIN_CLEAN_CHARS:  # scan or sparse/figure page → VLM (+OCR gate)
        return True
    return _has_tables(page) or _is_multicolumn(page)


def text_layer_to_markdown(text: str) -> str:
    """Deterministic text→markdown: lines that look like a clause start become
    '#' headings, everything else is body. Used both for clean pages (which have
    no tables — a table sends the page to the VLM) and as the fallback when a VLM
    transcription fails verification. Feeds the same `_items_from_markdown`."""
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            lines.append("")
        elif _clause_start_match(line):
            lines.append(f"# {line}")
        else:
            lines.append(line)
    return "\n".join(lines)


@dataclass
class _PagePrep:
    """One page's inputs, decided synchronously: whether it needs the VLM, its
    text layer, and either a rendered PNG (VLM path) or prebuilt markdown
    (text-layer path)."""

    index: int
    needs_vlm: bool
    text_layer: str
    png: bytes | None
    markdown: str | None


def _page_count(pdf_path: str) -> int:
    with pymupdf.open(pdf_path) as doc:
        return len(doc)


def _prepare_page(pdf_path: str, page_index: int) -> _PagePrep:
    """Route and load one page. Opens the document per call — PyMuPDF is not
    thread-safe across concurrent workers on a shared handle."""
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_index]
        text_layer = page.get_text()
        if not page_needs_vlm(page):
            return _PagePrep(
                page_index, False, text_layer, None, text_layer_to_markdown(text_layer)
            )
        png = page.get_pixmap(matrix=pymupdf.Matrix(RENDER_ZOOM, RENDER_ZOOM)).tobytes("png")
        return _PagePrep(page_index, True, text_layer, png, None)


async def parse_codebook_llm(
    pdf_path: Path, model
) -> tuple[list[RegulationClause], int, list[int]]:
    """Parse a codebook via the per-page router: clean text pages are read
    straight from the text layer, the rest are VLM-transcribed and verified
    against the text layer (or OCR for scans), with dimensions checked exactly.

    Returns (clauses, unverified_count, failed_pages):
    - unverified_count: scanned pages with no text layer AND no OCR text, so
      their transcription could not be cross-checked (kept, but flagged).
    - failed_pages: pages whose VLM transcription failed verification; a
      text-bearing page is rebuilt from its text layer (correct numbers), a scan
      is dropped. Never silently trusted, never aborts the whole parse.
    """
    from planlint.ingest.semantic import _clauses_from_items

    agent = build_transcription_agent(model)
    total = await asyncio.to_thread(_page_count, str(pdf_path))
    semaphore = asyncio.Semaphore(_CONCURRENT_PAGES)

    async def transcribe(page_index: int) -> tuple[list[StructuredItem], bool, bool]:
        """(items, unverified, failed) for one page — never raises."""
        async with semaphore:
            prep = await asyncio.to_thread(_prepare_page, str(pdf_path), page_index)
            if not prep.needs_vlm:
                return _items_from_markdown(prep.markdown or "", page_index), False, False

            # Choose the verification reference: text layer, else OCR the scan.
            if prep.text_layer.strip():
                reference, floor = prep.text_layer, _AGREEMENT_FLOOR
            else:
                ocr = await asyncio.to_thread(ocr_page_text, prep.png or b"")
                reference, floor = ocr, _AGREEMENT_FLOOR_OCR
            unverified = not reference.strip()  # blank scan: nothing to check against

            try:
                result = await agent.run(
                    [
                        "Transcribe this regulation page to Markdown.",
                        BinaryContent(data=prep.png or b"", media_type="image/png"),
                    ],
                    deps=_Verify(reference=reference, floor=floor),
                )
            except Exception:
                # Verification exhausted. Prefer the text layer (correct numbers)
                # over a transcription we can't trust; a scan has none → drop it.
                if prep.text_layer.strip():
                    fallback = text_layer_to_markdown(prep.text_layer)
                    return _items_from_markdown(fallback, page_index), False, True
                return [], unverified, True
            return _items_from_markdown(result.output, page_index), unverified, False

    results = await asyncio.gather(*(transcribe(i) for i in range(total)))
    items = [item for page_items, _, _ in results for item in page_items]
    unverified = sum(1 for _, u, _ in results if u)
    failed = [i for i, (_, _, f) in enumerate(results) if f]
    return _clauses_from_items(items), unverified, failed
