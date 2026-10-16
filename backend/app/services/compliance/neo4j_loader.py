"""Load extracted building code data into Neo4j."""

from __future__ import annotations

import json
from pathlib import Path

import structlog
from neo4j import AsyncGraphDatabase

from app.services.compliance.zone_spec_parser import ZoneSpec

logger = structlog.get_logger()


class Neo4jLoader:
    """Batch-load building code clauses, zones, and relationships into Neo4j."""

    def __init__(self, uri: str, user: str, password: str, database: str = ""):
        self._driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        self._database = database or None

    async def load_jurisdictions(self) -> None:
        """Create the jurisdiction hierarchy: NBC -> CCQ -> Municipal."""
        async with self._driver.session(database=self._database) as session:
            await session.run(
                """
                MERGE (nbc:Jurisdiction {name: "NBC", level: "national"})
                MERGE (ccq:Jurisdiction {name: "CCQ", level: "provincial"})
                MERGE (mun:Jurisdiction {name: "municipal", level: "municipal"})
                MERGE (ccq)-[:CHILD_OF]->(nbc)
                MERGE (mun)-[:CHILD_OF]->(ccq)
                """
            )
        logger.info("jurisdictions_loaded")

    async def load_clauses(self, clauses: list[dict]) -> int:
        """Batch-insert Clause nodes using UNWIND."""
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                UNWIND $clauses AS c
                MERGE (cl:Clause {id: c.id})
                SET cl.title = c.title,
                    cl.text = c.text,
                    cl.jurisdiction = c.jurisdiction,
                    cl.source_file = c.source_file,
                    cl.page = c.page,
                    cl.chapter = c.chapter
                WITH cl, c
                MATCH (j:Jurisdiction {name: c.jurisdiction})
                MERGE (cl)-[:BELONGS_TO]->(j)
                RETURN count(cl) AS cnt
                """,
                clauses=clauses,
            )
            record = await result.single()
            count = record["cnt"] if record else 0
        logger.info("clauses_loaded", count=count)
        return count

    async def load_cross_references(self, clauses: list[dict]) -> int:
        """Create REFERENCES relationships between clauses from cross_refs."""
        refs = []
        for clause in clauses:
            src = clause["id"]
            jurisdiction = clause.get("jurisdiction", "")
            for ref_id in clause.get("cross_refs", []):
                # Try to find the referenced clause with same jurisdiction prefix
                refs.append({
                    "src": src,
                    "dst_raw": ref_id,
                    "dst_full": f"{jurisdiction}:{ref_id}",
                })

        if not refs:
            return 0

        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                UNWIND $refs AS r
                MATCH (src:Clause {id: r.src})
                OPTIONAL MATCH (dst:Clause {id: r.dst_full})
                OPTIONAL MATCH (dst2:Clause) WHERE dst2.id ENDS WITH ':' + r.dst_raw
                WITH src, COALESCE(dst, dst2) AS target
                WHERE target IS NOT NULL
                MERGE (src)-[:REFERENCES]->(target)
                RETURN count(*) AS cnt
                """,
                refs=refs,
            )
            record = await result.single()
            count = record["cnt"] if record else 0
        logger.info("cross_references_loaded", count=count)
        return count

    async def load_zones(self, geojson_path: Path) -> int:
        """Load zone nodes from GeoJSON."""
        with open(geojson_path, encoding="utf-8") as f:
            data = json.load(f)

        zones = []
        for feat in data.get("features", []):
            props = feat.get("properties", {})
            zone_id = props.get("ZONE_ID") or props.get("zone_id") or props.get("ID", "")
            if not zone_id:
                continue
            zones.append({
                "id": zone_id,
                "dominante": props.get("DOMINANTE", ""),
                "geojson": json.dumps(feat.get("geometry", {})),
            })

        if not zones:
            return 0

        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                UNWIND $zones AS z
                MERGE (zone:Zone {id: z.id})
                SET zone.dominante = z.dominante,
                    zone.geojson = z.geojson
                RETURN count(zone) AS cnt
                """,
                zones=zones,
            )
            record = await result.single()
            count = record["cnt"] if record else 0
        logger.info("zones_loaded", count=count)
        return count

    async def load_zone_specs(self, specs: dict[str, ZoneSpec]) -> int:
        """Load ZoneSpec nodes and link to zones + referenced articles."""
        spec_data = []
        for zone_id, zs in specs.items():
            spec_data.append({
                "zone_id": zone_id,
                "authorized_uses": zs.authorized_uses,
                "max_height_m": zs.max_height_m,
                "front_margin_m": zs.front_margin_m,
                "side_margin_m": zs.side_margin_m,
                "rear_margin_m": zs.rear_margin_m,
                "min_lot_area_sqm": zs.min_lot_area_sqm,
                "min_green_area_pct": zs.min_green_area_pct,
                "max_density": zs.max_density_units_per_ha,
                "parking_type": zs.parking_type,
                "referenced_articles": zs.referenced_articles,
            })

        if not spec_data:
            return 0

        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                UNWIND $specs AS s
                MERGE (zs:ZoneSpec {zone_id: s.zone_id})
                SET zs.authorized_uses = s.authorized_uses,
                    zs.max_height_m = s.max_height_m,
                    zs.front_margin_m = s.front_margin_m,
                    zs.side_margin_m = s.side_margin_m,
                    zs.rear_margin_m = s.rear_margin_m,
                    zs.min_lot_area_sqm = s.min_lot_area_sqm,
                    zs.min_green_area_pct = s.min_green_area_pct,
                    zs.max_density = s.max_density,
                    zs.parking_type = s.parking_type
                WITH zs, s
                MATCH (z:Zone {id: s.zone_id})
                MERGE (z)-[:HAS_SPEC]->(zs)
                RETURN count(zs) AS cnt
                """,
                specs=spec_data,
            )
            record = await result.single()
            count = record["cnt"] if record else 0

            # Link zone specs to referenced articles
            for sd in spec_data:
                for art_id in sd.get("referenced_articles", []):
                    await session.run(
                        """
                        MATCH (zs:ZoneSpec {zone_id: $zone_id})
                        MATCH (c:Clause) WHERE c.id ENDS WITH ':' + $art_id
                        MERGE (zs)-[:REFERENCES_ARTICLE]->(c)
                        """,
                        zone_id=sd["zone_id"],
                        art_id=art_id,
                    )

        logger.info("zone_specs_loaded", count=count)
        return count

    async def load_definitions(self, definitions: list[dict]) -> int:
        """Load Definition nodes from Article 1 / NBC glossary."""
        if not definitions:
            return 0

        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                UNWIND $defs AS d
                MERGE (def:Definition {term: d.term, jurisdiction: d.jurisdiction})
                SET def.text = d.text
                WITH def, d
                MATCH (c:Clause {id: d.article_id})
                MERGE (def)-[:DEFINED_IN]->(c)
                RETURN count(def) AS cnt
                """,
                defs=definitions,
            )
            record = await result.single()
            count = record["cnt"] if record else 0
        logger.info("definitions_loaded", count=count)
        return count

    async def load_topic_relationships(self, clause_topics: dict[str, list[str]]) -> int:
        """Link clauses to Topic nodes."""
        links = []
        for clause_id, topics in clause_topics.items():
            for topic in topics:
                links.append({"clause_id": clause_id, "topic": topic})

        if not links:
            return 0

        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                UNWIND $links AS l
                MERGE (t:Topic {name: l.topic})
                WITH t, l
                MATCH (c:Clause {id: l.clause_id})
                MERGE (c)-[:COVERS_TOPIC]->(t)
                RETURN count(*) AS cnt
                """,
                links=links,
            )
            record = await result.single()
            count = record["cnt"] if record else 0
        logger.info("topic_relationships_loaded", count=count)
        return count

    async def build_amendment_chain(self) -> int:
        """Detect CCQ clauses that amend/override NBC clauses.

        Strategy: match clause IDs sharing the same numeric suffix
        across jurisdictions.
        """
        async with self._driver.session(database=self._database) as session:
            result = await session.run(
                """
                MATCH (ccq:Clause {jurisdiction: "CCQ"})
                MATCH (nbc:Clause {jurisdiction: "NBC"})
                WHERE split(ccq.id, ':')[1] = split(nbc.id, ':')[1]
                MERGE (ccq)-[:AMENDS]->(nbc)
                RETURN count(*) AS cnt
                """
            )
            record = await result.single()
            count = record["cnt"] if record else 0
        logger.info("amendment_chain_built", count=count)
        return count

    async def create_fulltext_indexes(self) -> None:
        """Create fulltext indexes required by neo4j_client.py search queries.

        Creates a 'clause_text' fulltext index on Clause.text + Clause.title
        so that db.index.fulltext.queryNodes('clause_text', ...) works.
        """
        async with self._driver.session(database=self._database) as session:
            await session.run(
                """
                CREATE FULLTEXT INDEX clause_text IF NOT EXISTS
                FOR (c:Clause)
                ON EACH [c.text, c.title]
                """
            )
        logger.info("fulltext_indexes_created")

    async def close(self) -> None:
        await self._driver.close()
