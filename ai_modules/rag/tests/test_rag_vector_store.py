from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from ai_modules.rag.vector_store import (
    MANIFEST_FILE,
    IndexModelMismatchError,
    IndexNotFoundError,
    VectorStore,
)

MODEL = "test/unit-vectors"


def _unit_vectors(n: int, dim: int = 8) -> np.ndarray:
    rng = np.random.default_rng(42)
    vectors = rng.normal(size=(n, dim)).astype(np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def test_build_save_load_round_trip(tmp_path: Path, fixture_chunks):
    vectors = _unit_vectors(len(fixture_chunks))
    store = VectorStore.build(
        fixture_chunks, vectors, model_name=MODEL, documents={"pm_kisan_guidelines": "ab" * 32}
    )
    store.save(tmp_path)

    loaded = VectorStore.load(tmp_path, expected_model=MODEL, expected_dimension=8)
    assert [c.chunk_id for c in loaded.chunks] == [c.chunk_id for c in fixture_chunks]
    assert loaded.chunks == fixture_chunks
    manifest = json.loads((tmp_path / MANIFEST_FILE).read_text(encoding="utf-8"))
    assert manifest["model_name"] == MODEL
    assert manifest["dimension"] == 8
    assert manifest["chunk_count"] == len(fixture_chunks)
    assert manifest["documents"] == {"pm_kisan_guidelines": "ab" * 32}

    # Exact inner-product search: each stored vector is its own nearest neighbour with cosine 1.0.
    for i in (0, len(fixture_chunks) - 1):
        (chunk, score), *_ = loaded.search(vectors[i], 3)
        assert chunk.chunk_id == fixture_chunks[i].chunk_id
        assert score == pytest.approx(1.0, abs=1e-5)


def test_load_refuses_index_built_with_another_model(tmp_path: Path, fixture_chunks):
    VectorStore.build(fixture_chunks, _unit_vectors(len(fixture_chunks)), model_name=MODEL).save(tmp_path)
    with pytest.raises(IndexModelMismatchError, match="built with"):
        VectorStore.load(tmp_path, expected_model="sentence-transformers/some-other-model")
    with pytest.raises(IndexModelMismatchError, match="dimension"):
        VectorStore.load(tmp_path, expected_model=MODEL, expected_dimension=384)


def test_query_with_wrong_dimension_is_rejected(fixture_chunks):
    store = VectorStore.build(fixture_chunks, _unit_vectors(len(fixture_chunks)), model_name=MODEL)
    with pytest.raises(IndexModelMismatchError):
        store.search(np.ones(16, dtype=np.float32), 2)


def test_missing_index_raises_not_found(tmp_path: Path):
    with pytest.raises(IndexNotFoundError):
        VectorStore.load(tmp_path, expected_model=MODEL)


def test_empty_store_round_trip_and_search(tmp_path: Path):
    store = VectorStore.build([], np.zeros((0, 8), dtype=np.float32), model_name=MODEL)
    store.save(tmp_path)
    loaded = VectorStore.load(tmp_path, expected_model=MODEL)
    assert len(loaded) == 0
    assert loaded.search(np.ones(8, dtype=np.float32), 5) == []


def test_build_rejects_vector_count_mismatch(fixture_chunks):
    with pytest.raises(ValueError):
        VectorStore.build(fixture_chunks, _unit_vectors(len(fixture_chunks) + 1), model_name=MODEL)
