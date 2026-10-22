"""POST /api/generate — Natural language → floor plan.

Persistence contract
--------------------
* `plan_store` always receives every successfully generated plan so that
  the edit and export routes (which run in the same process) can look up
  plans without a database round-trip.
* Neo4j persistence only happens when the caller supplies `project_id` in the
  request body.  Persistence is **synchronous and awaited** before the HTTP
  response is returned — if the write fails, an HTTP 502 is returned so the
  caller is never misled into thinking the blueprint is durable when it is not.
* When `project_id` is omitted the blueprint lives in-process only (session-
  scoped, evicted by the LRU cap in plan_store). Callers that need durable
  storage must supply a valid project_id obtained from POST /projects.

See app/api/_persist.py for the shared persistence helper used here and
in ingest.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.api._persist import persist_blueprint
from app.schemas.generation import GenerateRequest, GenerateResponse
from app.schemas.floor_plan import FloorPlanSpec, GeneratedFloorPlan, ComplianceResult
from app.core.pipeline import GenerationPipeline
from app.core.store import plan_store
from planlint.api.auth import AuthenticatedUser, get_current_user

router = APIRouter()

# Singleton pipeline instance
_pipeline: GenerationPipeline | None = None


def get_pipeline() -> GenerationPipeline:
    global _pipeline
    if _pipeline is None:
        _pipeline = GenerationPipeline()
    return _pipeline


@router.post("/generate", response_model=GenerateResponse)
async def generate_floor_plan(
    request_body: GenerateRequest,
    request: Request,
    user: AuthenticatedUser = Depends(get_current_user),
) -> GenerateResponse:
    """
    Generate a floor plan from a natural language description.

    Uses the full pipeline: LLM → constraint solver → geometry cleanup.
    Explicitly reports an error if the LLM provider is unavailable or quota is exceeded.

    If `project_id` is provided the blueprint is durably persisted in Neo4j
    under that project before the response is returned.  If persistence fails
    a 502 is returned so you are never misled about durability.
    """
    pipeline = get_pipeline()
    compliance: ComplianceResult | None = None
    floor_plan: GeneratedFloorPlan | None = None

    # Validate requested model if provided (rejects unknown or premium models)
    from app.services.llm.providers import validate_model_selection
    from fastapi import HTTPException
    try:
        validated_provider, validated_model = validate_model_selection(
            request_body.model, request_body.provider
        )
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))

    # Initialize client for availability check
    from app.services.llm.client import LLMClient
    active_llm = LLMClient(provider=validated_provider, model=validated_model)
    llm_ok, err_msg = await active_llm.verify_availability()

    if not llm_ok:
        status_code = (
            429 if ("429" in (err_msg or "") or "quota" in (err_msg or "").lower())
            else 503 if ("503" in (err_msg or "") or "unavailable" in (err_msg or "").lower())
            else 502
        )
        raise HTTPException(
            status_code=status_code,
            detail=err_msg or f"{validated_provider} ({validated_model}) is unresponsive. Please check your API quota or retry shortly.",
        )

    # Full pipeline with validated LLM
    result = await pipeline.generate_from_nl(
        prompt=request_body.prompt,
        plot_width_m=request_body.plot_width_m,
        plot_depth_m=request_body.plot_depth_m,
        locale=request_body.locale,
        run_compliance=request_body.run_compliance,
        generator=request_body.generator,
        zone_id=request_body.zone_id,
        model=validated_model,
        provider=validated_provider,
    )

    floor_plan = result.get("floor_plan")
    compliance = result.get("compliance")

    if floor_plan is None:
        first_error = (result.get("errors") or ["Failed to generate floor plan from prompt. Please rephrase or try another model."])[0]
        err_lower = first_error.lower()
        status_code = (
            429 if ("429" in err_lower or "quota" in err_lower)
            else 503 if ("503" in err_lower or "unavailable" in err_lower or "high demand" in err_lower)
            else 500
        )
        raise HTTPException(status_code=status_code, detail=first_error)

    # Always put into in-process store with user/project ownership metadata
    plan_store.put(floor_plan, user_id=user.id, project_id=request_body.project_id)

    # Durable persistence — synchronous, errors surface as HTTP failures
    persisted_project_id: str | None = None
    if request_body.project_id:
        await persist_blueprint(
            request, floor_plan, request_body.project_id, compliance, user_id=user.id
        )
        persisted_project_id = request_body.project_id

    # If there were non-fatal adjustment notices (e.g. area budget scaling), surface them in error/warning field
    notice_msg = "; ".join(result["errors"]) if result.get("errors") else None

    return GenerateResponse(
        generation_id=floor_plan.generation_id,
        status="completed",
        floor_plan=floor_plan,
        parsed_spec=floor_plan.spec,
        compliance=compliance,
        error=notice_msg,
        project_id=persisted_project_id,
    )


@router.post("/generate/from-spec", response_model=GenerateResponse)
async def generate_from_spec(
    spec: FloorPlanSpec,
    request: Request,
    project_id: str | None = None,
    user: AuthenticatedUser = Depends(get_current_user),
) -> GenerateResponse:
    """
    Generate a floor plan from a pre-built FloorPlanSpec (no LLM needed).
    Useful for testing the generation pipeline directly.

    Pass `project_id` as a query parameter to persist durably.
    """
    pipeline = get_pipeline()
    result = await pipeline.generate_from_spec(
        spec=spec,
        generator="housediffusion",
    )

    if not result.get("floor_plan"):
        return GenerateResponse(
            generation_id=result["generation_id"],
            status="failed",
            error="; ".join(result.get("errors", ["Unknown error"])),
        )

    floor_plan: GeneratedFloorPlan = result["floor_plan"]
    plan_store.put(floor_plan, user_id=user.id, project_id=project_id)

    persisted_project_id: str | None = None
    if project_id:
        await persist_blueprint(
            request, floor_plan, project_id, result.get("compliance"), user_id=user.id
        )
        persisted_project_id = project_id

    return GenerateResponse(
        generation_id=floor_plan.generation_id,
        status="completed",
        floor_plan=floor_plan,
        parsed_spec=floor_plan.spec,
        project_id=persisted_project_id,
    )
