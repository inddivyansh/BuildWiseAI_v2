"""Shared RapidOCR engine. One model load process-wide, reused by both the
spatial pipeline (scanned floor-plan labels) and the codebook LLM parser (the
verification reference for scanned pages). RapidOCR ships with the docling
extra; when it's unavailable every call degrades to an empty result rather than
raising, so OCR is always best-effort."""

from __future__ import annotations

from typing import Any

_engine: Any = None  # lazy singleton; model load is expensive


def _run(png: bytes) -> tuple[list, list] | None:
    """(quad boxes, texts) from RapidOCR, or None when OCR is unavailable or
    the image can't be decoded."""
    global _engine
    try:
        if _engine is None:
            from rapidocr import RapidOCR  # lazy: heavy import

            _engine = RapidOCR()
    except Exception:
        return None
    try:
        import cv2
        import numpy as np

        image = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        result = _engine(image)
    except Exception:
        return None
    boxes = getattr(result, "boxes", None)
    texts = getattr(result, "txts", None)
    if boxes is None or texts is None:
        return None
    return list(boxes), list(texts)


def ocr_boxes(png: bytes) -> list[tuple[list, str]]:
    """[(quad, text)] for each OCR'd text region; empty when OCR is unavailable."""
    out = _run(png)
    if out is None:
        return []
    boxes, texts = out
    return [(quad, str(text)) for quad, text in zip(boxes, texts)]


def ocr_page_text(png: bytes) -> str:
    """All OCR'd text on the page, space-joined. `""` when OCR found nothing or
    is unavailable — the caller treats that as an unverifiable page."""
    return " ".join(text for _, text in ocr_boxes(png)).strip()
