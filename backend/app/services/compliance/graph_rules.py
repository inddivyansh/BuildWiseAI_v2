"""Tier 2: Graph-enhanced rules using Neo4j for cross-reference traversal."""

from __future__ import annotations

import re

import structlog

from app.schemas.floor_plan import ComplianceViolation, GeneratedFloorPlan
from app.services.compliance.neo4j_client import ComplianceGraphClient
from app.services.compliance.zone_loader import ZoneDataLoader

logger = structlog.get_logger()


class GraphRuleChecker:
    """Tier 2: Zone-specific and jurisdiction-aware compliance rules.

    Uses the Neo4j knowledge graph to:
    - Retrieve zone-specific rules (setbacks, height, uses)
    - Traverse cross-references between articles
    - Apply jurisdiction hierarchy (municipal > CCQ > NBC)
    """

    def __init__(
        self,
        graph_client: ComplianceGraphClient,
        zone_loader: ZoneDataLoader,
    ):
        self._graph = graph_client
        self._zones = zone_loader

    async def check(
        self,
        plan: GeneratedFloorPlan,
        zone_id: str,
    ) -> list[ComplianceViolation]:
        violations: list[ComplianceViolation] = []

        # 1. Check zone-specific rules from graph
        zone_rules = await self._graph.get_zone_rules(zone_id)
        violations.extend(self._check_zone_spec_rules(plan, zone_rules, zone_id))

        # 2. Check hierarchical rules per topic
        for topic in ("height", "setbacks", "lot_coverage", "density"):
            hierarchy = await self._graph.get_jurisdiction_hierarchy(topic)
            violations.extend(self._check_hierarchical_rule(plan, topic, hierarchy))

        # 3. Resolve cross-references from zone spec
        zone_spec = self._zones.get_zone_spec(zone_id)
        if zone_spec:
            for art_id in zone_spec.referenced_articles[:20]:  # cap to avoid over-querying
                try:
                    refs = await self._graph.resolve_cross_references(
                        f"municipal:{art_id}", depth=2
                    )
                    violations.extend(self._check_referenced_rules(plan, refs, zone_id))
                except Exception:
                    pass

        return violations

    def _check_zone_spec_rules(
        self,
        plan: GeneratedFloorPlan,
        zone_rules: dict,
        zone_id: str,
    ) -> list[ComplianceViolation]:
        """Check rules from the zone spec node in the graph."""
        violations = []
        spec_node = zone_rules.get("spec")
        if not spec_node:
            return violations

        # Height check
        max_height = spec_node.get("max_height_m")
        if max_height:
            from app.config import settings
            building_height = settings.default_floor_height_m * plan.spec.num_floors
            if building_height > max_height:
                violations.append(ComplianceViolation(
                    rule_id="ZONE_HEIGHT_EXCEEDED",
                    severity="error",
                    description=(
                        f"Building height {building_height:.1f}m exceeds "
                        f"zone {zone_id} maximum {max_height:.1f}m"
                    ),
                    affected_rooms=[r.id for r in plan.spec.rooms],
                    code_reference=f"Zone {zone_id} spec",
                    suggestion=f"Reduce to {int(max_height / settings.default_floor_height_m)} floors or less",
                ))

        # Front margin check
        front_margin = spec_node.get("front_margin_m")
        if front_margin:
            for room in plan.spec.rooms:
                if not room.polygon:
                    continue
                for coord in room.polygon:
                    if coord.y < front_margin - 0.01:
                        violations.append(ComplianceViolation(
                            rule_id="ZONE_FRONT_MARGIN",
                            severity="error",
                            description=(
                                f"{room.label} violates zone {zone_id} front margin "
                                f"of {front_margin}m"
                            ),
                            affected_rooms=[room.id],
                            code_reference=f"Zone {zone_id} - marge avant",
                            suggestion=f"Move building at least {front_margin}m from front lot line",
                        ))
                        break

        # Rear margin check
        rear_margin = spec_node.get("rear_margin_m")
        if rear_margin and plan.spec.plot.depth_m:
            limit = plan.spec.plot.depth_m - rear_margin
            for room in plan.spec.rooms:
                if not room.polygon:
                    continue
                for coord in room.polygon:
                    if coord.y > limit + 0.01:
                        violations.append(ComplianceViolation(
                            rule_id="ZONE_REAR_MARGIN",
                            severity="error",
                            description=(
                                f"{room.label} violates zone {zone_id} rear margin "
                                f"of {rear_margin}m"
                            ),
                            affected_rooms=[room.id],
                            code_reference=f"Zone {zone_id} - marge arrière",
                            suggestion=f"Move building at least {rear_margin}m from rear lot line",
                        ))
                        break

        # Green area check
        min_green_pct = spec_node.get("min_green_area_pct")
        if min_green_pct and plan.spec.plot.area_sqm:
            lot_area = plan.spec.plot.area_sqm
            building_area = plan.total_area_sqm
            green_area_pct = (1 - building_area / lot_area) * 100
            if green_area_pct < min_green_pct:
                violations.append(ComplianceViolation(
                    rule_id="ZONE_GREEN_AREA",
                    severity="warning",
                    description=(
                        f"Green area {green_area_pct:.1f}% is below zone {zone_id} "
                        f"minimum {min_green_pct}%"
                    ),
                    affected_rooms=[r.id for r in plan.spec.rooms],
                    code_reference=f"Zone {zone_id} - aire verte",
                    suggestion="Reduce building footprint to meet green area requirements",
                ))

        return violations

    def _check_hierarchical_rule(
        self,
        plan: GeneratedFloorPlan,
        topic: str,
        hierarchy: list[dict],
    ) -> list[ComplianceViolation]:
        """Apply the most restrictive rule from the jurisdiction hierarchy.

        Municipal overrides CCQ overrides NBC, but lower levels can only be
        MORE restrictive, never less.
        """
        violations = []
        if not hierarchy:
            return violations

        # Extract numeric thresholds from clause texts
        for clause in hierarchy:
            text = clause.get("text", "")
            jurisdiction = clause.get("jurisdiction", "")

            if topic == "height":
                height_match = re.search(r"(\d+(?:[.,]\d+)?)\s*m(?:ètres)?", text)
                if height_match:
                    max_h = float(height_match.group(1).replace(",", "."))
                    from app.config import settings
                    actual_h = settings.default_floor_height_m * plan.spec.num_floors
                    if actual_h > max_h:
                        violations.append(ComplianceViolation(
                            rule_id=f"HIERARCHY_{topic.upper()}",
                            severity="error",
                            description=(
                                f"Building height {actual_h:.1f}m exceeds {jurisdiction} "
                                f"limit of {max_h:.1f}m"
                            ),
                            affected_rooms=[r.id for r in plan.spec.rooms],
                            code_reference=f"{jurisdiction}: {clause.get('id', '')}",
                        ))
                    break  # Most restrictive found

        return violations

    def _check_referenced_rules(
        self,
        plan: GeneratedFloorPlan,
        refs: list[dict],
        zone_id: str,
    ) -> list[ComplianceViolation]:
        """Check rules from cross-referenced articles."""
        violations = []
        for ref in refs:
            text = ref.get("text", "").lower()
            # Look for explicit prohibitions
            if "prohib" in text or "interdit" in text:
                violations.append(ComplianceViolation(
                    rule_id="XREF_PROHIBITION",
                    severity="info",
                    description=(
                        f"Referenced article {ref.get('id', '')} contains a prohibition: "
                        f"{ref.get('title', '')}"
                    ),
                    affected_rooms=[],
                    code_reference=ref.get("id", ""),
                    suggestion="Review referenced article for applicable restrictions",
                ))
        return violations
