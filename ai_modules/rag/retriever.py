"""Scheme-aware hybrid retrieval: dense cosine similarity (FAISS) + BM25 lexical boost.

* Scheme filter: when `scheme_ids` are given only chunks tagged with one of them are eligible
  (an explanation of PM-KISAN must cite PM-KISAN documents).
* Hybrid ranking: `score = dense + lexical_weight * bm25 / max(bm25)` so exact terms such as
  "pucca", "landholding" or "समग्र" lift passages that the embedding alone ranks lower.
* Evidence threshold: a passage only counts as evidence when its *dense* cosine similarity is at
  least `min_score`; the lexical boost can reorder passages but never makes weak evidence pass.
"""

from __future__ import annotations

import math
import os
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .chunker import Chunk
from .embedder import Embedder
from .vector_store import VectorStore

# Chosen on the real index: over 8 test questions (PMAY-G exclusions, pucca house, PM-KISAN income tax
# and documents, Gaon Ki Beti amount, PM-JAY 70+, MMVY income) 0.5 ranks a passage that answers the
# question first every time; 0.15 left 4 of them at rank 4.
DEFAULT_LEXICAL_WEIGHT = 0.5

# Calibrated on the real index with paraphrase-multilingual-MiniLM-L12-v2: off-topic questions
# ("capital of France", "bake a cake", "tell me a joke", Hindi weather question) peak at 0.24 cosine;
# relevant scheme questions in English or Hindi score 0.35-0.75. Override with RAG_MIN_SCORE.
DEFAULT_MIN_SCORE = 0.32

_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u0900-\u0963\u0966-\u097f]+")
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have i if in is it its me my of on or our so that the this to "
    "was were what when where which who why will with you your do does can how am not no any all shall "
    "के का की है में और को से पर या एक हैं भी लिए यह मैं क्या कैसे".split()
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


def configured_min_score() -> float:
    return float(os.getenv("RAG_MIN_SCORE", str(DEFAULT_MIN_SCORE)))


@dataclass(frozen=True, slots=True)
class RetrievedPassage:
    chunk: Chunk
    score: float  # hybrid ranking score
    dense_score: float  # cosine similarity between query and chunk embeddings
    lexical_score: float  # BM25 normalised to [0, 1] over the candidates
    sufficient: bool  # dense_score >= min_score


class BM25:
    """Okapi BM25 over a fixed list of documents."""

    def __init__(self, documents: Iterable[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self.term_freqs = [Counter(tokenize(doc)) for doc in documents]
        self.lengths = [sum(tf.values()) for tf in self.term_freqs]
        self.avg_length = (sum(self.lengths) / len(self.lengths)) if self.lengths else 0.0
        doc_freq: Counter[str] = Counter()
        for tf in self.term_freqs:
            doc_freq.update(tf.keys())
        n = len(self.term_freqs)
        self.idf = {term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in doc_freq.items()}

    def score(self, query: str, index: int, ignore: frozenset[str] = frozenset()) -> float:
        tf = self.term_freqs[index]
        length_norm = 1 - self.b + self.b * (self.lengths[index] / self.avg_length if self.avg_length else 0)
        total = 0.0
        for term in set(tokenize(query)) - ignore:
            freq = tf.get(term)
            if freq:
                total += self.idf[term] * freq * (self.k1 + 1) / (freq + self.k1 * length_norm)
        return total


class Retriever:
    def __init__(
        self,
        store: VectorStore,
        embedder: Embedder,
        *,
        min_score: float | None = None,
        lexical_weight: float = DEFAULT_LEXICAL_WEIGHT,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.min_score = configured_min_score() if min_score is None else min_score
        self.lexical_weight = lexical_weight
        self._bm25 = BM25(chunk.text for chunk in store.chunks)
        self._position = {chunk.chunk_id: i for i, chunk in enumerate(store.chunks)}

    def _uninformative_terms(self, query: str, positions: list[int]) -> frozenset[str]:
        """Query terms present in over a quarter of the candidate passages (typically the scheme name
        after scheme filtering) cannot tell those passages apart, so they get no lexical weight."""
        if len(positions) < 4:
            return frozenset()
        limit = len(positions) * 0.25
        return frozenset(
            term
            for term in set(tokenize(query))
            if sum(term in self._bm25.term_freqs[i] for i in positions) > limit
        )

    def retrieve(
        self,
        query: str,
        *,
        scheme_ids: Iterable[str] | None = None,
        k: int = 5,
        context: str | None = None,
    ) -> list[RetrievedPassage]:
        """`context` (e.g. the scheme name) is added to the embedded query only, not to the lexical one."""
        if not query.strip() or not len(self.store):
            return []
        wanted = set(scheme_ids or ())
        dense_query = f"{context}: {query}" if context else query
        # Exact search over every vector when filtering (corpus is small); otherwise a generous pool.
        pool = len(self.store) if wanted else max(k * 10, 50)
        hits = [
            (chunk, dense)
            for chunk, dense in self.store.search(self.embedder.embed_query(dense_query), pool)
            if not wanted or wanted.intersection(chunk.scheme_ids)
        ]
        if not hits:
            return []
        positions = [self._position[chunk.chunk_id] for chunk, _ in hits]
        # The scheme's own name ("PM-KISAN" -> pm, kisan) is not evidence once results are filtered by it.
        scheme_terms = frozenset(t for scheme_id in wanted for t in tokenize(scheme_id.replace("_", " ")))
        ignore = scheme_terms | self._uninformative_terms(query, positions)
        lexical = [self._bm25.score(query, i, ignore) for i in positions]
        top_lexical = max(lexical) or 1.0
        passages = [
            RetrievedPassage(
                chunk=chunk,
                score=dense + self.lexical_weight * (lex / top_lexical),
                dense_score=dense,
                lexical_score=lex / top_lexical,
                sufficient=dense >= self.min_score,
            )
            for (chunk, dense), lex in zip(hits, lexical, strict=True)
        ]
        passages.sort(key=lambda p: p.score, reverse=True)
        return passages[:k]

    def best_dense_score(self, query: str) -> float:
        """Highest cosine similarity of `query` against the whole corpus (0.0 for an empty index)."""
        if not query.strip() or not len(self.store):
            return 0.0
        hits = self.store.search(self.embedder.embed_query(query), 1)
        return hits[0][1] if hits else 0.0
