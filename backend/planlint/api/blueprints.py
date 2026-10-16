"""Blueprint routes — project-scoped CRUD for AI-generated floor plans.

These routes are mounted on the unified PlanLint + ArchiGen FastAPI app
(planlint/api/main.py).  They expose durable, project-scoped blueprint
history backed by the Neo4j repository.

Persistence model
-----------------
* Neo4j is the **single source of truth** for blueprint records here.
  plan_store is NOT consulted — it is an in-process session cache for the
  edit/export routes, not for durable project history.
* To create a blueprint, callers must supply a valid `project_id` when
  calling ``POST /api/generate`` (or ``POST /api/generate/from-spec``).
  Those routes persist synchronously before returning, so by the time a
  ``GET /projects/{id}/blueprints`` is served the record is already in Neo4j.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from planlint.api.auth import AuthenticatedUser, get_current_user

router = APIRouter(prefix="/projects/{project_id}/blueprints", tags=["blueprints"])


def _repo(request: Request):
    return request.app.state.repo


# ---------------------------------------------------------------------- models


class BlueprintSummary(BaseModel):
    id: str
    generator_model: str
    total_area_sqm: float
    created_at: str


class BlueprintDetail(BlueprintSummary):
    floor_plan: dict
    compliance: dict | None = None


# --------------------------------------------------------------------- routes


@router.get("", response_model=list[BlueprintSummary])
async def list_blueprints(
    project_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return summary rows (no floor_plan payload) for all blueprints in an owned project."""
    repo = _repo(request)
    if await repo.get_project(project_id, user_id=user.id) is None:
        raise HTTPException(404, "unknown project")
    rows = await repo.list_blueprints(project_id, user_id=user.id)
    return [BlueprintSummary(**r) for r in rows]


@router.get("/{blueprint_id}", response_model=BlueprintDetail)
async def get_blueprint(
    project_id: str,
    blueprint_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return a single blueprint with its full floor_plan JSON payload.

    Reads from Neo4j — the authoritative store for durable blueprints.
    Returns 404 if the blueprint doesn't exist or belongs to a different project/owner.
    """
    repo = _repo(request)
    # Verify project exists and belongs to current user
    if await repo.get_project(project_id, user_id=user.id) is None:
        raise HTTPException(404, "unknown project")

    row = await repo.get_blueprint(blueprint_id, user_id=user.id)
    if row is None:
        raise HTTPException(404, "blueprint not found")
    if row.get("project_id") != project_id:
        # Blueprint exists but belongs to a different project
        raise HTTPException(404, "blueprint not found in project")

    try:
        floor_plan = json.loads(row["floor_plan"])
    except (json.JSONDecodeError, TypeError, KeyError):
        raise HTTPException(500, "stored blueprint payload is corrupt")

    compliance: dict | None = None
    raw_compliance = row.get("compliance")
    if raw_compliance:
        try:
            compliance = json.loads(raw_compliance)
        except (json.JSONDecodeError, TypeError):
            pass  # compliance is optional; don't fail the whole response

    return BlueprintDetail(
        id=blueprint_id,
        generator_model=row["generator_model"],
        total_area_sqm=row["total_area_sqm"],
        created_at=row.get("created_at", ""),
        floor_plan=floor_plan,
        compliance=compliance,
    )


@router.delete("/{blueprint_id}", status_code=204)
async def delete_blueprint(
    project_id: str,
    blueprint_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete a blueprint from Neo4j for the authenticated user.

    Returns 404 if the blueprint doesn't exist or belongs to a different project/owner.
    """
    repo = _repo(request)
    # Verify project exists and belongs to user
    if await repo.get_project(project_id, user_id=user.id) is None:
        raise HTTPException(404, "unknown project")

    row = await repo.get_blueprint(blueprint_id, user_id=user.id)
    if row is None:
        raise HTTPException(404, "blueprint not found")
    if row.get("project_id") != project_id:
        raise HTTPException(404, "blueprint not found in project")

    deleted = await repo.delete_blueprint(blueprint_id, project_id=project_id, user_id=user.id)
    if not deleted:
        # Concurrent deletion — treat as success (idempotent DELETE)
        pass
