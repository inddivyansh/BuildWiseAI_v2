"""HTTP surface. Thin: every route delegates to the repository or pipeline."""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from planlint.api.auth import AuthenticatedUser, get_current_user
from planlint.config import settings
from planlint.ingest.vector_geometry import parse_scale
from planlint.models import RunEvent
from planlint.verify.pipeline import run_full

router = APIRouter()

logger = logging.getLogger(__name__)

# project_id becomes a directory name under the data dir; reject anything
# that couldn't have been produced by our own id generator.
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def _validate_project_id(project_id: str) -> None:
    if not _PROJECT_ID_RE.fullmatch(project_id):
        raise HTTPException(404, "unknown project")


def _repo(request: Request):
    return request.app.state.repo


class CreateProject(BaseModel):
    name: str
    description: str | None = None


class UpdateProject(BaseModel):
    name: str | None = None
    description: str | None = None


class ScaleUpdate(BaseModel):
    scale_text: str  # e.g. '1/4" = 1\'-0"' or '1:50'


@router.get("/projects")
async def list_projects(request: Request, user: AuthenticatedUser = Depends(get_current_user)):
    """List all projects for the authenticated user."""
    return await _repo(request).list_projects(user_id=user.id)


@router.post("/projects", status_code=201)
async def create_project(
    body: CreateProject,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Create an empty project owned by the authenticated user and return its details."""
    project_id = f"proj-{uuid.uuid4().hex[:10]}"
    await _repo(request).create_project(project_id, body.name, body.description, user_id=user.id)
    return {"id": project_id, "name": body.name, "description": body.description}


@router.get("/projects/{project_id}")
async def get_project(
    project_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Retrieve details for a single project owned by the authenticated user."""
    _validate_project_id(project_id)
    project = await _repo(request).get_project(project_id, user_id=user.id)
    if project is None:
        raise HTTPException(404, "unknown project")
    return project


@router.patch("/projects/{project_id}")
async def update_project(
    project_id: str,
    body: UpdateProject,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Update an authenticated user's project name or description."""
    _validate_project_id(project_id)
    if body.name is None and body.description is None:
        raise HTTPException(422, "at least one field (name, description) must be provided")
    updated = await _repo(request).update_project(
        project_id, name=body.name, description=body.description, user_id=user.id
    )
    if updated is None:
        raise HTTPException(404, "unknown project")
    return updated


@router.put("/projects/{project_id}")
async def replace_project(
    project_id: str,
    body: UpdateProject,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Update a project's name or description (PUT alias for PATCH)."""
    return await update_project(project_id, body, request, user=user)


@router.post("/projects/sample", status_code=201)
async def create_sample_project(
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Bundled sample: vector floor plan + ADA Standards excerpt owned by caller."""
    samples = Path(settings.planlint_samples_dir)
    plan = samples / "sample_floorplan.pdf"
    code = samples / "ada_excerpt.pdf"
    if not plan.exists() or not code.exists():
        raise HTTPException(500, "Sample PDFs missing — run the generators in samples/")
    repo = _repo(request)
    project_id = f"proj-{uuid.uuid4().hex[:10]}"
    await repo.create_project(project_id, "Sample: ADA door clearance audit", user_id=user.id)
    for source, kind in ((plan, "floorplan"), (code, "codebook")):
        document_id = f"doc-{uuid.uuid4().hex[:10]}"
        destination = Path(settings.planlint_data_dir) / project_id / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, destination)
        await repo.create_document(
            document_id, project_id, kind, source.name, str(destination), user_id=user.id
        )
    return {"id": project_id}


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete a user-owned project: cascade the graph, evict its runs, remove its files."""
    _validate_project_id(project_id)
    paths = await _repo(request).delete_project(project_id, user_id=user.id)
    if paths is None:
        raise HTTPException(404, "unknown project")
    request.app.state.runs.drop_project(project_id)
    for path in paths:
        if path:
            Path(path).unlink(missing_ok=True)
    shutil.rmtree(Path(settings.planlint_data_dir) / project_id, ignore_errors=True)


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Delete one document (cascading its graph descendants) and its file if owned by user."""
    info = await _repo(request).delete_document(document_id, user_id=user.id)
    if info is None:
        raise HTTPException(404, "document not found")
    if info.get("path"):
        Path(info["path"]).unlink(missing_ok=True)


@router.post("/projects/{project_id}/documents", status_code=201)
async def upload_document(
    project_id: str,
    kind: str,
    file: UploadFile,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Store an uploaded floorplan/codebook PDF and register it in the user's project graph."""
    _validate_project_id(project_id)
    if kind not in ("floorplan", "codebook"):
        raise HTTPException(422, "kind must be floorplan or codebook")
    filename = Path(file.filename or "upload.pdf").name
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(422, "only PDF files are supported")
    if await _repo(request).get_project(project_id, user_id=user.id) is None:
        raise HTTPException(404, "unknown project")
    document_id = f"doc-{uuid.uuid4().hex[:10]}"
    data_dir = Path(settings.planlint_data_dir).resolve()
    destination = (data_dir / project_id / f"{document_id}-{filename}").resolve()
    if not destination.is_relative_to(data_dir):
        raise HTTPException(400, "invalid path")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        shutil.copyfileobj(file.file, handle)
    try:
        await _repo(request).create_document(
            document_id, project_id, kind, filename, str(destination), user_id=user.id
        )
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {"id": document_id, "kind": kind, "filename": filename}


@router.post("/projects/{project_id}/verify", status_code=202)
async def start_verification(
    project_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Kick off a verification run in the background for an owned project; returns its run_id."""
    _validate_project_id(project_id)
    if await _repo(request).get_project(project_id, user_id=user.id) is None:
        raise HTTPException(404, "unknown project")
    app = request.app
    run_id = f"run-{uuid.uuid4().hex[:10]}"
    manager = app.state.runs
    manager.create(run_id, project_id, user_id=user.id)

    async def emit(event: RunEvent) -> None:
        await manager.emit(run_id, event)

    async def job() -> None:
        try:
            await run_full(
                project_id,
                app.state.repo,
                app.state.embedder,
                settings.planlint_vision_model,
                settings.planlint_text_model,
                run_id=run_id,
                emit=emit,
            )
        except Exception:  # run_full already emitted the error event
            logger.exception("verification run %s failed", run_id)

    run = manager.get(run_id)
    run.task = asyncio.create_task(job())
    return {"run_id": run_id}


@router.get("/runs/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Stream an owned run's progress events as Server-Sent Events (history + live)."""
    manager = request.app.state.runs
    run = manager.get(run_id)
    if run is None or (run.user_id and run.user_id != user.id):
        raise HTTPException(404, "unknown run")

    async def stream():
        async for event in manager.subscribe(run_id):
            yield {"event": "progress", "data": event.model_dump_json()}

    return EventSourceResponse(stream())


@router.get("/projects/{project_id}/results")
async def project_results(
    project_id: str,
    request: Request,
    run_id: str | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Return sheets, assets, clauses, and verdict edges for a run (latest by default)."""
    payload = await _repo(request).results_payload(project_id, run_id, user_id=user.id)
    if not payload:
        raise HTTPException(404, "unknown project")
    return payload


@router.get("/projects/{project_id}/runs")
async def list_project_runs(
    project_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all compliance audit runs for an owned project with verdict summary counts."""
    _validate_project_id(project_id)
    if await _repo(request).get_project(project_id, user_id=user.id) is None:
        raise HTTPException(404, "unknown project")
    return await _repo(request).list_runs(project_id, user_id=user.id)


@router.get("/projects/{project_id}/documents")
async def list_project_documents(
    project_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """List all documents associated with an owned project."""
    _validate_project_id(project_id)
    if await _repo(request).get_project(project_id, user_id=user.id) is None:
        raise HTTPException(404, "unknown project")
    return await _repo(request).get_documents(project_id, user_id=user.id)


@router.get("/documents/{document_id}/pdf")
async def document_pdf(
    document_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Serve a document's original PDF, scoped strictly to the authenticated owner."""
    document = await _repo(request).get_document(document_id, user_id=user.id)
    if document is None or not Path(document["path"]).exists():
        raise HTTPException(404, "document not found")
    return FileResponse(document["path"], media_type="application/pdf")


@router.patch("/documents/{document_id}/scale")
async def set_document_scale(
    document_id: str,
    body: ScaleUpdate,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Set a manual drawing scale to override failed auto-detection."""
    if parse_scale(body.scale_text) is None:
        raise HTTPException(422, "unparseable scale — use forms like '1/4\" = 1'-0\"' or '1:50'")
    repo = _repo(request)
    if await repo.get_document(document_id, user_id=user.id) is None:
        raise HTTPException(404, "document not found")
    await repo.set_document_manual_scale(document_id, body.scale_text, user_id=user.id)
    return {"status": "scale set — re-run verification to re-ingest"}
