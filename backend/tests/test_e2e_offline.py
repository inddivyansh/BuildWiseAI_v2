"""Golden end-to-end: real sample PDFs through the real ingestion code and
verification loop, in deterministic offline sample mode (PLANLINT_OFFLINE_SAMPLE).

Asserts the structured verdict set — the fixture the README promises."""

from pathlib import Path

import pytest
from inline_snapshot import snapshot

from planlint.config import settings
from planlint.verify.pipeline import ingest_pending_documents, run_verification

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


@pytest.fixture(autouse=True)
def offline_sample(monkeypatch):
    monkeypatch.setattr(settings, "planlint_offline_sample", True)
    # This test pins the lightweight parser: its expectations describe the
    # regex path, and it must not depend on whether docling is installed.
    monkeypatch.setattr(settings, "planlint_semantic_parser", "simple")


async def test_sample_project_end_to_end(fake_repo, fake_embedder):
    plan = SAMPLES / "sample_floorplan.pdf"
    code = SAMPLES / "ada_excerpt.pdf"
    assert plan.exists() and code.exists(), "run the generators in samples/ first"

    fake_repo.documents["doc-plan"] = {
        "id": "doc-plan", "project_id": "proj-1", "kind": "floorplan",
        "filename": plan.name, "path": str(plan), "ingested": False,
    }
    fake_repo.documents["doc-code"] = {
        "id": "doc-code", "project_id": "proj-1", "kind": "codebook",
        "filename": code.name, "path": str(code), "ingested": False,
    }

    await ingest_pending_documents("proj-1", fake_repo, fake_embedder, vision_model=None)

    # Spatial: all four doors found, snapped to vector geometry, measured.
    assets = {a["label"]: a for a in fake_repo.assets.values()}
    assert set(assets) == {'D1 36"', 'D2 30"', 'D3 32"', 'FIRE EXIT 36"'}
    assert assets['D2 30"']["source"] == "vector-snapped"
    assert assets['D2 30"']["measurements"] == {"clear_width": 30.0}
    assert assets['D1 36"']["measurements"] == {"clear_width": 36.0}

    # Semantic: clause tree with correct hierarchy.
    clauses = {c["clause_id"]: c for c in fake_repo.clauses.values()}
    assert {"403", "403.5.1", "404", "404.2", "404.2.3", "504.2", "1030.2"} <= set(clauses)
    assert clauses["404.2.3"]["parent_clause_id"] == "404.2"

    summary = await run_verification(
        "proj-1", fake_repo, fake_embedder, text_model=None, run_id="run-1"
    )
    assert summary.errors == []

    clause_id_by_node = {c["id"]: c["clause_id"] for c in fake_repo.clauses.values()}
    door_verdicts = sorted(
        (
            fake_repo.assets[v["asset_id"]]["label"],
            clause_id_by_node[v["regulation_id"]],
            v["verdict"],
            v["measured"],
        )
        for v in fake_repo.verdicts
        if clause_id_by_node[v["regulation_id"]] == "404.2.3"
    )
    assert door_verdicts == snapshot(
        [
            ('D1 36"', "404.2.3", "COMPLIES_WITH", 36.0),
            ('D2 30"', "404.2.3", "VIOLATES", 30.0),
            ('D3 32"', "404.2.3", "COMPLIES_WITH", 32.0),
        ]
    )
