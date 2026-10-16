"""Shared fakes: an in-memory GraphRepository twin and a deterministic
embedder, so the whole verification loop runs in CI with zero API calls
and no database."""

from __future__ import annotations

import hashlib
import math

import pytest
from pydantic_ai import models

from planlint.models import CheckResult, Constraint, PhysicalAsset, RegulationClause

models.ALLOW_MODEL_REQUESTS = False  # no test may ever hit a real LLM


class FakeEmbedder:
    """Deterministic bag-of-words embeddings (384-dim, unit norm): cosine
    similarity approximates token overlap, so retrieval ranking is
    *meaningful* in tests — 'door clear width' really lands near the
    door-width clause."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_one(t) for t in texts]

    def embed_one(self, text: str) -> list[float]:
        values = [0.0] * 384
        for token in text.lower().split():
            token = token.strip(".,;:()\"'")
            if not token:
                continue
            digest = hashlib.sha256(token.encode()).digest()
            values[int.from_bytes(digest[:4], "big") % 384] += 1.0
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [v / norm for v in values]


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


class FakeRepository:
    """In-memory twin of GraphRepository (the subset the pipeline uses)."""

    def __init__(self):
        self.documents: dict[str, dict] = {}
        self.sheets: dict[str, dict] = {}
        self.assets: dict[str, dict] = {}  # id -> row dict (incl. sheet_id)
        self.clauses: dict[str, dict] = {}  # id -> clause dict (+embedding, +project_id)
        self.parents: dict[str, str] = {}  # child clause node id -> parent node id
        self.constraints: dict[str, list[Constraint]] = {}
        self.extracted: set[str] = set()
        self.verdicts: list[dict] = []
        self.references: list[dict] = []
        self.spec_links: list[dict] = []
        self.details: list[dict] = []

    # -- documents
    async def get_documents(self, project_id: str, **kwargs) -> list[dict]:
        return [d for d in self.documents.values() if d["project_id"] == project_id]

    async def mark_ingested(self, document_id: str, pdf_type: str | None = None) -> None:
        self.documents[document_id]["ingested"] = True

    # -- sheets / assets
    async def create_sheet(self, sheet_id, document_id, page_number, width, height,
                           scale_text, scale_in_per_point, sheet_number=None, title=None) -> None:
        self.sheets[sheet_id] = {
            "id": sheet_id, "document_id": document_id, "page_number": page_number,
            "width": width, "height": height, "scale_text": scale_text,
            "scale_in_per_point": scale_in_per_point,
            "sheet_number": sheet_number, "title": title,
        }

    async def save_references(self, document_id, references) -> None:
        by_number = {
            s["sheet_number"]: s["id"]
            for s in self.sheets.values()
            if s.get("document_id") == document_id and s.get("sheet_number")
        }
        for r in references:
            if not r.source_asset_id:
                continue
            target_id = by_number.get(r.target_sheet_number)
            src_sheet = self.assets.get(r.source_asset_id, {}).get("sheet_id")
            if target_id is None or target_id == src_sheet:
                continue
            self.references.append(
                {
                    "source_asset_id": r.source_asset_id,
                    "target_sheet_id": target_id,
                    "target_sheet_number": r.target_sheet_number,
                    "kind": r.kind,
                    "detail_num": r.detail_num,
                    "confidence": r.confidence,
                }
            )

    async def upsert_assets(self, sheet_id: str, assets: list[PhysicalAsset]) -> None:
        for asset in assets:
            row = asset.model_dump(mode="json")
            row["measurements"] = {k.value: v for k, v in asset.measurements.items()}
            row["sheet_id"] = sheet_id
            self.assets[asset.id] = row

    async def save_details(self, document_id, details) -> None:
        for dt in details:
            self.details.append(
                {
                    "sheet_number": dt.sheet_number, "number": dt.number,
                    "title": dt.title, "bbox": list(dt.bbox), "kind": dt.kind,
                    "measurements": {getattr(k, "value", k): v for k, v in dt.measurements.items()},
                    "notes": dt.notes, "source_asset_id": dt.source_asset_id,
                }
            )

    async def save_specs(self, document_id, spec_index, links) -> None:
        for asset_id, code in sorted(set(links)):
            spec = spec_index.get(code)
            if spec is None:
                continue
            self.spec_links.append(
                {"asset_id": asset_id, "code": code,
                 "category": spec.category, "description": spec.description}
            )

    async def update_asset_measurements(self, asset_id, measurements, source, confidence) -> None:
        row = self.assets.get(asset_id)
        if row is None:
            return
        row["measurements"] = {getattr(k, "value", k): v for k, v in measurements.items()}
        row["source"] = source
        row["confidence"] = confidence

    async def get_assets(self, project_id: str) -> list[dict]:
        return list(self.assets.values())

    # -- clauses
    async def upsert_clauses(self, document_id, clauses: list[RegulationClause],
                             embeddings: list[list[float]]) -> None:
        project_id = self.documents[document_id]["project_id"]
        by_clause_id = {}
        for clause, embedding in zip(clauses, embeddings):
            entry = clause.model_dump(mode="json")
            entry["document_id"] = document_id
            entry["project_id"] = project_id
            entry["embedding"] = embedding
            self.clauses[clause.id] = entry
            by_clause_id[clause.clause_id] = clause.id
        for clause in clauses:
            if clause.parent_clause_id and clause.parent_clause_id in by_clause_id:
                self.parents[by_clause_id[clause.clause_id]] = by_clause_id[
                    clause.parent_clause_id
                ]

    async def vector_search(self, project_id, embedding, k=None) -> list[dict]:
        k = k or 8
        scored = [
            ({key: value for key, value in clause.items() if key != "embedding"},
             _cosine(embedding, clause["embedding"]))
            for clause in self.clauses.values()
            if clause["project_id"] == project_id
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return [{"clause": clause, "score": score} for clause, score in scored[:k]]

    async def ancestors(self, regulation_id: str) -> list[dict]:
        chain = []
        current = self.parents.get(regulation_id)
        while current is not None:
            chain.insert(0, {k: v for k, v in self.clauses[current].items() if k != "embedding"})
            current = self.parents.get(current)
        return chain

    # -- constraints
    async def get_constraints(self, regulation_id: str) -> list[Constraint]:
        return self.constraints.get(regulation_id, [])

    async def save_constraints(self, constraints: list[Constraint]) -> None:
        for constraint in constraints:
            self.constraints.setdefault(constraint.regulation_id, []).append(constraint)

    async def mark_constraints_extracted(self, regulation_id: str) -> None:
        self.extracted.add(regulation_id)

    async def constraints_extracted(self, regulation_id: str) -> bool:
        return regulation_id in self.extracted

    # -- verdicts
    async def write_verdict(self, asset_id, regulation_id, run_id, result: CheckResult) -> None:
        self.verdicts.append(
            {
                "asset_id": asset_id,
                "regulation_id": regulation_id,
                "run_id": run_id,
                "verdict": result.verdict.value,
                "measured": result.measured,
                "required": result.required,
                "reason": result.reason,
            }
        )


@pytest.fixture
def fake_repo() -> FakeRepository:
    return FakeRepository()


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()
