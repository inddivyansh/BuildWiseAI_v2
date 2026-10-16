"""Local embeddings via fastembed — no API key, no cost, deterministic."""

from __future__ import annotations

from planlint.config import settings


class Embedder:
    """Lazy singleton around a fastembed TextEmbedding model (384-dim)."""

    _model = None

    def _get_model(self):
        if Embedder._model is None:
            from fastembed import TextEmbedding  # lazy: downloads ONNX weights on first use

            Embedder._model = TextEmbedding(model_name=settings.embed_model)
        return Embedder._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        return [vector.tolist() for vector in model.embed(texts)]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]
