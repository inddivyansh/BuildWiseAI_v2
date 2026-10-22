"""Tier 3: Gemini-powered interpretation of ambiguous building code provisions."""

from __future__ import annotations

import json

import structlog
from openai import AsyncOpenAI

from app.config import settings
from app.schemas.floor_plan import ComplianceViolation, GeneratedFloorPlan
from app.services.compliance.lancedb_indexer import LanceDBIndexer
from app.services.compliance.neo4j_client import ComplianceGraphClient
from app.services.compliance.zone_spec_parser import ZoneSpec

logger = structlog.get_logger()

_SYSTEM_PROMPT = """\
You are an expert in Canadian building codes (NBC, CCQ) and Quebec municipal zoning.
You have access to:
- National Building Code of Canada (Part 9: Housing & Small Buildings)
- Code de construction du Québec
- Municipal zoning bylaws (Sainte-Foy-Sillery-Cap-Rouge)

Given a floor plan summary and relevant code excerpts, identify:
1. Violations not caught by automated geometric checks
2. Ambiguous provisions that need professional review
3. Confirmations that the plan meets requirements

For each finding, cite the specific article/clause number and jurisdiction.
Respond in English. Return ONLY valid JSON (no markdown fences):
{
  "violations": [
    {
      "rule_id": "string (e.g. NBC_9.8.2.1)",
      "severity": "error|warning|info",
      "description": "string",
      "affected_rooms": ["room_id"],
      "code_reference": "string (e.g. NBC 9.8.2.1)",
      "suggestion": "string or null"
    }
  ],
  "confirmations": ["string"]
}
"""

_USER_PROMPT_TEMPLATE = """\
## Floor Plan Summary
- Total area: {total_area:.1f} sqm
- Rooms: {room_count}
- Floors: {num_floors}
- Lot: {lot_width}m x {lot_depth}m
- Lot coverage: {coverage:.1%}
- Generator: {generator}

## Rooms
{rooms_text}

## Zone Information
{zone_text}

## Relevant Building Code Excerpts
{code_excerpts}

## Existing Automated Violations
{existing_violations}

Please identify any ADDITIONAL violations or concerns not already listed above.
Focus on:
- Fire safety and egress requirements
- Accessibility compliance
- Structural implications
- Special zone provisions
- Cross-referenced articles that may apply
"""


