"""POST /api/ingest — Upload an existing floor plan for parsing."""

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request

from app.api._persist import persist_blueprint
from app.config import settings
from app.core.pipeline import GenerationPipeline
from app.core.store import plan_store
from app.schemas.generation import IngestResponse
from planlint.api.auth import AuthenticatedUser, get_current_user

router = APIRouter()

_pipeline: GenerationPipeline | None = None


def _get_pipeline() -> GenerationPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = GenerationPipeline()
    return _pipeline


@router.post("/ingest", response_model=IngestResponse)
async def ingest_floor_plan(
    request: Request,
    file: UploadFile = File(...),
    file_type: str = Form("auto"),
    project_id: str | None = Form(None),
    scale_m_per_pixel: float | None = Form(None),
    plot_width_m: float | None = Form(None),
    plot_depth_m: float | None = Form(None),
    locale: str = Form("CA-QC"),
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Upload and parse an existing floor plan (PDF, image, DXF, IFC).

    Supported formats:
    - **pdf**: PDF floor plans → rendered and parsed for rooms/geometry
    - **image**: PNG, JPG, TIFF, BMP, WebP → CubiCasa5k (GPU) or OpenCV (CPU fallback)
    - **dxf**: AutoCAD DXF → ezdxf parser
    - **ifc**: Industry Foundation Classes → ifcopenshell parser

    Set file_type to "auto" to detect from file extension.

    If `project_id` is supplied (as a form field or query parameter), the
    resulting floor plan is durably persisted under that project in Neo4j.
    """
    # Accept project_id from form or query param
    target_project_id = project_id or request.query_params.get("project_id")

    # Validate file size
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(content) / 1e6:.1f} MB). Max: {settings.max_upload_size_mb} MB",
        )

    # Save to temp file
    suffix = Path(file.filename).suffix if file.filename else ".tmp"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(content)
        tmp.flush()
        tmp.close()
        tmp_path = Path(tmp.name)

        pipeline = _get_pipeline()

        # Auto-detect file type from extension
        actual_file_type = file_type
        if file_type == "auto":
            actual_file_type = pipeline.detect_file_type(tmp_path)

        result = await pipeline.ingest_from_file(
            file_path=tmp_path,
            file_type=actual_file_type,
            scale_m_per_pixel=scale_m_per_pixel,
            locale=locale,
            plot_width_m=plot_width_m,
            plot_depth_m=plot_depth_m,
        )

        floor_plan = result.get("floor_plan")
        if floor_plan is None:
            return IngestResponse(
                generation_id=result["generation_id"],
                status="failed",
                floor_plan=None,
                confidence=result.get("confidence", 0.0),
                warnings=result.get("warnings", []),
                error="No floor plan could be extracted from the uploaded file",
                project_id=None,
            )

        # Durable persistence to Neo4j if project_id is provided
        persisted_project_id: str | None = None
        if target_project_id:
            await persist_blueprint(
                request,
                floor_plan,
                target_project_id,
                compliance=result.get("compliance"),
                user_id=user.id,
            )
            persisted_project_id = target_project_id

        # Cache in plan_store with tenant ownership
        plan_store.put(floor_plan, user_id=user.id, project_id=persisted_project_id)

        return IngestResponse(
            generation_id=result["generation_id"],
            status="completed",
            floor_plan=floor_plan,
            confidence=result.get("confidence", 0.5),
            warnings=result.get("warnings", []),
            project_id=persisted_project_id,
        )

    finally:
        # Clean up temp file
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except Exception:
            pass
