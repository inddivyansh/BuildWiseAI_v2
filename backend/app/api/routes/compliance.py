"""Compliance checking API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.schemas.floor_plan import ComplianceResult, FloorPlanSpec, GeneratedFloorPlan
from planlint.api.auth import AuthenticatedUser, get_current_user

router = APIRouter()


class ComplianceCheckRequest(BaseModel):
    """Request body for compliance checking."""
    floor_plan: GeneratedFloorPlan
    zone_id: str | None = None
    locale: str = "CA-QC"
    run_llm: bool = True


@router.post("/compliance/check", response_model=ComplianceResult)
async def check_compliance(
    request: ComplianceCheckRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Check a floor plan against building codes.

    Runs the 3-tier compliance engine:
    - Tier 1: Deterministic NBC Part 9 checks (always)
    - Tier 2: Graph-enhanced zone-specific rules (if Neo4j available)
    - Tier 3: LLM interpretation of ambiguous provisions (if enabled)
    """
    from app.core.pipeline import GenerationPipeline

    pipeline = GenerationPipeline()
    if not pipeline.compliance_checker:
        # Fallback: run tier 1 only
        from app.services.compliance.deterministic_rules import DeterministicRuleChecker
        checker = DeterministicRuleChecker()
        violations = checker.check(request.floor_plan)
        return ComplianceResult(
            is_compliant=not any(v.severity == "error" for v in violations),
            violations=violations,
            checked_rules=8,
            jurisdiction=request.locale,
        )

    result = await pipeline.compliance_checker.check(
        plan=request.floor_plan,
        zone_id=request.zone_id,
        locale=request.locale,
        run_llm=request.run_llm,
    )
    return result


class ComplianceCheckSpecRequest(BaseModel):
    """Lightweight check from spec only (no full GeneratedFloorPlan needed)."""
    spec: FloorPlanSpec
    zone_id: str | None = None


@router.post("/compliance/check-spec", response_model=ComplianceResult)
async def check_compliance_from_spec(
    request: ComplianceCheckSpecRequest,
    user: AuthenticatedUser = Depends(get_current_user),
):
    """Quick compliance pre-check from a FloorPlanSpec (before generation).

    Only runs Tier 1 deterministic checks using target areas.
    """
    from app.services.compliance.deterministic_rules import DeterministicRuleChecker

    # Build a minimal GeneratedFloorPlan for the checker
    plan = GeneratedFloorPlan(
        spec=request.spec,
        walls=[],
        doors=[],
        windows=[],
        generation_id="precheck",
        generator_model="precheck",
        generation_time_s=0,
        total_area_sqm=request.spec.total_target_area,
        lot_coverage_ratio=(
            request.spec.total_target_area / request.spec.plot.buildable_area_sqm
            if request.spec.plot.buildable_area_sqm
            else 0
        ),
        area_deviation_pct=0,
    )

    checker = DeterministicRuleChecker()
    violations = checker.check(plan)
    return ComplianceResult(
        is_compliant=not any(v.severity == "error" for v in violations),
        violations=violations,
        checked_rules=8,
        jurisdiction=request.spec.locale,
    )


@router.get("/compliance/zones/{zone_id}")
async def get_zone_spec(zone_id: str):
    """Get zone specification data."""
    from app.core.pipeline import GenerationPipeline

    pipeline = GenerationPipeline()
    if not pipeline.compliance_checker or not pipeline.compliance_checker._zone_loader:
        raise HTTPException(404, "Zone data not loaded")

    spec = pipeline.compliance_checker._zone_loader.get_zone_spec(zone_id)
    if not spec:
        raise HTTPException(404, f"Zone '{zone_id}' not found")

    return {
        "zone_id": spec.zone_id,
        "authorized_uses": spec.authorized_uses,
        "max_height_m": spec.max_height_m,
        "front_margin_m": spec.front_margin_m,
        "side_margin_m": spec.side_margin_m,
        "rear_margin_m": spec.rear_margin_m,
        "min_lot_area_sqm": spec.min_lot_area_sqm,
        "min_green_area_pct": spec.min_green_area_pct,
        "max_density_units_per_ha": spec.max_density_units_per_ha,
        "parking_type": spec.parking_type,
        "min_underground_parking_pct": spec.min_underground_parking_pct,
        "referenced_articles": spec.referenced_articles,
        "special_provisions": spec.special_provisions,
    }


@router.get("/compliance/zones")
async def list_zones():
    """List all available zone IDs."""
    from app.core.pipeline import GenerationPipeline

    pipeline = GenerationPipeline()
    if not pipeline.compliance_checker or not pipeline.compliance_checker._zone_loader:
        return {"zones": [], "count": 0}

    zones = pipeline.compliance_checker._zone_loader.list_zones()
    return {"zones": zones, "count": len(zones)}


@router.get("/compliance/search")
async def search_clauses(
    q: str = Query(..., min_length=2, description="Search query"),
    jurisdiction: str | None = Query(None, description="Filter by jurisdiction"),
    limit: int = Query(10, ge=1, le=50),
):
    """Search building code clauses by text."""
    from app.core.pipeline import GenerationPipeline

    pipeline = GenerationPipeline()
    if not pipeline.compliance_checker:
        return {"results": [], "count": 0}

    # Try Neo4j full-text search first
    graph = getattr(pipeline.compliance_checker, '_tier2', None)
    if graph and hasattr(graph, '_graph'):
        try:
            results = await graph._graph.search_clauses(q, limit)
            if results:
                return {"results": results, "count": len(results)}
        except Exception:
            pass

    # Fall back to LanceDB vector search via Tier 3's indexer
    tier3 = getattr(pipeline.compliance_checker, '_tier3', None)
    if tier3:
        lancedb_indexer = getattr(tier3, '_lancedb', None)
        if lancedb_indexer:
            try:
                results = await lancedb_indexer.search(
                    q, top_k=limit, jurisdiction=jurisdiction,
                )
                return {"results": results, "count": len(results)}
            except Exception:
                pass

    return {"results": [], "count": 0}