class LLMComplianceInterpreter:
    """Tier 3: Use Gemini to interpret ambiguous building code provisions.

    Combines:
    - Vector search (LanceDB) for relevant clauses
    - Graph queries (Neo4j) for cross-references and definitions
    - LLM reasoning for interpretation
    """

    def __init__(
        self,
        lancedb_indexer: LanceDBIndexer | None = None,
        graph_client: ComplianceGraphClient | None = None,
    ):
        self._client = AsyncOpenAI(
            base_url=settings.gemini_base_url,
            api_key=settings.gemini_api_key,
        )
        self._model = settings.llm_model
        self._lancedb = lancedb_indexer
        self._graph = graph_client

    async def interpret(
        self,
        plan: GeneratedFloorPlan,
        zone_id: str | None,
        zone_spec: ZoneSpec | None,
        existing_violations: list[ComplianceViolation],
    ) -> list[ComplianceViolation]:
        """Run LLM compliance interpretation.

        Steps:
        1. Build plan summary
        2. Vector search for relevant clauses
        3. Fetch cross-references and definitions from Neo4j
        4. Ask Gemini to find additional violations
        5. Parse response into ComplianceViolation objects
        """
        # 1. Build plan summary
        plan_summary = self._build_plan_summary(plan)
        zone_text = self._build_zone_text(zone_spec, zone_id)

        # 2. Vector search for relevant clauses
        code_excerpts = await self._get_relevant_clauses(plan, zone_id)

        # 3. Fetch definitions for terms in the clauses
        if self._graph and code_excerpts:
            terms = _extract_technical_terms(code_excerpts)
            if terms:
                definitions = await self._graph.get_definitions(terms[:10])
                if definitions:
                    code_excerpts += "\n\n## Definitions\n"
                    for d in definitions:
                        code_excerpts += f"- **{d['term']}**: {d['text'][:200]}\n"

        # 4. Build user prompt
        existing_text = ""
        if existing_violations:
            existing_text = "\n".join(
                f"- [{v.severity}] {v.rule_id}: {v.description}"
                for v in existing_violations[:10]
            )
        else:
            existing_text = "(none)"

        user_prompt = _USER_PROMPT_TEMPLATE.format(
            **plan_summary,
            zone_text=zone_text,
            code_excerpts=code_excerpts or "(no code excerpts available)",
            existing_violations=existing_text,
        )

        # 5. Call Gemini with multi-model failover pool
        candidate_models = [
            "gemini-3.5-flash-lite",
            "gemini-3.5-flash",
            "gemini-3.8-flash",
            "gemini-3.7-flash",
            "gemini-flash-latest",
        ]
        if self._model and self._model in candidate_models:
            candidates = [self._model] + [m for m in candidate_models if m != self._model]
        else:
            candidates = candidate_models

        for idx, candidate in enumerate(candidates):
            try:
                resp = await self._client.chat.completions.create(
                    model=candidate,
                    messages=[
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,
                    max_tokens=2000,
                )
                content = resp.choices[0].message.content.strip()

                # Strip markdown fences if present
                if content.startswith("```"):
                    content = content.split("\n", 1)[1]
                    if content.endswith("```"):
                        content = content[:-3]

                data = json.loads(content)
                return self._parse_violations(data)

            except json.JSONDecodeError as e:
                logger.warning("llm_compliance_json_parse_failed", model=candidate, error=str(e))
                return []
            except Exception as e:
                err_str = str(e)
                status_code = getattr(e, "status_code", None)
                is_rate_limit = (
                    status_code == 429
                    or "429" in err_str
                    or "quota" in err_str.lower()
                    or "rate limit" in err_str.lower()
                    or "resource_exhausted" in err_str.lower()
                )
                if is_rate_limit and idx < len(candidates) - 1:
                    logger.warning(
                        "llm_compliance_model_quota_shifting",
                        exhausted_model=candidate,
                        next_model=candidates[idx + 1],
                    )
                    continue
                logger.warning("llm_compliance_failed", model=candidate, error=err_str[:120])
                break

        return []

    async def _get_relevant_clauses(
        self, plan: GeneratedFloorPlan, zone_id: str | None
    ) -> str:
        """Find relevant building code clauses via vector search."""
        if not self._lancedb:
            return ""

        # Build search queries from plan characteristics
        queries = [
            f"residential floor plan {plan.spec.num_floors} floors",
            "minimum room dimensions habitable",
        ]

        room_types = {r.type.value for r in plan.spec.rooms}
        if "bedroom" in room_types or "master_bedroom" in room_types:
            queries.append("bedroom minimum area dimensions window")
        if "kitchen" in room_types:
            queries.append("kitchen requirements ventilation")
        if "staircase" in room_types:
            queries.append("staircase egress width")
        if plan.spec.num_floors > 1:
            queries.append("multi-storey egress fire separation")

        excerpts = []
        seen_ids = set()
        for query in queries[:5]:
            results = await self._lancedb.search(query, top_k=3)
            for r in results:
                if r["id"] not in seen_ids:
                    seen_ids.add(r["id"])
                    excerpts.append(
                        f"### {r['id']} ({r.get('jurisdiction', '')})\n{r['text'][:500]}"
                    )

        return "\n\n".join(excerpts[:15])

    def _build_plan_summary(self, plan: GeneratedFloorPlan) -> dict:
        rooms_text = "\n".join(
            f"- {r.label} ({r.type.value}): {r.actual_area_sqm or r.target_area_sqm:.1f} sqm"
            for r in plan.spec.rooms
        )
        return {
            "total_area": plan.total_area_sqm,
            "room_count": len(plan.spec.rooms),
            "num_floors": plan.spec.num_floors,
            "lot_width": plan.spec.plot.width_m,
            "lot_depth": plan.spec.plot.depth_m,
            "coverage": plan.lot_coverage_ratio,
            "generator": plan.generator_model,
            "rooms_text": rooms_text,
        }

    def _build_zone_text(self, zone_spec: ZoneSpec | None, zone_id: str | None) -> str:
        if not zone_spec:
            return f"Zone: {zone_id or 'unknown'} (no spec data available)"

        lines = [f"Zone: {zone_spec.zone_id}"]
        if zone_spec.authorized_uses:
            lines.append(f"Authorized uses: {', '.join(zone_spec.authorized_uses)}")
        if zone_spec.max_height_m:
            lines.append(f"Max height: {zone_spec.max_height_m}m")
        if zone_spec.front_margin_m:
            lines.append(f"Front margin: {zone_spec.front_margin_m}m")
        if zone_spec.rear_margin_m:
            lines.append(f"Rear margin: {zone_spec.rear_margin_m}m")
        if zone_spec.min_green_area_pct:
            lines.append(f"Min green area: {zone_spec.min_green_area_pct}%")
        if zone_spec.max_density_units_per_ha:
            lines.append(f"Max density: {zone_spec.max_density_units_per_ha} units/ha")
        if zone_spec.special_provisions:
            lines.append("Special provisions:")
            for prov in zone_spec.special_provisions[:5]:
                lines.append(f"  - {prov[:200]}")
        return "\n".join(lines)

    def _parse_violations(self, data: dict) -> list[ComplianceViolation]:
        """Parse LLM JSON response into ComplianceViolation objects."""
        violations = []
        for v in data.get("violations", []):
            try:
                violations.append(ComplianceViolation(
                    rule_id=v.get("rule_id", "LLM_FINDING"),
                    severity=v.get("severity", "info"),
                    description=v.get("description", ""),
                    affected_rooms=v.get("affected_rooms", []),
                    code_reference=v.get("code_reference", ""),
                    suggestion=v.get("suggestion"),
                ))
            except Exception:
                pass
        return violations


def _extract_technical_terms(text: str) -> list[str]:
    """Extract potential technical terms that might have legal definitions."""
    import re
    # Look for quoted terms and key building code vocabulary
    terms = set()
    for m in re.finditer(r"[«»\"']([^«»\"']{3,30})[«»\"']", text):
        terms.add(m.group(1).strip().lower())

    # Add known building code terms if they appear
    known = [
        "aire constructible", "marge avant", "marge arrière", "marge latérale",
        "hauteur", "étage", "logement", "superficie de plancher",
        "aire d'agrément", "aire verte", "bâtiment principal",
    ]
    text_lower = text.lower()
    for term in known:
        if term in text_lower:
            terms.add(term)
    return list(terms)[:20]
