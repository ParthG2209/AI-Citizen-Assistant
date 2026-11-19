"""FAISS vector store (exact inner-product search over L2-normalised embeddings).

On disk (`data/vector_index/` by default):
* `index.faiss`   – serialised `faiss.IndexFlatIP`
* `chunks.json`   – chunk texts + metadata, row i == FAISS id i
* `manifest.json` – embedding model, dimension, chunk count, source document hashes

`VectorStore.load` refuses an index that was built with a different embedding model or
dimension, because similarity scores across embedding spaces are meaningless.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .chunker import Chunk

if TYPE_CHECKING:
    import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INDEX_DIR = REPO_ROOT / "data" / "vector_index"
FORMAT_VERSION = 1
INDEX_FILE = "index.faiss"
CHUNKS_FILE = "chunks.json"
MANIFEST_FILE = "manifest.json"


class IndexNotFoundError(FileNotFoundError):
    """No index has been built yet in the given directory."""


class IndexModelMismatchError(RuntimeError):
    """The saved index was built with a different embedding model or dimension."""


@dataclass(slots=True)
class IndexManifest:
    model_name: str
    dimension: int
    chunk_count: int
    documents: dict[str, str]  # doc_id -> sha256 of the source used
    built_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    metric: str = "inner_product_normalized"
    format_version: int = FORMAT_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "format_version": self.format_version,
            "model_name": self.model_name,
            "dimension": self.dimension,
            "metric": self.metric,
            "chunk_count": self.chunk_count,
            "documents": dict(sorted(self.documents.items())),
            "built_at": self.built_at,
        }


class VectorStore:
    def __init__(self, index: Any, chunks: list[Chunk], manifest: IndexManifest) -> None:
        if index.ntotal != len(chunks):
            raise ValueError(f"index has {index.ntotal} vectors but {len(chunks)} chunks were given")
        self.index = index
        self.chunks = chunks
        self.manifest = manifest

    def __len__(self) -> int:
        return len(self.chunks)

    @classmethod
    def build(
        cls,
        chunks: list[Chunk],
        vectors: np.ndarray,
        *,
        model_name: str,
        documents: dict[str, str] | None = None,
    ) -> VectorStore:
        import faiss
        import numpy as np

        vectors = np.ascontiguousarray(vectors, dtype=np.float32)
        if vectors.ndim != 2 or vectors.shape[0] != len(chunks):
            raise ValueError(f"expected {len(chunks)} vectors, got array of shape {vectors.shape}")
        index = faiss.IndexFlatIP(int(vectors.shape[1]))
        if len(chunks):
            index.add(vectors)
        manifest = IndexManifest(
            model_name=model_name,
            dimension=int(vectors.shape[1]),
            chunk_count=len(chunks),
            documents=documents or {},
        )
        return cls(index, chunks, manifest)

    def search(self, query_vector: np.ndarray, k: int) -> list[tuple[Chunk, float]]:
        """Top-k chunks by cosine similarity (vectors are normalised)."""
        import numpy as np

        if not len(self.chunks) or k <= 0:
            return []
        query = np.ascontiguousarray(query_vector.reshape(1, -1), dtype=np.float32)
        if query.shape[1] != self.manifest.dimension:
            raise IndexModelMismatchError(
                f"query vector has dimension {query.shape[1]}, index expects {self.manifest.dimension}"
            )
        scores, ids = self.index.search(query, min(k, len(self.chunks)))
        return [(self.chunks[i], float(s)) for s, i in zip(scores[0], ids[0], strict=True) if i >= 0]

    def save(self, directory: Path = DEFAULT_INDEX_DIR) -> Path:
        import faiss

        directory.mkdir(parents=True, exist_ok=True)
        # serialize_index + Python I/O avoids FAISS' C fopen with non-ASCII / spaced Windows paths.
        (directory / INDEX_FILE).write_bytes(faiss.serialize_index(self.index).tobytes())
        (directory / CHUNKS_FILE).write_text(
            json.dumps([c.to_dict() for c in self.chunks], ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (directory / MANIFEST_FILE).write_text(
            json.dumps(self.manifest.to_dict(), indent=2), encoding="utf-8"
        )
        return directory

    @classmethod
    def read_manifest(cls, directory: Path = DEFAULT_INDEX_DIR) -> IndexManifest:
        path = directory / MANIFEST_FILE
        if not path.exists():
            raise IndexNotFoundError(f"no vector index in {directory}; run scripts/build_vector_index.py")
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format_version") != FORMAT_VERSION:
            raise IndexModelMismatchError(f"unsupported index format {data.get('format_version')}")
        return IndexManifest(
            model_name=data["model_name"],
            dimension=int(data["dimension"]),
            chunk_count=int(data["chunk_count"]),
            documents=dict(data.get("documents", {})),
            built_at=data["built_at"],
            metric=data["metric"],
        )

    @classmethod
    def load(
        cls,
        directory: Path = DEFAULT_INDEX_DIR,
        *,
        expected_model: str,
        expected_dimension: int | None = None,
    ) -> VectorStore:
        import faiss
        import numpy as np

        manifest = cls.read_manifest(directory)
        if manifest.model_name != expected_model:
            raise IndexModelMismatchError(
                f"index in {directory} was built with {manifest.model_name!r}, but the configured embedding "
                f"model is {expected_model!r}; rebuild it with scripts/build_vector_index.py"
            )
        if expected_dimension is not None and manifest.dimension != expected_dimension:
            raise IndexModelMismatchError(
                f"index dimension {manifest.dimension} != embedding dimension {expected_dimension}"
            )
        raw = np.frombuffer((directory / INDEX_FILE).read_bytes(), dtype=np.uint8)
        index = faiss.deserialize_index(raw)
        if index.d != manifest.dimension:
            raise IndexModelMismatchError(f"FAISS index dimension {index.d} != manifest {manifest.dimension}")
        chunks = [
            Chunk.from_dict(d) for d in json.loads((directory / CHUNKS_FILE).read_text(encoding="utf-8"))
        ]
        return cls(index, chunks, manifest)
