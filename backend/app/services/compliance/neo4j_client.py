"""Neo4j query client for runtime compliance checking."""

from __future__ import annotations

from typing import Any

import structlog
from neo4j import AsyncGraphDatabase

logger = structlog.get_logger()


class ComplianceGraphClient:
    """Query the Neo4j building code knowledge graph at runtime."""

    def __init__(self, uri: str, user: str, password: str, database: str = ""):
        self._driver = AsyncGraphDatabase.driver(
            uri, 
            auth=(user, password),
            max_connection_lifetime=200,
            max_connection_pool_size=50,
            keep_alive=True
        )
        self._database = database or None

    async def get_zone_rules(self, zone_id: str) -> dict[str, Any]:
        """Get all rules applicable to a zone, including cross-referenced articles."""
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH (z:Zone {id: $zone_id})-[:HAS_SPEC]->(s:ZoneSpec)
                OPTIONAL MATCH (s)-[:REFERENCES_ARTICLE]->(c:Clause)
                OPTIONAL MATCH (c)-[:REFERENCES]->(ref:Clause)
                RETURN s, collect(DISTINCT c) AS clauses, collect(DISTINCT ref) AS refs
                """,
                zone_id=zone_id,
            )
            record = await result.single()
            if not record:
                return {"spec": None, "clauses": [], "refs": []}

            spec_node = record["s"]
            spec = dict(spec_node) if spec_node else {}
            clauses = [dict(c) for c in record["clauses"]]
            refs = [dict(r) for r in record["refs"]]

            return {"spec": spec, "clauses": clauses, "refs": refs}

    async def get_rules_for_topic(
        self, topic: str, zone_id: str | None = None
    ) -> list[dict]:
        """Get rules covering a topic across all jurisdictions.

        Returns most restrictive first (municipal > CCQ > NBC).
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH (c:Clause)-[:COVERS_TOPIC]->(:Topic {name: $topic})
                OPTIONAL MATCH (c)-[:BELONGS_TO]->(j:Jurisdiction)
                RETURN c.id AS id, c.text AS text, c.title AS title,
                       c.jurisdiction AS jurisdiction, j.level AS level
                ORDER BY CASE c.jurisdiction
                    WHEN 'municipal' THEN 0
                    WHEN 'CCQ' THEN 1
                    WHEN 'NBC' THEN 2
                    ELSE 3
                END
                """,
                topic=topic,
            )
            records = [dict(r) async for r in result]
            return records

    async def resolve_cross_references(
        self, clause_id: str, depth: int = 2
    ) -> list[dict]:
        """Traverse cross-reference chain up to N hops."""
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                f"""
                MATCH path = (c:Clause {{id: $clause_id}})-[:REFERENCES*1..{depth}]->(ref:Clause)
                RETURN DISTINCT ref.id AS id, ref.text AS text,
                       ref.title AS title, ref.jurisdiction AS jurisdiction
                """,
                clause_id=clause_id,
            )
            records = [dict(r) async for r in result]
            return records

    async def get_jurisdiction_hierarchy(self, topic: str) -> list[dict]:
        """Get rule override chain: NBC -> CCQ -> Municipal for a topic.

        Returns all three levels so the checker can pick the most restrictive.
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH (c:Clause)-[:COVERS_TOPIC]->(:Topic {name: $topic})
                RETURN c.id AS id, c.text AS text, c.title AS title,
                       c.jurisdiction AS jurisdiction
                ORDER BY CASE c.jurisdiction
                    WHEN 'NBC' THEN 0
                    WHEN 'CCQ' THEN 1
                    WHEN 'municipal' THEN 2
                    ELSE 3
                END
                """,
                topic=topic,
            )
            records = [dict(r) async for r in result]
            return records

    async def get_definitions(self, terms: list[str]) -> list[dict]:
        """Look up legal definitions for terms found in clauses."""
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH (d:Definition)
                WHERE d.term IN $terms
                RETURN d.term AS term, d.text AS text, d.jurisdiction AS jurisdiction
                """,
                terms=[t.lower() for t in terms],
            )
            records = [dict(r) async for r in result]
            return records

    async def search_clauses(self, query: str, limit: int = 10) -> list[dict]:
        """Full-text search using Neo4j's built-in index."""
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                CALL db.index.fulltext.queryNodes('clause_text', $query)
                YIELD node, score
                RETURN node.id AS id, node.text AS text, node.title AS title,
                       node.jurisdiction AS jurisdiction, score
                ORDER BY score DESC
                LIMIT $limit
                """,
                query=query,
                limit=limit,
            )
            records = [dict(r) async for r in result]
            return records

    async def get_zone_spec_node(self, zone_id: str) -> dict | None:
        """Get the ZoneSpec node for a zone."""
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH (z:Zone {id: $zone_id})-[:HAS_SPEC]->(s:ZoneSpec)
                RETURN s
                """,
                zone_id=zone_id,
            )
            record = await result.single()
            if record and record["s"]:
                return dict(record["s"])
            return None

    async def health(self) -> bool:
        """Check Neo4j connectivity."""
        try:
            async with self._driver.session(database=self._database) as session:
                result = await session.run("RETURN 1 AS ok")
                record = await result.single()
                return record is not None and record["ok"] == 1
        except Exception:
            return False

    async def get_stats(self) -> dict:
        """Get graph statistics."""
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH (c:Clause) WITH count(c) AS clauses
                MATCH (z:Zone) WITH clauses, count(z) AS zones
                MATCH (t:Topic) WITH clauses, zones, count(t) AS topics
                MATCH (d:Definition) WITH clauses, zones, topics, count(d) AS definitions
                RETURN clauses, zones, topics, definitions
                """
            )
            record = await result.single()
            if record:
                return dict(record)
            return {}

    async def close(self) -> None:
        await self._driver.close()
