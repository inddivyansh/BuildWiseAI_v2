"""LanceDB vector index for building code clause search.

Uses Google Gemini gemini-embedding-001 for embeddings (768-dim).
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

logger = structlog.get_logger()

# Gemini embedding config
_GEMINI_EMBED_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:embedContent"
_GEMINI_BATCH_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-embedding-001:batchEmbedContents"
_EMBED_DIM = 768
_BATCH_SIZE = 100  # Gemini supports up to 100 texts per batch


def _get_gemini_key() -> str:
    """Get Gemini API key from settings."""
    from app.config import settings
    return settings.gemini_api_key


def _embed_batch_gemini(texts: list[str], api_key: str) -> list[list[float]]:
    """Embed a batch of texts using Gemini batchEmbedContents API."""
    requests = [
        {
            "model": "models/gemini-embedding-001",
            "content": {"parts": [{"text": t[:2048]}]},
            "outputDimensionality": _EMBED_DIM,
        }
        for t in texts
    ]
    resp = httpx.post(
        _GEMINI_BATCH_URL,
        params={"key": api_key},
        json={"requests": requests},
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return [e["values"] for e in data["embeddings"]]


def _embed_single_gemini(text: str, api_key: str) -> list[float]:
    """Embed a single text using Gemini embedContent API."""
    resp = httpx.post(
        _GEMINI_EMBED_URL,
        params={"key": api_key},
        json={
            "content": {"parts": [{"text": text[:2048]}]},
            "outputDimensionality": _EMBED_DIM,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]["values"]


class LanceDBIndexer:
    """Build and query a vector search index for building code clauses."""

    def __init__(
        self,
        db_path: str,
        embedding_model: str = "gemini-embedding-001",
    ):
        import lancedb

        self.db = lancedb.connect(db_path)
        self._model_name = embedding_model
        self._table_name = "clauses"

    async def index_clauses(self, clauses: list[dict]) -> int:
        """Embed all clause texts and store in LanceDB.

        Each record: id, text, jurisdiction, chapter, page, source_file, vector
        """
        if not clauses:
            return 0

        api_key = _get_gemini_key()
        texts = [c.get("text", "")[:2048] for c in clauses]

        # Batch embed
        all_embeddings = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i:i + _BATCH_SIZE]
            try:
                batch_embs = _embed_batch_gemini(batch, api_key)
                all_embeddings.extend(batch_embs)
            except Exception as e:
                logger.warning("gemini_embed_batch_failed", batch=i, error=str(e))
                # Fallback: zero vectors for this batch
                for _ in batch:
                    all_embeddings.append([0.0] * _EMBED_DIM)

        records = []
        for i, clause in enumerate(clauses):
            records.append({
                "id": clause["id"],
                "text": clause.get("text", "")[:2000],
                "jurisdiction": clause.get("jurisdiction", ""),
                "chapter": clause.get("chapter", ""),
                "page": clause.get("page", 0),
                "source_file": clause.get("source_file", ""),
                "vector": all_embeddings[i],
            })

        # Create or overwrite table
        try:
            self.db.drop_table(self._table_name)
        except Exception:
            pass
        self.db.create_table(self._table_name, records)

        logger.info("lancedb_indexed", count=len(records))
        return len(records)

    async def search(
        self,
        query: str,
        top_k: int = 10,
        jurisdiction: str | None = None,
        topics: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Vector search with optional jurisdiction filter."""
        api_key = _get_gemini_key()
        try:
            query_vec = _embed_single_gemini(query, api_key)
        except Exception as e:
            logger.warning("gemini_embed_query_failed", error=str(e))
            return []

        try:
            table = self.db.open_table(self._table_name)
        except Exception:
            logger.warning("lancedb_table_not_found")
            return []

        search = table.search(query_vec).limit(top_k * 3 if jurisdiction else top_k)
        results = search.to_list()

        # Apply jurisdiction filter
        if jurisdiction:
            results = [r for r in results if r.get("jurisdiction") == jurisdiction]

        # Trim to top_k
        results = results[:top_k]

        return [
            {
                "id": r["id"],
                "text": r["text"],
                "jurisdiction": r.get("jurisdiction", ""),
                "chapter": r.get("chapter", ""),
                "page": r.get("page", 0),
                "score": r.get("_distance", 0),
            }
            for r in results
        ]

    async def get_by_ids(self, clause_ids: list[str]) -> list[dict]:
        """Direct lookup by clause IDs."""
        try:
            table = self.db.open_table(self._table_name)
        except Exception:
            return []

        results = []
        for cid in clause_ids:
            rows = table.search().where(f"id = '{cid}'").limit(1).to_list()
            if rows:
                r = rows[0]
                results.append({
                    "id": r["id"],
                    "text": r["text"],
                    "jurisdiction": r.get("jurisdiction", ""),
                    "chapter": r.get("chapter", ""),
                    "page": r.get("page", 0),
                })
        return results
