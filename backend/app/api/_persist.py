"""Shared blueprint persistence helper used by generate and ingest routes.

Extracted here to avoid duplicating the same logic and to keep route modules
thin.  Both ``generate.py`` and ``ingest.py`` import ``persist_blueprint``
from this module.
"""

from __future__ import annotations

import logging

from fastapi import HTTPException, Request

from app.schemas.floor_plan import GeneratedFloorPlan, ComplianceResult

logger = logging.getLogger(__name__)


async def persist_blueprint(
    request: Request,
    plan: GeneratedFloorPlan,
    project_id: str,
    compliance: ComplianceResult | None = None,
    user_id: str | None = None,
) -> None:
    """Persist a generated (or ingested) blueprint to Neo4j.

    * Verifies the project exists and belongs to user_id — raises HTTP 404 if not.
    * Raises HTTP 503 if no repository is attached (standalone mode).
    * Raises HTTP 502 if the Neo4j write itself fails.

    The caller is responsible for calling ``plan_store.put(plan, user_id, project_id)``
    before this function so that edit/export routes can find the plan in-session.
    """
    try:
        repo = request.app.state.repo
    except AttributeError:
        raise HTTPException(
            503,
            "Persistence unavailable: no repository attached. "
            "Run the unified app (planlint.api.main:app) or omit project_id.",
        )

    project = await repo.get_project(project_id, user_id=user_id)
    if project is None:
        raise HTTPException(404, f"project '{project_id}' not found")

    compliance_json: str | None = None
    if compliance is not None:
        try:
            compliance_json = compliance.model_dump_json()
        except Exception:
            compliance_json = None

    try:
        await repo.create_blueprint(
            blueprint_id=plan.generation_id,
            project_id=project_id,
            floor_plan_json=plan.model_dump_json(),
            generator_model=plan.generator_model,
            total_area_sqm=plan.total_area_sqm,
            compliance_json=compliance_json,
            user_id=user_id,
        )
    except Exception as exc:
        logger.error(
            "blueprint_persist_failed",
            generation_id=plan.generation_id,
            project_id=project_id,
            exc_info=True,
        )
        raise HTTPException(
            502, f"Blueprint generated but could not be saved: {exc}"
        ) from exc
