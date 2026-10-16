"""Semantic ingestion: codebook PDF → RegulationClause list.

Two parser backends feed the same clause-tree builder:

- ``docling`` (recommended for real codebooks): layout-aware parsing that
  survives multi-column pages and nested tables. Heavy install; packaged as
  the ``planlint[docling]`` extra.
- ``simple``: PyMuPDF text blocks. Sufficient for clean single-column text
  PDFs (like the bundled ADA excerpt) and keeps the base install light.

``auto`` uses Docling when importable, else falls back to simple.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pymupdf

from planlint.config import settings
from planlint.ingest.clause_tree import (
    StructuredItem,
    TextBlock,
    _clause_start_match,
    build_clause_tree,
    build_clause_tree_from_structure,
)
from planlint.models import RegulationClause

_converter = None  # lazy singleton; Docling loads layout/OCR model weights on init
_isolation_lock = threading.Lock()  # one Docling worker at a time (see parse_codebook_isolated)


def _simple_blocks(pdf_path: Path) -> list[TextBlock]:
    blocks: list[TextBlock] = []
    with pymupdf.open(pdf_path) as doc:
        for page_index, page in enumerate(doc):
            for x0, y0, x1, y1, text, *_ in page.get_text("blocks"):
                if text.strip():
                    blocks.append(
                        TextBlock(text=text.strip(), page=page_index, bbox=(x0, y0, x1, y1))
                    )
    return blocks


def _get_converter():
    global _converter
    if _converter is None:
        from docling.document_converter import DocumentConverter  # lazy: heavy import

        _converter = DocumentConverter()
    return _converter


def _docling_items(
    pdf_path: Path,
    chunk_pages: int | None = None,
    page_span: tuple[int, int] | None = None,
) -> list[StructuredItem]:
    """Layout-aware parse: Docling decides reading order, what is a section
    header, what is a table, and what is page furniture (headers/footers are
    excluded from the body it iterates). We keep that structure instead of
    flattening back to text and re-guessing with regexes.

    Conversion runs in page-range chunks because peak memory scales with the
    pages in flight, not the document size: Docling's parse backend holds
    every page of one convert (parsed content, rasters, artifacts) until that
    convert completes, at a measured ~2 GB of transient commit per page on a
    real codebook. Chunk size therefore IS the memory ceiling — 16 pages
    peaked at 30.7 GB and exhausted commit on a loaded machine; 2 pages peak
    ~6.5 GB. Docling keeps page numbers absolute under page_range, so chunk
    items concatenate without offsets.

    `page_span` (1-based, inclusive) restricts parsing to a page window —
    used by parse_codebook_isolated to give each worker a bounded slice."""
    from docling.datamodel.base_models import ConversionStatus  # lazy: heavy import

    if chunk_pages is None:
        chunk_pages = settings.planlint_docling_page_chunk
    if page_span is None:
        with pymupdf.open(pdf_path) as doc:
            page_span = (1, len(doc))
    first_page, last_page = page_span

    items: list[StructuredItem] = []
    converter = _get_converter()
    for start in range(first_page, last_page + 1, chunk_pages):
        end = min(start + chunk_pages - 1, last_page)
        result = converter.convert(str(pdf_path), page_range=(start, end))
        # A partial result means pages silently contributed no clauses — for a
        # compliance linter that is data loss, so fail the parse loudly.
        if result.status != ConversionStatus.SUCCESS or result.errors:
            detail = (
                str(result.errors[0].error_message)
                if result.errors
                else f"conversion status {result.status.value}"
            )
            raise RuntimeError(
                f"codebook parse failed on pages {start}–{end} of "
                f"{pdf_path.name}: {detail}"
            )
        items.extend(_items_from_document(result.document))
        del result  # release this chunk's pages before converting the next
    return items


def _is_margin_furniture(text: str, top: float, bottom: float, page_height: float) -> bool:
    """A short standalone text item lying entirely inside the page's top or
    bottom margin band: a running head or page footer the layout model failed
    to label as furniture ("Titles II and III - 2010 Standards - 20").

    Positional, not textual — repeated but meaningful body lines
    ("EXCEPTIONS:") must never be stripped, and a clause heading typeset low
    on a page is protected by the clause-start check."""
    lines = [line for line in text.strip().splitlines() if line.strip()]
    if not lines or len(lines) > 2 or any(len(line) > 90 for line in lines):
        return False
    if _clause_start_match(text.strip()):
        return False
    in_top_band = bottom <= page_height * 0.08
    in_bottom_band = top >= page_height * 0.92
    return in_top_band or in_bottom_band


def _items_from_document(document) -> list[StructuredItem]:
    """Extract StructuredItems from one converted (chunk) document."""
    from docling_core.types.doc import DocItemLabel, TableItem  # lazy: heavy import

    def provenance(item) -> tuple[int, tuple | None, float]:
        prov = getattr(item, "prov", None)
        if not prov:
            return 0, None, 0.0
        page_no = prov[0].page_no
        b = prov[0].bbox
        page_height = document.pages[page_no].size.height
        # Docling uses bottom-left origin; flip to top-left PDF points.
        return (
            max(page_no - 1, 0),
            (b.l, page_height - b.t, b.r, page_height - b.b),
            page_height,
        )

    items: list[StructuredItem] = []
    for item, _level in document.iterate_items():
        label = getattr(item, "label", None)
        if label in (DocItemLabel.PAGE_HEADER, DocItemLabel.PAGE_FOOTER):
            continue  # page numbers ('10-95') and running titles are not content
        page, bbox, page_height = provenance(item)
        if isinstance(item, TableItem):
            try:
                table_text = item.export_to_markdown(doc=document)
            except TypeError:  # older docling-core signature
                table_text = item.export_to_markdown()
            if table_text.strip():
                items.append(StructuredItem("table", table_text.strip(), page, bbox))
            continue
        text = (getattr(item, "text", "") or "").strip()
        if not text:
            continue
        kind = "header" if label in (DocItemLabel.SECTION_HEADER, DocItemLabel.TITLE) else "text"
        if (
            kind == "text"
            and bbox is not None
            and _is_margin_furniture(text, bbox[1], bbox[3], page_height)
        ):
            continue  # running head / page footer the layout model missed
        items.append(StructuredItem(kind, text, page, bbox))
    return items


def _docling_available() -> bool:
    try:
        import docling  # noqa: F401

        return True
    except ImportError:
        return False


# Provider env vars that indicate a vision model can be called. Presence only —
# the value is never read or logged (it's an API key).
_PROVIDER_KEYS = {
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
}


def _has_vision_api_key(model: str) -> bool:
    """True when the environment carries an API key for the model's provider.

    Known providers are checked by name; unknown providers fall back to the
    convention ``<PROVIDER>_API_KEY`` (e.g. ``MISTRAL_API_KEY`` for
    ``mistral:...``).  Pydantic AI will raise at call time if the key is wrong.
    """
    provider = model.split(":", 1)[0]
    keys = _PROVIDER_KEYS.get(provider)
    if keys is not None:
        return any(os.environ.get(key) for key in keys)
    # Unknown provider: check the conventional <PROVIDER>_API_KEY env var.
    return bool(os.environ.get(f"{provider.upper()}_API_KEY"))


def resolve_parser_mode(configured: str, vision_model: str) -> str:
    """Resolve `auto` to a concrete backend. LLM-first: `auto` picks the LLM
    parser when a vision API key is configured (its per-document cost is one-time
    — the graph caches clauses), else Docling when installed, else the simple
    text parser. Explicit modes pass through; PLANLINT_OFFLINE_SAMPLE forces `simple`
    so offline/CI never makes a model call."""
    if settings.planlint_offline_sample:
        return "simple" if configured in ("auto", "llm") else configured
    if configured != "auto":
        return configured
    if _has_vision_api_key(vision_model):
        return "llm"
    return "docling" if _docling_available() else "simple"


def _clauses_from_items(items: list[StructuredItem]) -> list[RegulationClause]:
    """Structure-driven tree build, with the regex builder as fallback when
    the layout model found no section headers (unusual scan/layout)."""
    clauses = build_clause_tree_from_structure(items)
    if clauses:
        return clauses
    blocks = [TextBlock(text=i.text, page=i.page, bbox=i.bbox) for i in items]
    return build_clause_tree(blocks)


def parse_codebook(pdf_path: Path, mode: str = "auto") -> list[RegulationClause]:
    """Parse a codebook PDF into a clause tree. mode: auto | docling | simple."""
    if mode == "docling" or (mode == "auto" and _docling_available()):
        return _clauses_from_items(_docling_items(pdf_path))
    return build_clause_tree(_simple_blocks(pdf_path))


def _docling_items_worker(pdf_path_str: str, first: int, last: int) -> list[StructuredItem]:
    """Subprocess entry point: parse one page window. Module-level for spawn
    pickling; StructuredItems (frozen dataclasses) pickle back to the parent."""
    return _docling_items(Path(pdf_path_str), page_span=(first, last))


def parse_codebook_isolated(pdf_path: Path, mode: str = "auto") -> list[RegulationClause]:
    """parse_codebook, but the Docling path runs in short-lived subprocesses.

    Docling's memory (layout/OCR model weights plus onnxruntime arenas that
    never shrink — several GB of commit charge on a large codebook) would
    otherwise live in the API process for its lifetime, accumulating across
    runs until the machine's commit limit is hit and unrelated allocations
    start failing with bad_alloc. Workers return every byte to the OS when
    they exit, and an out-of-memory can only kill a worker, never the server.

    A fresh worker parses each window of pages (a few chunks' worth) because
    arena commit creeps per conversion even at a flat working set — measured
    ~100 MB/chunk on a real codebook. Restarting the worker caps the creep;
    the model reload per window is noise next to the parse itself, and
    extractions are cached in the graph anyway. Page numbers are absolute, so
    window results concatenate; the clause tree is built over the whole
    document in the parent.

    The simple parser is lightweight and stays in-process (also keeps tests
    and PLANLINT_OFFLINE_SAMPLE flows free of subprocess machinery).

    Parses are serialized: concurrent workers (a re-run racing an unfinished
    run, or two projects verifying at once) can push a loaded machine over
    its commit limit — measured doing exactly that. Callers run in worker
    threads, so blocking on the lock is fine."""
    if not (mode == "docling" or (mode == "auto" and _docling_available())):
        return parse_codebook(pdf_path, mode)
    import concurrent.futures
    import multiprocessing

    with pymupdf.open(pdf_path) as doc:
        page_count = len(doc)
    # Fresh worker every few chunks: enough converts to amortize the model
    # load, few enough that per-convert arena creep (~0.1 GB) stays bounded.
    window = max(32, settings.planlint_docling_page_chunk * 4)
    context = multiprocessing.get_context("spawn")
    items: list[StructuredItem] = []
    with _isolation_lock:
        for first in range(1, page_count + 1, window):
            last = min(first + window - 1, page_count)
            with concurrent.futures.ProcessPoolExecutor(1, mp_context=context) as pool:
                try:
                    items.extend(
                        pool.submit(_docling_items_worker, str(pdf_path), first, last).result()
                    )
                except concurrent.futures.process.BrokenProcessPool as exc:
                    raise RuntimeError(
                        f"codebook parser ran out of memory on pages {first}–{last} "
                        f"of {pdf_path.name} — close other applications or lower "
                        "PLANLINT_DOCLING_PAGE_CHUNK and re-run"
                    ) from exc
    return _clauses_from_items(items)
