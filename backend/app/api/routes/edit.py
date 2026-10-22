"""POST /api/edit — Edit an existing floor plan via NL or structured commands.

Persistence contract
--------------------
* Edited plans are stored in plan_store under the new generation_id so
  subsequent export/ingest routes can reach them.
* If the blueprint being edited is durably stored in Neo4j (i.e. it has a
  project_id in the repository), the edited result is also persisted in Neo4j
  under the same project.  This is synchronous — failure returns HTTP 502.
* If the source blueprint is only in plan_store (session-only, no project_id),
  the edited plan is also session-only.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.schemas.generation import EditAction, EditRequest, EditResponse
from app.core.store import plan_store
from app.services.edit.editor import apply_edits
from planlint.api.auth import AuthenticatedUser, get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)


def _repo(request: Request):
    """Return the Neo4j repo if the unified app is running, else None."""
    return getattr(request.app.state, "repo", None)


async def _persist_edited(
    request: Request,
    floor_plan,
    source_blueprint_id: str,
    user_id: str | None = None,
) -> str | None:
    """Persist an edited blueprint under the same project as the source.

    Returns the project_id if persisted, None if the source was session-only.
    Raises HTTPException(502) if persistence was expected but failed.
    """
    repo = _repo(request)
    if repo is None:
        return None

    source = await repo.get_blueprint(source_blueprint_id, user_id=user_id)
    if source is None:
        # Source was session-only — edited plan stays session-only too
        return None

    project_id: str = source["project_id"]
    try:
        await repo.create_blueprint(
            blueprint_id=floor_plan.generation_id,
            project_id=project_id,
            floor_plan_json=floor_plan.model_dump_json(),
            generator_model=floor_plan.generator_model,
            total_area_sqm=floor_plan.total_area_sqm,
            compliance_json=None,
            user_id=user_id,
        )
        return project_id
    except Exception as exc:
        logger.error(
            "edited_blueprint_persist_failed",
            generation_id=floor_plan.generation_id,
            source_blueprint_id=source_blueprint_id,
            exc_info=True,
        )
        raise HTTPException(
            502, f"Edit succeeded but could not be saved: {exc}"
        ) from exc


@router.post("/edit", response_model=EditResponse)
async def edit_floor_plan(
    request: EditRequest,
    http_request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> EditResponse:
    """
    Edit an existing floor plan.

    If structured `actions` are provided, applies them deterministically.
    If a natural language `prompt` is given, parses it using the validated LLM provider.

    The edited blueprint is persisted to Neo4j if the source blueprint was
    previously persisted (i.e. had a project_id).
    """
    # Try to find the existing plan (in plan_store or Neo4j blueprint repository scoped to user)
    existing = plan_store.get(request.generation_id, user_id=user.id)
    project_id: str | None = None
    if not existing:
        repo = _repo(http_request)
        if repo:
            row = await repo.get_blueprint(request.generation_id, user_id=user.id)
            if row and row.get("floor_plan"):
                try:
                    from app.schemas.floor_plan import GeneratedFloorPlan
                    existing = GeneratedFloorPlan.model_validate_json(row["floor_plan"])
                    project_id = row.get("project_id")
                    plan_store.put(existing, user_id=user.id, project_id=project_id)
                except Exception:
                    pass

    if not existing:
        raise HTTPException(status_code=404, detail="Floor plan not found")

    actions = request.actions
    if not actions and request.prompt:
        # LLM parsing path with validated model
        from app.services.llm.providers import validate_model_selection
        from app.services.llm.client import LLMClient
        try:
            val_provider, val_model = validate_model_selection(request.model, request.provider)
            edit_llm = LLMClient(provider=val_provider, model=val_model)
            parsed_json = await edit_llm.parse_edit_command(
                prompt=request.prompt,
                current_spec=existing.spec.model_dump(exclude_none=True),
            )
            raw_actions = parsed_json.get("actions", [])
            actions = [EditAction(**a) for a in raw_actions]
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err))
        except Exception as e:
            return EditResponse(
                generation_id=request.generation_id,
                status="failed",
                error=f"Failed to parse edit prompt: {str(e)}",
            )

    if actions:
        # Structured editing path — apply actions to existing spec
        try:
            floor_plan, changes = await apply_edits(
                spec=existing.spec,
                actions=actions,
            )
            if floor_plan:
                # Persist under the same project as the source, if it had one
                persisted_pid = await _persist_edited(
                    http_request, floor_plan, request.generation_id, user_id=user.id
                )
                plan_store.put(floor_plan, user_id=user.id, project_id=persisted_pid or project_id)
                return EditResponse(
                    generation_id=floor_plan.generation_id,
                    status="completed",
                    floor_plan=floor_plan,
                    changes_summary="; ".join(changes),
                )
        except HTTPException:
            raise
        except Exception as e:
            return EditResponse(
                generation_id=request.generation_id,
                status="failed",
                error=str(e),
            )

    # Fallback: if no edits could be applied, just return the existing plan
    return EditResponse(
        generation_id=existing.generation_id,
        status="completed",
        floor_plan=existing,
        changes_summary="No changes were made.",
    )
