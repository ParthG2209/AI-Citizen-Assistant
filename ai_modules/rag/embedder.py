"""Sentence-transformers embedder (multilingual by default so Hindi and English share one space).

The model is loaded lazily on first use and cached per (model, device). Embeddings are
L2-normalised float32, so inner product == cosine similarity.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from sentence_transformers import SentenceTransformer

DEFAULT_EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def configured_model_name() -> str:
    return os.getenv("RAG_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)


def configured_device() -> str:
    return os.getenv("RAG_EMBEDDING_DEVICE", "cpu")


@lru_cache(maxsize=2)
def _load_model(model_name: str, device: str) -> SentenceTransformer:
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, device=device)


class Embedder:
    def __init__(
        self, model_name: str | None = None, device: str | None = None, batch_size: int = 32
    ) -> None:
        self.model_name = model_name or configured_model_name()
        self.device = device or configured_device()
        self.batch_size = batch_size

    @property
    def model(self) -> SentenceTransformer:
        return _load_model(self.model_name, self.device)

    @property
    def dimension(self) -> int:
        dim = self.model.get_sentence_embedding_dimension()
        if dim is None:
            raise RuntimeError(f"{self.model_name} does not report an embedding dimension")
        return int(dim)

    def embed(self, texts: list[str]) -> np.ndarray:
        import numpy as np

        if not texts:
            return np.zeros((0, self.dimension), dtype=np.float32)
        vectors = self.model.encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([text])[0]


@lru_cache(maxsize=2)
def _cached_embedder(model_name: str, device: str) -> Embedder:
    return Embedder(model_name, device)


def get_embedder(model_name: str | None = None, device: str | None = None) -> Embedder:
    """Shared embedder for the configured (or given) model; the model itself loads on first use."""
    return _cached_embedder(model_name or configured_model_name(), device or configured_device())
