"""
Outlines Pydantic models for constrained decoding.

When served via SGLang with Outlines backend, these schemas
enforce that the LLM output is always valid JSON matching our types.
For Ollama, we use JSON mode + post-validation instead.
"""

from pydantic import BaseModel, Field


class OutlinesRoom(BaseModel):
    """Room schema for constrained LLM output."""
    id: str = Field(..., description="e.g. 'bedroom_1'")
    type: str = Field(..., description="Room type enum value")
    label: str = Field(..., description="Display name")
    target_area_sqm: float = Field(..., gt=0)
    floor: int = Field(0, ge=0)
    requires_window: bool = True
    requires_exterior_wall: bool = False


class OutlinesSetbacks(BaseModel):
    front_m: float = 4.5
    rear_m: float = 7.5
    left_m: float = 1.5
    right_m: float = 1.5


class OutlinesPlot(BaseModel):
    width_m: float = Field(..., gt=5)
    depth_m: float = Field(..., gt=5)
    setbacks: OutlinesSetbacks = Field(default_factory=OutlinesSetbacks)
    max_lot_coverage: float = Field(0.6, gt=0, le=1)


class OutlinesAdjacency(BaseModel):
    room_a_id: str
    room_b_id: str
    relationship: str = "adjacent"


class OutlinesFloorPlanSpec(BaseModel):
    """
    The Pydantic model used with Outlines for constrained decoding.
    Mirrors FloorPlanSpec but simplified for LLM output.
    """
    rooms: list[OutlinesRoom]
    adjacency: list[OutlinesAdjacency] = []
    plot: OutlinesPlot
    num_floors: int = Field(1, ge=1, le=5)
    style: str | None = None
    locale: str = "CA-QC"
