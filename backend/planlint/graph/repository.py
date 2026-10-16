"""GraphRepository: every Cypher statement in the system lives here.

All writes are parameterized; relationship types are interpolated only from
the VerdictType enum (closed set), never from user input.
"""

from __future__ import annotations

import json
from typing import Any

from neo4j import AsyncDriver

from planlint.config import settings
from planlint.models import (
    CheckResult,
    Constraint,
    Detail,
    PhysicalAsset,
    RegulationClause,
    SheetReference,
    Spec,
    VerdictType,
)


class GraphRepository:
    """The sole gateway to Neo4j — one method per graph operation, all
    parameterized. Swappable with `FakeRepository` in tests via `create_app`."""

    def __init__(self, driver: AsyncDriver, database: str = "neo4j"):
        self._driver = driver
        self._db = database

    async def _run(self, query: str, **params: Any) -> list[dict]:
        async with self._driver.session(database=self._db) as session:
            result = await session.run(query, **params)
            return [record.data() async for record in result]

    # ------------------------------------------------------------- schema

    # ------------------------------------------------------------- schema

    async def init_schema(self) -> None:
        for label in ("Project", "Document", "Sheet", "PhysicalAsset", "Regulation", "Constraint", "Spec", "Detail", "Blueprint"):
            await self._run(
                f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            )
        await self._run(
            "CREATE INDEX project_user_id IF NOT EXISTS FOR (p:Project) ON (p.user_id)"
        )
        await self._run(
            "CREATE VECTOR INDEX regulation_embedding IF NOT EXISTS "
            "FOR (r:Regulation) ON (r.embedding) "
            "OPTIONS {indexConfig: {`vector.dimensions`: $dims, "
            "`vector.similarity_function`: 'cosine'}}",
            dims=settings.embed_dimensions,
        )

    # ------------------------------------------------------------ projects

    async def create_project(
        self,
        project_id: str,
        name: str,
        description: str | None = None,
        user_id: str | None = None,
    ) -> None:
        await self._run(
            "CREATE (p:Project {id: $id, name: $name, description: $description, "
            "user_id: $user_id, created_at: datetime()})",
            id=project_id,
            name=name,
            description=description,
            user_id=user_id,
        )

    async def get_project(self, project_id: str, user_id: str | None = None) -> dict | None:
        rows = await self._run(
            "MATCH (p:Project {id: $id}) "
            "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
            "SET p.user_id = CASE WHEN p.user_id IS NULL AND $uid IS NOT NULL THEN $uid ELSE p.user_id END "
            "WITH p "
            "OPTIONAL MATCH (p)-[:HAS_DOCUMENT]->(d:Document) "
            "OPTIONAL MATCH (p)-[:HAS_BLUEPRINT]->(b:Blueprint) "
            "RETURN p.id AS id, p.name AS name, p.description AS description, p.user_id AS user_id, "
            "toString(p.created_at) AS created_at, "
            "count(DISTINCT d) AS document_count, count(DISTINCT b) AS blueprint_count",
            id=project_id,
            uid=user_id,
        )
        return rows[0] if rows and rows[0]["id"] is not None else None

    async def update_project(
        self,
        project_id: str,
        name: str | None = None,
        description: str | None = None,
        user_id: str | None = None,
    ) -> dict | None:
        """Update a project's name and/or description. Returns updated project or None if not found/unauthorized."""
        rows = await self._run(
            "MATCH (p:Project {id: $id}) "
            "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
            "SET p.name = coalesce($name, p.name), "
            "    p.description = CASE WHEN $desc_set THEN $description ELSE p.description END, "
            "    p.user_id = coalesce(p.user_id, $uid) "
            "RETURN p.id AS id",
            id=project_id,
            name=name,
            description=description,
            desc_set=description is not None,
            uid=user_id,
        )
        if not rows:
            return None
        return await self.get_project(project_id, user_id=user_id)

    async def delete_project(self, project_id: str, user_id: str | None = None) -> list[str] | None:
        """Delete a project and everything under it: documents, sheets, assets,
        regulations, constraints, and (via DETACH) all verdict edges. Returns
        the document file paths so the caller can clean up disk, or None if the
        project doesn't exist."""
        rows = await self._run(
            "MATCH (p:Project {id: $pid}) "
            "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
            "OPTIONAL MATCH (p)-[:HAS_DOCUMENT]->(d:Document) "
            "OPTIONAL MATCH (d)-[:HAS_SHEET]->(s:Sheet) "
            "OPTIONAL MATCH (s)-[:CONTAINS]->(a:PhysicalAsset) "
            "OPTIONAL MATCH (s)-[:HAS_DETAIL]->(dt:Detail) "
            "OPTIONAL MATCH (d)-[:HAS_CLAUSE]->(r:Regulation) "
            "OPTIONAL MATCH (r)-[:DEFINES]->(c:Constraint) "
            "OPTIONAL MATCH (d)-[:HAS_SPEC]->(sp:Spec) "
            "OPTIONAL MATCH (p)-[:HAS_BLUEPRINT]->(b:Blueprint) "
            "WITH p, [x IN collect(DISTINCT d) | x.path] AS paths, "
            "collect(DISTINCT d) + collect(DISTINCT s) + collect(DISTINCT a) + collect(DISTINCT dt) "
            "+ collect(DISTINCT r) + collect(DISTINCT c) + collect(DISTINCT sp) + collect(DISTINCT b) AS descendants "
            "FOREACH (n IN descendants | DETACH DELETE n) "
            "DETACH DELETE p "
            "RETURN paths",
            pid=project_id,
            uid=user_id,
        )
        return rows[0]["paths"] if rows else None

    async def list_projects(self, user_id: str | None = None) -> list[dict]:
        return await self._run(
            "MATCH (p:Project) "
            "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
            "OPTIONAL MATCH (p)-[:HAS_DOCUMENT]->(d:Document) "
            "OPTIONAL MATCH (p)-[:HAS_BLUEPRINT]->(b:Blueprint) "
            "RETURN p.id AS id, p.name AS name, p.description AS description, p.user_id AS user_id, "
            "toString(p.created_at) AS created_at, "
            "count(DISTINCT d) AS document_count, count(DISTINCT b) AS blueprint_count "
            "ORDER BY created_at DESC",
            uid=user_id,
        )

    # ----------------------------------------------------------- documents

    async def create_document(
        self,
        document_id: str,
        project_id: str,
        kind: str,
        filename: str,
        path: str,
        user_id: str | None = None,
    ) -> None:
        await self._run(
            "MATCH (p:Project {id: $project_id}) "
            "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
            "CREATE (p)-[:HAS_DOCUMENT]->(:Document {id: $id, kind: $kind, filename: $filename, "
            "path: $path, pdf_type: null, ingested: false, project_id: $project_id})",
            project_id=project_id,
            id=document_id,
            kind=kind,
            filename=filename,
            path=path,
            uid=user_id,
        )

    async def get_document(self, document_id: str, user_id: str | None = None) -> dict | None:
        rows = await self._run(
            "MATCH (p:Project)-[:HAS_DOCUMENT]->(d:Document {id: $id}) "
            "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
            "RETURN d { .* } AS doc, p.user_id AS owner_id",
            id=document_id,
            uid=user_id,
        )
        return rows[0]["doc"] if rows else None

    async def get_documents(self, project_id: str, user_id: str | None = None) -> list[dict]:
        return [
            row["doc"]
            for row in await self._run(
                "MATCH (p:Project {id: $pid})-[:HAS_DOCUMENT]->(d:Document) "
                "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
                "RETURN d { .* } AS doc ORDER BY d.filename",
                pid=project_id,
                uid=user_id,
            )
        ]

    async def delete_document(self, document_id: str, user_id: str | None = None) -> dict | None:
        """Delete one document and its subtree. A floorplan owns sheets/assets,
        a codebook owns regulations/constraints; the OPTIONAL MATCHes cover
        both. DETACH DELETE also removes verdict edges touching the deleted
        nodes, so surviving assets simply lose verdicts against a deleted
        codebook — no orphan edges are possible. Returns {path, kind,
        project_id} for disk cleanup, or None if the document doesn't exist."""
        rows = await self._run(
            "MATCH (p:Project)-[:HAS_DOCUMENT]->(d:Document {id: $id}) "
            "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
            "OPTIONAL MATCH (d)-[:HAS_SHEET]->(s:Sheet) "
            "OPTIONAL MATCH (s)-[:CONTAINS]->(a:PhysicalAsset) "
            "OPTIONAL MATCH (s)-[:HAS_DETAIL]->(dt:Detail) "
            "OPTIONAL MATCH (d)-[:HAS_CLAUSE]->(r:Regulation) "
            "OPTIONAL MATCH (r)-[:DEFINES]->(c:Constraint) "
            "OPTIONAL MATCH (d)-[:HAS_SPEC]->(sp:Spec) "
            "WITH d, d.path AS path, d.kind AS kind, d.project_id AS project_id, "
            "collect(DISTINCT s) + collect(DISTINCT a) + collect(DISTINCT dt) "
            "+ collect(DISTINCT r) + collect(DISTINCT c) + collect(DISTINCT sp) AS descendants "
            "FOREACH (n IN descendants | DETACH DELETE n) "
            "DETACH DELETE d "
            "RETURN path, kind, project_id",
            id=document_id,
            uid=user_id,
        )
        return rows[0] if rows else None

    async def set_document_manual_scale(
        self, document_id: str, scale_text: str, user_id: str | None = None
    ) -> None:
        """Store a user-entered scale and force re-ingestion: existing sheets
        and their assets (with all verdict edges) are removed."""
        await self._run(
            "MATCH (p:Project)-[:HAS_DOCUMENT]->(d:Document {id: $id}) "
            "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
            "SET d.manual_scale_text = $scale_text, d.ingested = false "
            "WITH d OPTIONAL MATCH (d)-[:HAS_SHEET]->(s:Sheet) "
            "OPTIONAL MATCH (s)-[:CONTAINS]->(a:PhysicalAsset) "
            "DETACH DELETE s, a",
            id=document_id,
            scale_text=scale_text,
            uid=user_id,
        )

    async def mark_ingested(self, document_id: str, pdf_type: str | None = None) -> None:
        await self._run(
            "MATCH (d:Document {id: $id}) SET d.ingested = true, "
            "d.pdf_type = coalesce($pdf_type, d.pdf_type)",
            id=document_id,
            pdf_type=pdf_type,
        )

    # ----------------------------------------------------------- blueprints
    # Durable storage for ArchiGen-generated floor plans (see
    # planlint/api/blueprints.py). The in-memory `app.core.store.PlanStore`
    # remains a fast per-process cache the ArchiGen engine reads/writes
    # directly; this is the cross-restart source of truth, keyed by the
    # same `generation_id` so the two stay interchangeable.

    async def create_blueprint(
        self,
        blueprint_id: str,
        project_id: str,
        floor_plan_json: str,
        generator_model: str,
        total_area_sqm: float,
        compliance_json: str | None = None,
        user_id: str | None = None,
    ) -> None:
        await self._run(
            "MATCH (p:Project {id: $project_id}) "
            "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
            "CREATE (p)-[:HAS_BLUEPRINT]->(:Blueprint {id: $id, project_id: $project_id, "
            "floor_plan: $floor_plan, generator_model: $generator_model, "
            "total_area_sqm: $total_area_sqm, compliance: $compliance, "
            "created_at: datetime()})",
            project_id=project_id,
            id=blueprint_id,
            floor_plan=floor_plan_json,
            generator_model=generator_model,
            total_area_sqm=total_area_sqm,
            compliance=compliance_json,
            uid=user_id,
        )

    async def get_blueprint(self, blueprint_id: str, user_id: str | None = None) -> dict | None:
        rows = await self._run(
            "MATCH (p:Project)-[:HAS_BLUEPRINT]->(b:Blueprint {id: $id}) "
            "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
            "RETURN b {.id, .project_id, .generator_model, .total_area_sqm, "
            ".floor_plan, .compliance, created_at: toString(b.created_at)} AS b, "
            "p.user_id AS owner_id",
            id=blueprint_id,
            uid=user_id,
        )
        return rows[0]["b"] if rows else None

    async def list_blueprints(self, project_id: str, user_id: str | None = None) -> list[dict]:
        """Summaries only (no floor_plan payload) — full history for a project."""
        return [
            row["b"]
            for row in await self._run(
                "MATCH (p:Project {id: $pid})-[:HAS_BLUEPRINT]->(b:Blueprint) "
                "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
                "RETURN b {.id, .generator_model, .total_area_sqm, "
                "created_at: toString(b.created_at)} AS b "
                "ORDER BY b.created_at DESC",
                pid=project_id,
                uid=user_id,
            )
        ]

    async def delete_blueprint(
        self, blueprint_id: str, project_id: str | None = None, user_id: str | None = None
    ) -> bool:
        rows = await self._run(
            "MATCH (p:Project)-[:HAS_BLUEPRINT]->(b:Blueprint {id: $id}) "
            "WHERE ($pid IS NULL OR p.id = $pid) AND ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
            "DETACH DELETE b RETURN count(b) AS cnt",
            id=blueprint_id,
            pid=project_id,
            uid=user_id,
        )
        return bool(rows and rows[0]["cnt"])

    # -------------------------------------------------------------- sheets

    async def create_sheet(
        self,
        sheet_id: str,
        document_id: str,
        page_number: int,
        width: float,
        height: float,
        scale_text: str | None,
        scale_in_per_point: float | None,
        sheet_number: str | None = None,
        title: str | None = None,
    ) -> None:
        await self._run(
            "MATCH (d:Document {id: $document_id}) "
            "MERGE (d)-[:HAS_SHEET]->(s:Sheet {id: $id}) "
            "SET s.page_number = $page_number, s.width = $width, s.height = $height, "
            "s.scale_text = $scale_text, s.scale_in_per_point = $scale_in_per_point, "
            "s.sheet_number = $sheet_number, s.title = $title, "
            "s.project_id = d.project_id",
            document_id=document_id,
            id=sheet_id,
            page_number=page_number,
            width=width,
            height=height,
            scale_text=scale_text,
            scale_in_per_point=scale_in_per_point,
            sheet_number=sheet_number,
            title=title,
        )

    async def set_sheet_scale(
        self, sheet_id: str, scale_text: str, scale_in_per_point: float
    ) -> None:
        await self._run(
            "MATCH (s:Sheet {id: $id}) "
            "SET s.scale_text = $scale_text, s.scale_in_per_point = $scale_in_per_point",
            id=sheet_id,
            scale_text=scale_text,
            scale_in_per_point=scale_in_per_point,
        )

    async def get_sheet(self, sheet_id: str) -> dict | None:
        rows = await self._run("MATCH (s:Sheet {id: $id}) RETURN s { .* } AS s", id=sheet_id)
        return rows[0]["s"] if rows else None

    # -------------------------------------------------------------- assets

    async def upsert_assets(self, sheet_id: str, assets: list[PhysicalAsset]) -> None:
        await self._run(
            "MATCH (s:Sheet {id: $sheet_id}) "
            "UNWIND $assets AS a "
            "MERGE (s)-[:CONTAINS]->(n:PhysicalAsset {id: a.id}) "
            "SET n.type = a.type, n.label = a.label, n.bbox = a.bbox, "
            "n.confidence = a.confidence, n.source = a.source, "
            "n.measurements = a.measurements, n.project_id = s.project_id",
            sheet_id=sheet_id,
            assets=[
                {
                    "id": asset.id,
                    "type": asset.type.value,
                    "label": asset.label,
                    "bbox": list(asset.bbox),
                    "confidence": asset.confidence,
                    "source": asset.source,
                    "measurements": json.dumps(
                        {k.value: v for k, v in asset.measurements.items()}
                    ),
                }
                for asset in assets
            ],
        )

    async def update_asset_measurements(
        self,
        asset_id: str,
        measurements: dict,
        source: str,
        confidence: float,
    ) -> None:
        """Overwrite an asset's measurements/provenance with a merged set (the
        caller merges locally, then writes the whole map) — used when a dimension
        harvested from a referenced detail/section sheet enriches the asset."""
        await self._run(
            "MATCH (a:PhysicalAsset {id: $id}) "
            "SET a.measurements = $m, a.source = $source, a.confidence = $confidence",
            id=asset_id,
            m=json.dumps(
                {getattr(k, "value", k): v for k, v in measurements.items()}
            ),
            source=source,
            confidence=confidence,
        )

    async def get_assets(self, project_id: str) -> list[dict]:
        rows = await self._run(
            "MATCH (s:Sheet {project_id: $pid})-[:CONTAINS]->(a:PhysicalAsset) "
            "RETURN a { .* } AS asset, s.id AS sheet_id",
            pid=project_id,
        )
        out = []
        for row in rows:
            asset = row["asset"]
            asset["measurements"] = json.loads(asset.get("measurements") or "{}")
            asset["sheet_id"] = row["sheet_id"]
            out.append(asset)
        return out

    async def save_details(self, document_id: str, details: list[Detail]) -> None:
        """Create the `Detail` nodes (a specific detail on a sheet) with the content
        harvested from their region, and the `DETAILED_BY` edge from the referring
        asset. MERGEd on (document, sheet_number, number) so re-runs are idempotent."""
        await self._run(
            "MATCH (d:Document {id: $document_id}) "
            "UNWIND $details AS dt "
            "MATCH (d)-[:HAS_SHEET]->(s:Sheet {sheet_number: dt.sheet_number}) "
            "MERGE (s)-[:HAS_DETAIL]->(n:Detail {document_id: $document_id, "
            "sheet_number: dt.sheet_number, number: dt.number}) "
            "SET n.id = dt.id, n.title = dt.title, n.bbox = dt.bbox, n.kind = dt.kind, "
            "n.measurements = dt.measurements, n.notes = dt.notes, n.project_id = d.project_id "
            "WITH n, dt WHERE dt.source_asset_id IS NOT NULL "
            "MATCH (a:PhysicalAsset {id: dt.source_asset_id}) "
            "MERGE (a)-[r:DETAILED_BY]->(n) SET r.kind = dt.kind",
            document_id=document_id,
            details=[
                {
                    "id": dt.id,
                    "sheet_number": dt.sheet_number,
                    "number": dt.number,
                    "title": dt.title,
                    "bbox": list(dt.bbox),
                    "kind": dt.kind,
                    "measurements": json.dumps(
                        {k.value: v for k, v in dt.measurements.items()}
                    ),
                    "notes": dt.notes,
                    "source_asset_id": dt.source_asset_id,
                }
                for dt in details
            ],
        )

    # ---------------------------------------------------------- references

    async def save_references(
        self, document_id: str, references: list[SheetReference]
    ) -> None:
        """Link each grounded, asset-bound reference to its target sheet (matched
        by sheet_number within the document). A reference whose target sheet isn't
        in the document, or which points at the asset's own sheet, is skipped. The
        edge is a plain relationship, so DETACH DELETE of either end removes it."""
        await self._run(
            "UNWIND $refs AS ref "
            "MATCH (a:PhysicalAsset {id: ref.source_asset_id}) "
            "MATCH (:Document {id: $document_id})-[:HAS_SHEET]->"
            "(target:Sheet {sheet_number: ref.target}) "
            "MATCH (src:Sheet)-[:CONTAINS]->(a) WHERE target <> src "
            "MERGE (a)-[r:REFERENCES {detail_num: ref.detail_num, "
            "target_sheet: ref.target}]->(target) "
            "SET r.kind = ref.kind, r.confidence = ref.confidence",
            document_id=document_id,
            refs=[
                {
                    "source_asset_id": r.source_asset_id,
                    "target": r.target_sheet_number,
                    "detail_num": r.detail_num,
                    "kind": r.kind,
                    "confidence": r.confidence,
                }
                for r in references
                if r.source_asset_id
            ],
        )

    async def save_specs(
        self,
        document_id: str,
        spec_index: dict[str, Spec],
        links: list[tuple[str, str]],
    ) -> None:
        """Create the fixture/finish `Spec` nodes that assets actually reference and
        the `SPECIFIED_BY` edges. Specs are MERGEd on (document, code) so re-runs are
        idempotent; the edge is plain, so DETACH DELETE of either end cleans it up."""
        pairs = sorted({(a, c) for a, c in links})
        codes = {c for _, c in pairs}
        specs = [spec_index[c] for c in codes if c in spec_index]
        await self._run(
            "MATCH (d:Document {id: $document_id}) "
            "UNWIND $specs AS s "
            "MERGE (d)-[:HAS_SPEC]->(sp:Spec {document_id: $document_id, code: s.code}) "
            "SET sp.id = s.id, sp.category = s.category, sp.description = s.description, "
            "sp.project_id = d.project_id",
            document_id=document_id,
            specs=[
                {"id": s.id, "code": s.code, "category": s.category, "description": s.description}
                for s in specs
            ],
        )
        await self._run(
            "UNWIND $pairs AS p "
            "MATCH (a:PhysicalAsset {id: p.asset_id}) "
            "MATCH (:Document {id: $document_id})-[:HAS_SPEC]->"
            "(sp:Spec {document_id: $document_id, code: p.code}) "
            "MERGE (a)-[:SPECIFIED_BY]->(sp)",
            document_id=document_id,
            pairs=[{"asset_id": a, "code": c} for a, c in pairs],
        )

    # ------------------------------------------------------------- clauses

    async def upsert_clauses(
        self,
        document_id: str,
        clauses: list[RegulationClause],
        embeddings: list[list[float]],
    ) -> None:
        assert len(clauses) == len(embeddings)
        await self._run(
            "MATCH (d:Document {id: $document_id}) "
            "UNWIND $clauses AS c "
            "MERGE (d)-[:HAS_CLAUSE]->(r:Regulation {id: c.id}) "
            "SET r.clause_id = c.clause_id, r.title = c.title, "
            "r.hierarchy_path = c.hierarchy_path, r.text = c.text, r.page = c.page, "
            "r.bbox = c.bbox, r.document_id = $document_id, r.project_id = d.project_id "
            "WITH r, c CALL db.create.setNodeVectorProperty(r, 'embedding', c.embedding) "
            "RETURN count(r)",
            document_id=document_id,
            clauses=[
                {
                    "id": clause.id,
                    "clause_id": clause.clause_id,
                    "title": clause.title,
                    "hierarchy_path": clause.hierarchy_path,
                    "text": clause.text,
                    "page": clause.page,
                    "bbox": list(clause.bbox) if clause.bbox else None,
                    "embedding": embedding,
                }
                for clause, embedding in zip(clauses, embeddings)
            ],
        )
        # Second pass: PARENT_OF edges via (document_id, clause_id) lookups.
        await self._run(
            "UNWIND $links AS link "
            "MATCH (parent:Regulation {document_id: $document_id, clause_id: link.parent}) "
            "MATCH (child:Regulation {document_id: $document_id, clause_id: link.child}) "
            "MERGE (parent)-[:PARENT_OF]->(child)",
            document_id=document_id,
            links=[
                {"parent": clause.parent_clause_id, "child": clause.clause_id}
                for clause in clauses
                if clause.parent_clause_id
            ],
        )

    async def get_clauses(self, project_id: str) -> list[dict]:
        return [
            row["clause"]
            for row in await self._run(
                "MATCH (r:Regulation {project_id: $pid}) "
                "RETURN r {.id, .clause_id, .title, .hierarchy_path, .text, .page, .bbox, "
                ".document_id} AS clause ORDER BY r.clause_id",
                pid=project_id,
            )
        ]

    async def vector_search(
        self, project_id: str, embedding: list[float], k: int | None = None
    ) -> list[dict]:
        k = k or settings.retrieval_top_k
        # Over-fetch, then scope to this project's codebooks.
        rows = await self._run(
            "CALL db.index.vector.queryNodes('regulation_embedding', $overfetch, $embedding) "
            "YIELD node, score "
            "WHERE node.project_id = $pid "
            "RETURN node {.id, .clause_id, .title, .hierarchy_path, .text, .page, .bbox, "
            ".document_id} AS clause, score "
            "ORDER BY score DESC LIMIT $k",
            overfetch=k * 5,
            embedding=embedding,
            pid=project_id,
            k=k,
        )
        return rows  # [{clause: {...}, score: float}]

    async def ancestors(self, regulation_id: str) -> list[dict]:
        """Full ancestral chain, root first (Chapter → Section → …)."""
        return [
            row["clause"]
            for row in await self._run(
                "MATCH path = (a:Regulation)-[:PARENT_OF*1..6]->(m:Regulation {id: $id}) "
                "RETURN a {.id, .clause_id, .title, .text} AS clause, length(path) AS depth "
                "ORDER BY depth DESC",
                id=regulation_id,
            )
        ]

    # ---------------------------------------------------------- constraints

    async def get_constraints(self, regulation_id: str) -> list[Constraint]:
        rows = await self._run(
            "MATCH (:Regulation {id: $id})-[:DEFINES]->(c:Constraint) RETURN c { .* } AS c",
            id=regulation_id,
        )
        return [Constraint.model_validate(row["c"]) for row in rows]

    async def save_constraints(self, constraints: list[Constraint]) -> None:
        await self._run(
            "UNWIND $constraints AS c "
            "MATCH (r:Regulation {id: c.regulation_id}) "
            "MERGE (r)-[:DEFINES]->(n:Constraint {id: c.id}) "
            "SET n += c",
            constraints=[
                {
                    **constraint.model_dump(mode="json"),
                }
                for constraint in constraints
            ],
        )

    async def mark_constraints_extracted(self, regulation_id: str) -> None:
        await self._run(
            "MATCH (r:Regulation {id: $id}) SET r.constraints_extracted = true", id=regulation_id
        )

    async def constraints_extracted(self, regulation_id: str) -> bool:
        rows = await self._run(
            "MATCH (r:Regulation {id: $id}) RETURN coalesce(r.constraints_extracted, false) AS x",
            id=regulation_id,
        )
        return bool(rows and rows[0]["x"])

    # ------------------------------------------------------------- verdicts

    async def write_verdict(
        self, asset_id: str, regulation_id: str, run_id: str, result: CheckResult
    ) -> None:
        rel = VerdictType(result.verdict).value  # closed enum -> safe to interpolate
        await self._run(
            f"MATCH (a:PhysicalAsset {{id: $asset_id}}), (r:Regulation {{id: $regulation_id}}) "
            f"MERGE (a)-[v:{rel} {{run_id: $run_id}}]->(r) "
            f"SET v.checked_at = datetime(), v.measured = $measured, "
            f"v.required = $required, v.reason = $reason",
            asset_id=asset_id,
            regulation_id=regulation_id,
            run_id=run_id,
            measured=result.measured,
            required=result.required,
            reason=result.reason,
        )

    # -------------------------------------------------------------- results

    async def latest_run_id(self, project_id: str) -> str | None:
        rows = await self._run(
            "MATCH (a:PhysicalAsset {project_id: $pid})-[v]->(:Regulation) "
            "WHERE v.run_id IS NOT NULL "
            "RETURN v.run_id AS run_id, max(v.checked_at) AS latest "
            "ORDER BY latest DESC LIMIT 1",
            pid=project_id,
        )
        return rows[0]["run_id"] if rows else None

    async def list_runs(self, project_id: str, user_id: str | None = None) -> list[dict]:
        """Return all compliance audit runs for a project with summary stats."""
        if user_id:
            proj = await self.get_project(project_id, user_id=user_id)
            if not proj:
                return []
        return await self._run(
            "MATCH (a:PhysicalAsset {project_id: $pid})-[v]->(:Regulation) "
            "WHERE v.run_id IS NOT NULL "
            "WITH v.run_id AS run_id, "
            "     toString(max(v.checked_at)) AS checked_at, "
            "     count(v) AS verdict_count, "
            "     count(CASE WHEN type(v) = 'COMPLIES_WITH' THEN 1 END) AS complies_count, "
            "     count(CASE WHEN type(v) = 'VIOLATES' THEN 1 END) AS violates_count, "
            "     count(CASE WHEN type(v) = 'NEEDS_REVIEW' THEN 1 END) AS review_count "
            "RETURN run_id, checked_at, verdict_count, complies_count, violates_count, review_count "
            "ORDER BY checked_at DESC",
            pid=project_id,
        )

    async def results_payload(
        self, project_id: str, run_id: str | None = None, user_id: str | None = None
    ) -> dict:
        """Everything the dual-pane UI needs, in one shape. Defaults to the
        most recent run's verdicts (prior runs stay in the graph as audit
        history, reachable by passing an explicit run_id)."""
        projects = await self._run(
            "MATCH (p:Project {id: $pid}) "
            "WHERE ($uid IS NULL OR p.user_id = $uid OR p.user_id IS NULL) "
            "RETURN p {.id, .name} AS p",
            pid=project_id,
            uid=user_id,
        )
        if not projects:
            return {}
        if run_id is None:
            run_id = await self.latest_run_id(project_id)
        documents = await self.get_documents(project_id, user_id=user_id)

        sheet_rows = await self._run(
            "MATCH (d:Document {project_id: $pid})-[:HAS_SHEET]->(s:Sheet) "
            "RETURN d.id AS document_id, s { .* } AS sheet ORDER BY s.page_number",
            pid=project_id,
        )
        verdict_filter = "WHERE $run_id IS NULL OR v.run_id = $run_id"
        asset_rows = await self._run(
            "MATCH (s:Sheet {project_id: $pid})-[:CONTAINS]->(a:PhysicalAsset) "
            "OPTIONAL MATCH (a)-[v]->(r:Regulation) "
            f"{verdict_filter} "
            "WITH s, a, collect(CASE WHEN v IS NULL THEN null ELSE {"
            "verdict: type(v), run_id: v.run_id, measured: v.measured, "
            "required: v.required, reason: v.reason, regulation_id: r.id, "
            "clause_id: r.clause_id, clause_page: r.page, clause_bbox: r.bbox, "
            "clause_document_id: r.document_id} END) AS verdicts "
            "RETURN s.id AS sheet_id, a { .* } AS asset, "
            "[x IN verdicts WHERE x IS NOT NULL] AS verdicts, "
            "[(a)-[ref:REFERENCES]->(t:Sheet) | {kind: ref.kind, "
            "detail_num: ref.detail_num, target_sheet_number: t.sheet_number, "
            "target_sheet_id: t.id, confidence: ref.confidence}] AS references, "
            "[(a)-[:SPECIFIED_BY]->(sp:Spec) | {code: sp.code, "
            "category: sp.category, description: sp.description}] AS specs, "
            "[(a)-[:DETAILED_BY]->(dt:Detail) | {sheet_number: dt.sheet_number, "
            "number: dt.number, title: dt.title, bbox: dt.bbox, kind: dt.kind, "
            "measurements: dt.measurements, notes: dt.notes, "
            "target_sheet_id: head([(sx:Sheet)-[:HAS_DETAIL]->(dt) | sx.id])}] AS details",
            pid=project_id,
            run_id=run_id,
        )
        assets_by_sheet: dict[str, list[dict]] = {}
        for row in asset_rows:
            asset = row["asset"]
            asset["measurements"] = json.loads(asset.get("measurements") or "{}")
            asset.pop("embedding", None)
            asset["verdicts"] = row["verdicts"]
            asset["references"] = row["references"]
            asset["specs"] = row["specs"]
            details = row["details"]
            for dt in details:
                dt["measurements"] = json.loads(dt.get("measurements") or "{}")
            asset["details"] = details
            assets_by_sheet.setdefault(row["sheet_id"], []).append(asset)

        sheets = []
        for row in sheet_rows:
            sheet = row["sheet"]
            sheet["document_id"] = row["document_id"]
            sheet["assets"] = assets_by_sheet.get(sheet["id"], [])
            sheets.append(sheet)

        return {
            "project": projects[0]["p"],
            "documents": documents,
            "sheets": sheets,
            "clauses": await self.get_clauses(project_id),
        }
