from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response

from app.core.store import plan_store
from app.services.export.svg_exporter import export_svg
from app.services.export.dxf_exporter import export_dxf
from planlint.api.auth import AuthenticatedUser, get_current_user

router = APIRouter()

SUPPORTED_FORMATS = {"svg", "dxf"}


@router.get("/export/formats")
async def list_formats():
    """List supported export formats."""
    return {
        "formats": [
            {"id": "svg", "name": "SVG", "description": "Scalable Vector Graphics — viewable in any browser"},
            {"id": "dxf", "name": "DXF", "description": "AutoCAD Drawing Exchange Format — open in any CAD software"},
        ]
    }


@router.get("/export/{generation_id}/{fmt}")
async def export_floor_plan(
    generation_id: str,
    fmt: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """
    Export a generated floor plan in the requested format, scoped to the authenticated owner.

    Supported formats: svg, dxf.
    IFC export happens automatically during generation (if ifcopenshell is available).
    """
    if fmt not in SUPPORTED_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format '{fmt}'. Supported: {', '.join(sorted(SUPPORTED_FORMATS))}",
        )

    plan = plan_store.get(generation_id, user_id=user.id)
    if plan is None:
        repo = getattr(request.app.state, "repo", None)
        if repo:
            row = await repo.get_blueprint(generation_id, user_id=user.id)
            if row and row.get("floor_plan"):
                try:
                    from app.schemas.floor_plan import GeneratedFloorPlan
                    plan = GeneratedFloorPlan.model_validate_json(row["floor_plan"])
                    plan_store.put(plan, user_id=user.id, project_id=row.get("project_id"))
                except Exception:
                    pass

    if plan is None:
        raise HTTPException(
            status_code=404,
            detail=f"No floor plan found for generation_id '{generation_id}'. "
                   "Generate a plan first via POST /api/generate.",
        )

    if fmt == "svg":
        svg_content = export_svg(plan)
        return Response(
            content=svg_content,
            media_type="image/svg+xml",
            headers={
                "Content-Disposition": f'attachment; filename="floorplan_{generation_id[:8]}.svg"',
            },
        )

    if fmt == "dxf":
        dxf_bytes = export_dxf(plan)
        return Response(
            content=dxf_bytes,
            media_type="application/dxf",
            headers={
                "Content-Disposition": f'attachment; filename="floorplan_{generation_id[:8]}.dxf"',
            },
        )

