"""Ground and bind cross-sheet reference markers.

The VLM classifies a marker (section / detail / elevation) and reads its target
sheet; this module is the deterministic half of that seam. `ground_reference`
accepts a target only when it is corroborated by the page text layer AND/OR the
sheet-index registry — a hallucinated or mistyped sheet id is dropped, never
linked. `nearest_asset` then binds a grounded marker to the plan asset it sits on
(proximity), so a detail bubble on a door links that door to its detail sheet.
"""

from __future__ import annotations

from collections.abc import Sequence

from planlint.ingest import vector_geometry as geometry
from planlint.ingest.sheet_type import SHEET_NUMBER
from planlint.models import BBox, PhysicalAsset, SheetReference

# A reference marker this close (PDF points) to an asset box is taken to reference
# that asset (a detail bubble drawn over a door, a section cut beside a stair).
_REF_RADIUS_PT = 40.0


def _canonical_sheet(text: str) -> str | None:
    """The canonical sheet number inside a VLM string ('3/A3.0', 'A-3.0 ')."""
    m = SHEET_NUMBER.search(text.upper().replace(" ", ""))
    return m.group(1) if m else None


def ground_reference(
    kind: str,
    detail_num: str,
    target_sheet: str,
    bbox: BBox,
    page_text_upper: str,
    registry: dict[str, str],
) -> SheetReference | None:
    """A `SheetReference` when the VLM's target is real, else None.

    The target must be corroborated: printed on this page's text layer and/or a key
    in the sheet-index registry. Both → high confidence; one → medium; neither →
    dropped (an ungrounded model guess never becomes a link).
    """
    target = _canonical_sheet(target_sheet)
    if target is None:
        return None
    in_text = target in page_text_upper.upper()
    in_registry = target in registry
    if not in_text and not in_registry:
        return None
    return SheetReference(
        kind=kind,  # type: ignore[arg-type]  # validated by the model's Literal
        detail_num=detail_num.strip(),
        target_sheet_number=target,
        bbox=bbox,
        confidence=0.9 if (in_text and in_registry) else 0.6,
    )


def nearest_asset(
    ref_bbox: BBox, assets: Sequence[PhysicalAsset]
) -> PhysicalAsset | None:
    """The plan asset a marker references — the nearest within `_REF_RADIUS_PT`, or
    None (a section cut through open plan references no single asset)."""
    best: PhysicalAsset | None = None
    best_d = _REF_RADIUS_PT + 1.0
    for asset in assets:
        d = geometry._box_distance(ref_bbox, asset.bbox)
        if d <= _REF_RADIUS_PT and d < best_d:
            best, best_d = asset, d
    return best
