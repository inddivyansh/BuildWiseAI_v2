"""Extract structured knowledge from building code text using GraphRAG + Gemini."""

from __future__ import annotations

import json
import structlog

from openai import AsyncOpenAI
from app.config import settings

logger = structlog.get_logger()

# Topic taxonomy for building code clauses
TOPICS = [
    "height",
    "setbacks",
    "lot_coverage",
    "density",
    "parking",
    "fire_safety",
    "accessibility",
    "egress",
    "room_dimensions",
    "ceiling_height",
    "windows",
    "structural",
    "plumbing",
    "electrical",
    "ventilation",
    "insulation",
    "uses",
    "landscaping",
    "signage",
    "definitions",
]

_TOPIC_PROMPT = """\
You are an expert in Canadian building codes (NBC, CCQ) and Quebec municipal zoning.

Given the following building code clause, classify it into one or more topics from this list:
{topics}

Clause:
---
{clause_text}
---

Return a JSON array of matching topic strings. Only include topics that are clearly relevant.
Example: ["height", "setbacks"]
"""

_ENTITY_EXTRACTION_PROMPT = """\
You are an expert in Canadian building codes. Extract structured entities from this clause.

Clause ID: {clause_id}
Jurisdiction: {jurisdiction}
Text:
---
{clause_text}
---

Extract:
1. **requirements**: Numeric thresholds or rules (e.g., "min height 2.1m", "max coverage 60%")
2. **definitions**: Terms being defined in this clause
3. **references**: Other clauses/articles referenced
4. **conditions**: When/where the rule applies

Return JSON:
{{
  "requirements": [
    {{"type": "min|max|exact|range", "metric": "string", "value": number, "unit": "string"}}
  ],
  "definitions": [{{"term": "string", "text": "string"}}],
  "references": ["clause_id_1", "clause_id_2"],
  "conditions": ["string"]
}}
"""


class GraphRAGExtractor:
    """Use Gemini LLM to extract structured knowledge from building code text."""

    def __init__(self):
        self._client = AsyncOpenAI(
            base_url=settings.gemini_base_url,
            api_key=settings.gemini_api_key,
        )
        self._model = settings.llm_model

    async def extract_topics(self, clause_text: str) -> list[str]:
        """Classify a clause into topics using Gemini."""
        prompt = _TOPIC_PROMPT.format(
            topics=", ".join(TOPICS),
            clause_text=clause_text[:2000],
        )
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
            )
            text = resp.choices[0].message.content.strip()
            # Parse JSON array
            topics = json.loads(text)
            return [t for t in topics if t in TOPICS]
        except Exception as e:
            logger.warning("topic_extraction_failed", error=str(e))
            return []

    async def extract_entities(self, clause: dict) -> dict:
        """Extract structured entities from a clause using Gemini."""
        prompt = _ENTITY_EXTRACTION_PROMPT.format(
            clause_id=clause.get("id", ""),
            jurisdiction=clause.get("jurisdiction", ""),
            clause_text=clause.get("text", "")[:3000],
        )
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500,
            )
            text = resp.choices[0].message.content.strip()
            # Strip markdown fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                if text.endswith("```"):
                    text = text[:-3]
            return json.loads(text)
        except Exception as e:
            logger.warning(
                "entity_extraction_failed",
                clause_id=clause.get("id"),
                error=str(e),
            )
            return {"requirements": [], "definitions": [], "references": [], "conditions": []}

    async def extract_from_clauses(
        self,
        clauses: list[dict],
        batch_size: int = 10,
    ) -> dict:
        """Process a list of clauses and extract topics + entities.

        Returns:
            {
                "clause_topics": {clause_id: [topics]},
                "clause_entities": {clause_id: {requirements, definitions, ...}},
            }
        """
        clause_topics: dict[str, list[str]] = {}
        clause_entities: dict[str, dict] = {}

        for i in range(0, len(clauses), batch_size):
            batch = clauses[i:i + batch_size]
            for clause in batch:
                cid = clause["id"]
                topics = await self.extract_topics(clause.get("text", ""))
                clause_topics[cid] = topics

                entities = await self.extract_entities(clause)
                clause_entities[cid] = entities

            logger.info(
                "graphrag_batch_complete",
                processed=min(i + batch_size, len(clauses)),
                total=len(clauses),
            )

        return {
            "clause_topics": clause_topics,
            "clause_entities": clause_entities,
        }
