"""Domain models. These are the only shapes LLMs are allowed to emit and the
only shapes written to the graph — a malformed model response is a retry,
never a corrupt graph."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class AssetType(str, Enum):
    """Kind of physical object detected on a drawing."""

    DOOR = "door"
    FIRE_EXIT = "fire_exit"
    RAMP = "ramp"
    CORRIDOR = "corridor"
    STAIR = "stair"
    WINDOW = "window"
    ROOM = "room"
    OTHER = "other"


class Parameter(str, Enum):
    """A measurable quantity a constraint can govern (asset measurement key)."""

    AREA = "area_m2"  # floor area; canonical unit is square metres
    CLEAR_WIDTH = "clear_width"
    OPENING_HEIGHT = "opening_height"
    THRESHOLD_HEIGHT = "threshold_height"
    MANEUVERING_CLEARANCE = "maneuvering_clearance"
    SLOPE = "slope"
    RISER_HEIGHT = "riser_height"
    TREAD_DEPTH = "tread_depth"
    LANDING_LENGTH = "landing_length"


class Operator(str, Enum):
    """How a constraint's value bounds the measurement (min/max/range/…)."""

    MIN = "min"
    MAX = "max"
    RANGE = "range"
    BOOLEAN = "boolean"
    QUALITATIVE = "qualitative"


class VerdictType(str, Enum):
    """The checker's ruling for one asset against one regulation."""

    COMPLIES_WITH = "COMPLIES_WITH"
    VIOLATES = "VIOLATES"
    NEEDS_REVIEW = "NEEDS_REVIEW"


# (x0, y0, x1, y1) in PDF points, origin top-left of the page.
BBox = tuple[float, float, float, float]


class PhysicalAsset(BaseModel):
    """A physical object found on a sheet, with its measured parameters."""

    id: str = Field(default_factory=lambda: _new_id("asset"))
    type: AssetType
    label: str = ""
    bbox: BBox
    confidence: float = 1.0
    source: Literal[
        "vector-snapped", "raster-snapped", "vlm-only", "schedule", "detail-referenced"
    ] = "vlm-only"
    measurements: dict[Parameter, float] = Field(default_factory=dict)


class SheetReference(BaseModel):
    """A cross-sheet reference marker on a drawing — a section (`1/A3.0`), detail
    (`3/A1.7`), or elevation callout linking a plan location to another sheet.

    The VLM classifies the marker and reads its target; the target is grounded
    against the page text layer and the sheet registry before it reaches the graph,
    so a hallucinated or mistyped sheet id never becomes a link.
    """

    id: str = Field(default_factory=lambda: _new_id("ref"))
    kind: Literal["section", "detail", "elevation"]
    detail_num: str = ""  # the 'N' in 'N/SHEET' (a detail/view number on the target)
    target_sheet_number: str  # e.g. "A3.0"
    bbox: BBox
    source_asset_id: str | None = None  # the nearest plan asset, when one is close
    confidence: float = 1.0


class Spec(BaseModel):
    """A fixture / finish / material specification from a schedule, keyed by its
    code (`X-40` → 'Jotul F3 stove', `F-60` → 'Hardwood Floor'). Shared: many assets
    can reference the same spec, so the link — not the node — carries the asset."""

    id: str = Field(default_factory=lambda: _new_id("spec"))
    code: str  # normalized, e.g. 'X40', 'F60'
    category: str = ""  # 'fixture' | 'finish' | 'material'
    description: str = ""


class Detail(BaseModel):
    """A specific numbered detail/view on a sheet (detail 3 on A1.7), located by the
    VLM and grounded against the callout that references it. Carries the content read
    from *inside its region* — the dimensions and notes drawn there — so a referring
    asset gets both a precise link and scoped, unambiguous measurements."""

    id: str = Field(default_factory=lambda: _new_id("detail"))
    sheet_number: str  # the sheet the detail lives on, e.g. 'A1.7'
    number: str  # the detail's own number, e.g. '3'
    title: str = ""
    bbox: BBox  # the detail's region on the target sheet (PDF points)
    kind: str = "detail"  # section | detail | elevation (from the referencing callout)
    measurements: dict[Parameter, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    source_asset_id: str | None = None  # the asset whose callout points here


class RegulationClause(BaseModel):
    """One clause of a codebook, positioned in its section hierarchy."""

    id: str = Field(default_factory=lambda: _new_id("reg"))
    clause_id: str  # e.g. "404.2.3"
    title: str = ""
    hierarchy_path: str = ""  # "Chapter 4 › 404 Doors … › 404.2 Manual Doors"
    text: str
    page: int = 0
    bbox: BBox | None = None
    parent_clause_id: str | None = None


class Constraint(BaseModel):
    """A machine-checkable rule extracted from a clause's prose."""

    id: str = Field(default_factory=lambda: _new_id("con"))
    regulation_id: str  # RegulationClause.id it was extracted from
    applies_to: AssetType
    parameter: Parameter | None = None  # None only when operator is qualitative/boolean
    operator: Operator
    value: float | None = None
    value_high: float | None = None  # upper bound when operator == range
    unit: str | None = None
    extraction_confidence: float = 1.0
    summary: str = ""  # human-readable restatement, used in verdict reasons


class CheckResult(BaseModel):
    """The checker's output: a verdict plus the numbers that justify it."""

    verdict: VerdictType
    measured: float | None = None  # inches (canonical)
    required: str | None = None  # human string, e.g. ">= 32 in"
    reason: str = ""


class RunEvent(BaseModel):
    """A single progress update streamed to SSE subscribers during a run."""

    stage: str  # "ingest:semantic" | "ingest:spatial" | "verify" | "done" | "error"
    message: str
    progress: float | None = None  # 0..1 within the stage
    level: Literal["info", "warning", "error"] = "info"
    asset_id: str | None = None


class RunSummary(BaseModel):
    """Final tally of a completed run: verdict counts and any errors."""

    run_id: str
    counts: dict[str, int] = Field(default_factory=dict)  # VerdictType value -> count
    errors: list[str] = Field(default_factory=list)
