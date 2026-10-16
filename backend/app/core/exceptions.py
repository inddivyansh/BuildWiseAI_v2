"""Custom exceptions for the ArchiGen pipeline."""


class ArchiGenError(Exception):
    """Base exception for all ArchiGen errors."""


# --- Pipeline errors ---

class PipelineError(ArchiGenError):
    """A pipeline stage failed."""
    def __init__(self, stage: str, message: str, generation_id: str | None = None):
        self.stage = stage
        self.generation_id = generation_id
        super().__init__(f"[{stage}] {message}")


class LLMParsingError(PipelineError):
    """LLM failed to parse natural language into FloorPlanSpec."""
    def __init__(self, message: str, **kwargs):
        super().__init__("llm_parse", message, **kwargs)


class GenerationError(PipelineError):
    """Floor plan generation model failed."""
    def __init__(self, message: str, **kwargs):
        super().__init__("generation", message, **kwargs)


class GeometryError(PipelineError):
    """Geometry cleanup or validation failed."""
    def __init__(self, message: str, **kwargs):
        super().__init__("geometry", message, **kwargs)


class ComplianceError(PipelineError):
    """Compliance checking failed (not a violation — the check itself failed)."""
    def __init__(self, message: str, **kwargs):
        super().__init__("compliance", message, **kwargs)


class ExportError(PipelineError):
    """File export (IFC/DXF/SVG/PDF) failed."""
    def __init__(self, message: str, **kwargs):
        super().__init__("export", message, **kwargs)


# --- Validation errors ---

class SpatialConstraintError(ArchiGenError):
    """Room areas exceed buildable envelope or violate zoning constraints."""
    def __init__(self, total_area: float, max_area: float):
        self.total_area = total_area
        self.max_area = max_area
        super().__init__(
            f"Total room area ({total_area:.1f} sqm) exceeds "
            f"max buildable area ({max_area:.1f} sqm)"
        )


class AdjacencyConstraintError(ArchiGenError):
    """Generated layout violates required room adjacency."""
    def __init__(self, violations: list[str]):
        self.violations = violations
        super().__init__(f"{len(violations)} adjacency constraint(s) violated")


# --- Resource errors ---

class ModelNotLoadedError(ArchiGenError):
    """ML model is not loaded or not available."""
    def __init__(self, model_name: str):
        self.model_name = model_name
        super().__init__(f"Model '{model_name}' is not loaded")


class ProjectNotFoundError(ArchiGenError):
    """Requested project or generation does not exist."""
    def __init__(self, resource_id: str):
        self.resource_id = resource_id
        super().__init__(f"Resource '{resource_id}' not found")
