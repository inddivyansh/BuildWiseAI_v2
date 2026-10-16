"""End-to-end verification loop against the in-memory repository, with a
FunctionModel extractor. Snapshots assert the structured verdict set —
never LLM prose."""

from inline_snapshot import snapshot
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from planlint.models import (
    AssetType,
    Parameter,
    PhysicalAsset,
    RegulationClause,
)
from planlint.verify.pipeline import run_verification

CLAUSE_404 = RegulationClause(
    id="reg-404",
    clause_id="404",
    title="Doors, Doorways, and Gates",
    text="404 Doors, Doorways, and Gates",
)
CLAUSE_WIDTH = RegulationClause(
    id="reg-40423",
    clause_id="404.2.3",
    title="Clear Width",
    text=(
        "404.2.3 Clear Width. Door openings shall provide a clear width of "
        "32 inches (815 mm) minimum."
    ),
    parent_clause_id="404",
)
CLAUSE_FORCE = RegulationClause(
    id="reg-40429",
    clause_id="404.2.9",
    title="Door and Gate Opening Force",
    text=(
        "404.2.9 Door and Gate Opening Force. Fire doors shall have a minimum "
        "opening force allowable by the appropriate administrative authority."
    ),
    parent_clause_id="404",
)

DOOR_OK = PhysicalAsset(
    id="asset-d1", type=AssetType.DOOR, label="D1", bbox=(0, 0, 40, 10),
    source="vector-snapped", measurements={Parameter.CLEAR_WIDTH: 36.0},
)
DOOR_BAD = PhysicalAsset(
    id="asset-d2", type=AssetType.DOOR, label="D2", bbox=(100, 0, 140, 10),
    source="vector-snapped", measurements={Parameter.CLEAR_WIDTH: 30.0},
)


def extractor_model(fail_on: str | None = None) -> FunctionModel:
    """Scripted extractor: answers based on which clause is in the prompt."""

    def model_fn(messages, info):
        prompt = str(messages)
        if fail_on and fail_on in prompt:
            raise RuntimeError(f"simulated extractor failure on {fail_on}")
        if "404.2.3" in prompt and "Clear Width" in prompt:
            args = {
                "constraints": [
                    {
                        "applies_to": "door",
                        "parameter": "clear_width",
                        "operator": "min",
                        "value": 32.0,
                        "unit": "in",
                        "extraction_confidence": 1.0,
                        "summary": "Doors need at least 32 inches of clear width.",
                    }
                ]
            }
        elif "404.2.9" in prompt:
            args = {
                "constraints": [
                    {
                        "applies_to": "door",
                        "parameter": None,
                        "operator": "qualitative",
                        "value": None,
                        "unit": None,
                        "extraction_confidence": 0.9,
                        "summary": "Opening force set by the administrative authority.",
                    }
                ]
            }
        else:
            args = {"constraints": []}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    return FunctionModel(model_fn)


async def seed(fake_repo, fake_embedder):
    fake_repo.documents["doc-plan"] = {
        "id": "doc-plan", "project_id": "proj-1", "kind": "floorplan",
        "filename": "plan.pdf", "path": "plan.pdf", "ingested": True,
    }
    fake_repo.documents["doc-code"] = {
        "id": "doc-code", "project_id": "proj-1", "kind": "codebook",
        "filename": "ada.pdf", "path": "ada.pdf", "ingested": True,
    }
    clauses = [CLAUSE_404, CLAUSE_WIDTH, CLAUSE_FORCE]
    embeddings = fake_embedder.embed([c.text for c in clauses])
    await fake_repo.upsert_clauses("doc-code", clauses, embeddings)
    await fake_repo.create_sheet("sheet-1", "doc-plan", 0, 612, 792, 'SCALE: 1/4" = 1\'-0"', 48 / 72)
    await fake_repo.upsert_assets("sheet-1", [DOOR_OK, DOOR_BAD])


def verdict_set(fake_repo) -> list[tuple]:
    labels = {"asset-d1": "D1", "asset-d2": "D2"}
    clause_ids = {cid: c["clause_id"] for cid, c in fake_repo.clauses.items()}
    return sorted(
        (
            labels[v["asset_id"]],
            clause_ids[v["regulation_id"]],
            v["verdict"],
            v["measured"],
            v["required"],
        )
        for v in fake_repo.verdicts
    )


async def test_golden_verdict_set(fake_repo, fake_embedder):
    await seed(fake_repo, fake_embedder)
    summary = await run_verification(
        "proj-1", fake_repo, fake_embedder, extractor_model(), run_id="run-1"
    )
    assert summary.errors == []
    assert verdict_set(fake_repo) == snapshot(
        [
            ("D1", "404.2.3", "COMPLIES_WITH", 36.0, ">= 32 in"),
            ("D1", "404.2.9", "NEEDS_REVIEW", None, None),
            ("D2", "404.2.3", "VIOLATES", 30.0, ">= 32 in"),
            ("D2", "404.2.9", "NEEDS_REVIEW", None, None),
        ]
    )
    assert summary.counts == {"COMPLIES_WITH": 1, "VIOLATES": 1, "NEEDS_REVIEW": 2}


async def test_constraint_cache_prevents_reextraction(fake_repo, fake_embedder):
    """Second run re-uses cached constraints — the graph is the cache."""
    await seed(fake_repo, fake_embedder)
    await run_verification("proj-1", fake_repo, fake_embedder, extractor_model(), run_id="run-1")
    constraint_count = sum(len(v) for v in fake_repo.constraints.values())

    def exploding_fn(messages, info):
        raise AssertionError("extractor must not be called again — constraints are cached")

    await run_verification(
        "proj-1", fake_repo, fake_embedder, FunctionModel(exploding_fn), run_id="run-2"
    )
    assert sum(len(v) for v in fake_repo.constraints.values()) == constraint_count
    assert any(v["run_id"] == "run-2" for v in fake_repo.verdicts)


async def test_extractor_error_isolates_per_asset(fake_repo, fake_embedder):
    """A clause whose extraction blows up doesn't kill the run; assets still
    get verdicts from the other clauses, and the error is recorded."""
    await seed(fake_repo, fake_embedder)
    summary = await run_verification(
        "proj-1", fake_repo, fake_embedder, extractor_model(fail_on="404.2.9"), run_id="run-1"
    )
    assert len(summary.errors) >= 1
    assert "simulated extractor failure" in summary.errors[0]
    # The run completed and other clauses still produced verdicts.
    verdicts = {(v["asset_id"], v["verdict"]) for v in fake_repo.verdicts}
    assert ("asset-d2", "NEEDS_REVIEW") in verdicts  # error fallback edge exists
