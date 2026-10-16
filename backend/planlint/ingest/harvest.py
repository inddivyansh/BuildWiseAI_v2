"""Harvest a grounded dimension from the sheet a plan asset references.

Phase 1 links a plan asset to the detail/section that draws it. This module reads
the dimension actually printed on that target sheet — via the deterministic
dimension grid (`ingest/dimensions.py`), so a value is trusted only when it is
printed AND validated against its dimension line's length × scale. No LLM, no
estimation.

Two rules keep it honest for a compliance tool:

1. The per-parameter bounds reject only a *misread* (a 200" "riser"), NEVER a value
   that is merely non-compliant. A too-tall riser or a too-narrow door is a real
   magnitude the checker must SEE to flag as a violation — clamping it out here would
   hide the very defect we exist to catch.
2. A value is attributed to a parameter only when it is unambiguous: it fits exactly
   ONE of the asset's wanted parameters (not, say, both riser and tread, whose ranges
   overlap) AND is the sole grounded candidate. Otherwise nothing is harvested (→
   NEEDS_REVIEW), never a wrong or guessed number. Parameters that magnitude can't
   separate (riser vs tread) are therefore left to the labelled VLM elevation path.
"""

from __future__ import annotations

from planlint.models import BBox, Parameter
from planlint.ingest import dimensions
from planlint.ingest import vector_geometry as geometry

# Physical-plausibility bounds (inches): wide enough to keep every real value,
# including non-compliant ones, and reject only a gross misread. Width and height
# are deliberately non-overlapping so a door's leaf width and its opening height are
# separable; riser and tread DO overlap (nothing separates them by magnitude), so a
# stair carrying both harvests neither here — that is intentional, not an oversight.
_PLAUSIBLE_RANGE: dict[Parameter, tuple[float, float]] = {
    Parameter.RISER_HEIGHT: (2.0, 14.0),
    Parameter.TREAD_DEPTH: (5.0, 24.0),
    Parameter.CLEAR_WIDTH: (10.0, 54.0),
    Parameter.OPENING_HEIGHT: (60.0, 144.0),
    Parameter.THRESHOLD_HEIGHT: (0.0, 6.0),
    Parameter.MANEUVERING_CLEARANCE: (6.0, 96.0),
    Parameter.LANDING_LENGTH: (24.0, 240.0),
}


def _in(value: float, rng: tuple[float, float] | None) -> bool:
    return rng is not None and rng[0] <= value <= rng[1]


def _center(box: BBox) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def harvest_measurements(
    page, want: set[Parameter], region: BBox | None = None
) -> dict[Parameter, float]:
    """Grounded measurements for the wanted parameters, read off `page`'s printed
    dimensions. When `region` is given (a specific detail's box), only dimensions
    inside it are read — scoping to detail 3 removes the multi-detail ambiguity that
    a whole sheet has. A parameter is filled only from a value plausible for it, NOT
    plausible for any other wanted parameter, and the sole such candidate. Anything
    ambiguous is omitted (→ NEEDS_REVIEW), never guessed; a non-compliant value is
    kept, so the checker can flag it."""
    primitives, labels = geometry.extract_primitives(page)
    # A detail prints its own scale; prefer one inside the region, else the sheet's.
    region_labels = (
        [lbl for lbl in labels if geometry._contains(region, _center(lbl.bbox))]
        if region is not None
        else labels
    )
    scale_text = next(
        (lbl.text for lbl in region_labels if "SCALE" in lbl.text.upper()), None
    ) or next((lbl.text for lbl in labels if "SCALE" in lbl.text.upper()), None)
    scale = geometry.parse_scale(scale_text or "")
    if scale is None:  # can't ground a dimension without a scale
        return {}
    if region is not None:
        primitives = [p for p in primitives if geometry._contains(region, p.midpoint)]
        labels = region_labels
    grid = dimensions.build_dimension_grid(primitives, labels, scale)
    values = sorted({round(d.value_in, 1) for d in grid})

    out: dict[Parameter, float] = {}
    for param in want:
        rng = _PLAUSIBLE_RANGE.get(param)
        if rng is None:
            continue
        candidates = [
            v
            for v in values
            if _in(v, rng)
            and not any(p != param and _in(v, _PLAUSIBLE_RANGE.get(p)) for p in want)
        ]
        if len(candidates) == 1:  # sole, cross-parameter-unambiguous grounded value
            out[param] = candidates[0]
    return out
