"""Code Hunter: deterministic retrieval of the clauses that may govern an
asset. Vector search over Regulation embeddings, each hit expanded with its
full ancestral chain (Chapter → Section → sub-clause)."""

from __future__ import annotations

import asyncio

from planlint.models import PhysicalAsset


def _query_text(asset: PhysicalAsset) -> str:
    parts = [asset.type.value.replace("_", " ")]
    parts += [parameter.value.replace("_", " ") for parameter in asset.measurements]
    parts.append("building code requirement")
    return " ".join(parts)


async def hunt(
    asset: PhysicalAsset,
    project_id: str,
    repo,
    embedder,
    k: int | None = None,
) -> list[dict]:
    """Returns [{clause, score, ancestors}] ordered by relevance."""
    # ONNX inference is sync CPU-bound; keep it off the event loop.
    embedding = await asyncio.to_thread(embedder.embed_one, _query_text(asset))
    hits = await repo.vector_search(project_id, embedding, k)
    results = []
    for hit in hits:
        clause = hit["clause"]
        results.append(
            {
                "clause": clause,
                "score": hit["score"],
                "ancestors": await repo.ancestors(clause["id"]),
            }
        )
    return results
