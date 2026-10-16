"""Main compliance engine — orchestrates all three tiers."""

from __future__ import annotations

import structlog

from app.schemas.floor_plan import ComplianceResult, ComplianceViolation, GeneratedFloorPlan
from app.services.compliance.deterministic_rules import DeterministicRuleChecker
from app.services.compliance.neo4j_client import ComplianceGraphClient
from app.services.compliance.lancedb_indexer import LanceDBIndexer
from app.services.compliance.zone_loader import ZoneDataLoader

logger = structlog.get_logger()


class ComplianceChecker:
    """Three-tier compliance engine.

    Tier 1: Deterministic (always, ~0ms) — NBC Part 9 geometry checks
    Tier 2: Graph-enhanced (if Neo4j + zone_id, ~100ms) — zone-specific rules
    Tier 3: LLM interpretation (if enabled, ~2-5s) — ambiguous provisions
    """

    def __init__(
        self,
        graph_client: ComplianceGraphClient | None = None,
        lancedb_indexer: LanceDBIndexer | None = None,
        zone_loader: ZoneDataLoader | None = None,
    ):
        self._tier1 = DeterministicRuleChecker()
        self._tier2 = None
        self._tier3 = None
        self._zone_loader = zone_loader

        if graph_client and zone_loader:
            from app.services.compliance.graph_rules import GraphRuleChecker
            self._tier2 = GraphRuleChecker(graph_client, zone_loader)

        if lancedb_indexer:
            from app.services.compliance.llm_interpreter import LLMComplianceInterpreter
            self._tier3 = LLMComplianceInterpreter(
                lancedb_indexer=lancedb_indexer,
                graph_client=graph_client,
            )

    async def check(
        self,
        plan: GeneratedFloorPlan,
        zone_id: str | None = None,
        locale: str = "CA-QC",
        run_llm: bool = True,
    ) -> ComplianceResult:
        """Run full compliance check across all available tiers.

        Args:
            plan: The generated floor plan to check.
            zone_id: Municipal zone ID (e.g. "31001Ma").
            locale: Jurisdiction locale (e.g. "CA-QC").
            run_llm: Whether to run Tier 3 LLM interpretation.

        Returns:
            ComplianceResult with violations sorted by severity.
        """
        all_violations: list[ComplianceViolation] = []
        checked_rules = 0

        # --- Tier 1: Deterministic (always runs) ---
        try:
            t1 = self._tier1.check(plan, zone_id=zone_id, zone_loader=self._zone_loader)
            all_violations.extend(t1)
            checked_rules += 8  # number of check methods
            logger.info("compliance_tier1_complete", violations=len(t1))
        except Exception as e:
            logger.error("compliance_tier1_failed", error=str(e))

        # --- Tier 2: Graph-enhanced (if available) ---
        if zone_id and self._tier2:
            try:
                t2 = await self._tier2.check(plan, zone_id)
                all_violations.extend(t2)
                checked_rules += len(t2) + 4
                logger.info("compliance_tier2_complete", violations=len(t2))
            except Exception as e:
                logger.warning("compliance_tier2_failed", error=str(e))

        # --- Tier 3: LLM interpretation (if enabled) ---
        if run_llm and self._tier3:
            has_critical = any(v.severity == "error" for v in all_violations)
            # Skip LLM if there are already many critical errors
            if len([v for v in all_violations if v.severity == "error"]) > 10:
                logger.info("compliance_tier3_skipped", reason="too_many_critical_errors")
            else:
                try:
                    zone_spec = None
                    if zone_id and self._zone_loader:
                        zone_spec = self._zone_loader.get_zone_spec(zone_id)
                    t3 = await self._tier3.interpret(
                        plan, zone_id, zone_spec, all_violations
                    )
                    all_violations.extend(t3)
                    checked_rules += len(t3)
                    logger.info("compliance_tier3_complete", violations=len(t3))
                except Exception as e:
                    logger.warning("compliance_tier3_failed", error=str(e))

        # Deduplicate and sort
        violations = _deduplicate_violations(all_violations)
        violations = _sort_violations(violations)

        is_compliant = not any(v.severity == "error" for v in violations)

        return ComplianceResult(
            is_compliant=is_compliant,
            violations=violations,
            checked_rules=checked_rules,
            jurisdiction=locale,
        )


def _deduplicate_violations(
    violations: list[ComplianceViolation],
) -> list[ComplianceViolation]:
    """Remove duplicate violations based on rule_id + affected_rooms."""
    seen = set()
    unique = []
    for v in violations:
        key = (v.rule_id, tuple(sorted(v.affected_rooms)))
        if key not in seen:
            seen.add(key)
            unique.append(v)
    return unique


def _sort_violations(
    violations: list[ComplianceViolation],
) -> list[ComplianceViolation]:
    """Sort by severity: error > warning > info."""
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(violations, key=lambda v: order.get(v.severity, 3))
