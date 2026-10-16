"""API request and response schemas."""

from pydantic import BaseModel, Field

from .floor_plan import (
    ComplianceResult,
    FloorPlanSpec,
    GeneratedFloorPlan,
)


# --- Generation ---

class GenerateRequest(BaseModel):
    """Natural language floor plan generation request."""
    prompt: str = Field(
        ...,
        min_length=10,
        max_length=5000,
        description="Natural language description of the desired floor plan",
        examples=[
            "3-bedroom bungalow on a 12x20m lot in Laval with open kitchen and home office",
            "Modern 2-story house, 4 bedrooms, 2.5 baths, double garage, 15m wide lot",
        ],
    )
    # Optional: user can provide structured constraints alongside NL
    plot_width_m: float | None = None
    plot_depth_m: float | None = None
    locale: str = "CA-QC"
    zone_id: str | None = Field(None, description="Municipal zone ID, e.g. '31001Ma'")
    run_compliance: bool = True
    generator: str = "housediffusion"  # housediffusion, gsdiff, constraint_solver
    # Free-tier AI model selection (e.g. 'gemini-2.5-flash', 'qwen-2.5-32b', 'grok-beta', 'ollama:qwen2.5')
    model: str | None = Field(None, description="Requested AI model identifier")
    provider: str | None = Field(None, description="Requested AI provider (e.g. 'gemini', 'groq', 'xai', 'ollama')")
    # When provided the generated blueprint is durably persisted under this project
    # in the Neo4j graph and survives restarts.  The project must already exist.
    # If omitted the result is stored in the in-process plan_store only (session-scoped).
    project_id: str | None = Field(
        None,
        description=(
            "ID of an existing project to associate the blueprint with. "
            "Create a project first via POST /projects."
        ),
    )


class GenerateResponse(BaseModel):
    """Response for generation endpoint."""
    generation_id: str
    status: str  # "completed", "processing", "failed"
    floor_plan: GeneratedFloorPlan | None = None
    parsed_spec: FloorPlanSpec | None = None  # The LLM's interpretation
    compliance: ComplianceResult | None = None
    error: str | None = None
    # Set when the blueprint was durably persisted to a Neo4j project.
    project_id: str | None = None


# --- Editing ---

class EditAction(BaseModel):
    """A single edit operation."""
    action: str = Field(
        ...,
        description="Edit type",
        examples=["resize", "move", "add_room", "remove_room", "swap", "merge", "split"],
    )
    target_room_id: str | None = None
    parameters: dict = Field(default_factory=dict)


class EditRequest(BaseModel):
    """Edit an existing floor plan via natural language or structured commands."""
    generation_id: str = Field(..., description="ID of the floor plan to edit")
    prompt: str | None = Field(
        None,
        description="Natural language edit instruction",
        examples=[
            "Make the master bedroom bigger and add a walk-in closet",
            "Swap the kitchen and living room",
            "I need a home office, take space from the guest room",
        ],
    )
    # Alternative: structured edit commands (from drag-and-drop UI)
    actions: list[EditAction] | None = None
    model: str | None = Field(None, description="Requested AI model identifier for NL edit parsing")
    provider: str | None = Field(None, description="Requested AI provider for NL edit parsing")
    # Optional: spatial mask from canvas selection
    edit_region: dict | None = Field(
        None,
        description="Bounding box of the region to regenerate: {x, y, width, height} in meters",
    )


class EditResponse(BaseModel):
    generation_id: str
    status: str
    floor_plan: GeneratedFloorPlan | None = None
    changes_summary: str | None = None
    error: str | None = None


# --- Ingestion ---

class IngestRequest(BaseModel):
    """Upload an existing floor plan for parsing."""
    file_type: str = Field(
        ...,
        description="Type of uploaded file",
        examples=["image", "pdf", "dxf", "dwg", "ifc"],
    )
    # File itself comes via multipart form data
    auto_detect_rooms: bool = True
    locale: str = "CA-QC"


class IngestResponse(BaseModel):
    generation_id: str
    status: str
    floor_plan: GeneratedFloorPlan | None = None
    confidence: float | None = Field(None, description="Detection confidence 0-1")
    warnings: list[str] = []
    error: str | None = None
    # Set when the blueprint was durably persisted to a Neo4j project.
    project_id: str | None = None


# --- Export ---

class ExportFormat(BaseModel):
    format: str = Field(..., description="ifc, dxf, svg, pdf")


class ExportResponse(BaseModel):
    download_url: str
    format: str
    file_size_bytes: int


# --- Project CRUD ---

class ProjectCreate(BaseModel):
    name: str
    description: str | None = None


class ProjectSummary(BaseModel):
    id: str
    name: str
    description: str | None
    created_at: str
    updated_at: str
    thumbnail_url: str | None = None
    version_count: int = 1


class ProjectDetail(ProjectSummary):
    latest_floor_plan: GeneratedFloorPlan | None = None
    compliance: ComplianceResult | None = None
