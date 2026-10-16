"""CRUD for user projects — mock in-memory store for Phase 2.

NOTE (unified backend): this router is kept verbatim from ArchiGen but is
NOT mounted in the unified app (`planlint/api/main.py`). The unified app
uses planlint's persistent Neo4j-backed `/projects` endpoints
(`planlint/api/routes.py`) as the single shared project source of truth,
and `planlint/api/blueprints.py` to link generated floor plans to those
projects. This file is left in place only so none of ArchiGen's original
code is lost and it keeps working if `app.main:app` is ever run standalone.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.schemas.generation import ProjectCreate, ProjectSummary, ProjectDetail

router = APIRouter()

# In-memory project store (replaced by Supabase in later phases)
_projects: dict[str, dict] = {}


@router.get("/projects", response_model=list[ProjectSummary])
async def list_projects():
    """List all projects."""
    return [
        ProjectSummary(**p) for p in _projects.values()
    ]


@router.post("/projects", response_model=ProjectSummary, status_code=201)
async def create_project(request: ProjectCreate):
    """Create a new project."""
    project_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    project = {
        "id": project_id,
        "name": request.name,
        "description": request.description,
        "created_at": now,
        "updated_at": now,
        "thumbnail_url": None,
        "version_count": 0,
    }
    _projects[project_id] = project
    return ProjectSummary(**project)


@router.get("/projects/{project_id}", response_model=ProjectDetail)
async def get_project(project_id: str):
    """Get project details."""
    from app.core.store import plan_store

    if project_id not in _projects:
        plan = plan_store.get(project_id)
        if plan:
            now = datetime.now(timezone.utc).isoformat()
            project = {
                "id": project_id,
                "name": "Generated Plan",
                "description": "Auto-created from generation",
                "created_at": now,
                "updated_at": now,
                "thumbnail_url": None,
                "version_count": 1,
            }
            _projects[project_id] = project
            return ProjectDetail(**project, latest_floor_plan=plan)
        raise HTTPException(status_code=404, detail="Project not found")

    project_data = _projects[project_id].copy()
    plan = plan_store.get(project_id)
    if plan:
        project_data["latest_floor_plan"] = plan
    return ProjectDetail(**project_data)


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: str):
    """Delete a project."""
    if project_id not in _projects:
        raise HTTPException(status_code=404, detail="Project not found")
    del _projects[project_id]
