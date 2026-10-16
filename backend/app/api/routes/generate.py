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
from app.services.generation.mock import generate_mock_floor_plan
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
    Falls back to mock generation if LLM server is unavailable.

    If `project_id` is provided the blueprint is durably persisted in Neo4j
    under that project before the response is returned.  If persistence fails
    a 502 is returned so you are never misled about durability.
    """
    pipeline = get_pipeline()
    compliance: ComplianceResult | None = None
    floor_plan: GeneratedFloorPlan | None = None

    # Check if LLM is available
    llm_ok = await pipeline.llm.health_check()

    if llm_ok:
        # Full pipeline with LLM
        result = await pipeline.generate_from_nl(
            prompt=request_body.prompt,
            plot_width_m=request_body.plot_width_m,
            plot_depth_m=request_body.plot_depth_m,
            locale=request_body.locale,
            run_compliance=request_body.run_compliance,
            generator=request_body.generator,
            zone_id=request_body.zone_id,
        )
        if result.get("floor_plan"):
            floor_plan = result["floor_plan"]
            compliance = result.get("compliance")

    # Fallback to mock generation if LLM unavailable or pipeline returned nothing
    if floor_plan is None:
        floor_plan = generate_mock_floor_plan(
            prompt=request_body.prompt,
            plot_width_m=request_body.plot_width_m,
            plot_depth_m=request_body.plot_depth_m,
            locale=request_body.locale,
        )

    # Always put into in-process store with user/project ownership metadata
    plan_store.put(floor_plan, user_id=user.id, project_id=request_body.project_id)

    # Durable persistence — synchronous, errors surface as HTTP failures
    persisted_project_id: str | None = None
    if request_body.project_id:
        await persist_blueprint(
            request, floor_plan, request_body.project_id, compliance, user_id=user.id
        )
        persisted_project_id = request_body.project_id

    return GenerateResponse(
        generation_id=floor_plan.generation_id,
        status="completed",
        floor_plan=floor_plan,
        parsed_spec=floor_plan.spec,
        compliance=compliance,
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
