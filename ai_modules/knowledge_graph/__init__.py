"""Scheme knowledge graph: Neo4j when reachable, otherwise an identical in-memory traversal.

from ai_modules.knowledge_graph import KnowledgeGraph
kg = KnowledgeGraph.connect()           # seeds Neo4j from the current rules if out of date
kg.source                                # "neo4j" | "fallback"
kg.complementary_schemes(["pm_kisan"])  # -> list[SchemeEdge]
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from typing import Any, Literal, TypeVar

from ai_modules.rule_engine.definitions import SchemeRule
from ai_modules.rule_engine.result import SchemeEvaluation

from .fallback import InMemoryGraph
from .graph_builder import (
    MP_STATE_ID,
    District,
    GraphSpec,
    apply_schema,
    build_graph_spec,
    load_districts,
    seed_graph,
    seeded_run_id,
)
from .models import (
    RELATION_TYPES,
    CitizenEvaluationRef,
    ConditionRef,
    DocumentUnlock,
    DocumentUsage,
    GraphCounts,
    RegionRef,
    SchemeEdge,
)
from .neo4j_client import Neo4jClient, Neo4jSettings, available, unavailable_reason
from .queries import Neo4jQueries

logger = logging.getLogger(__name__)

T = TypeVar("T")

__all__ = [
    "RELATION_TYPES",
    "CitizenEvaluationRef",
    "ConditionRef",
    "District",
    "DocumentUnlock",
    "DocumentUsage",
    "GraphCounts",
    "GraphSpec",
    "InMemoryGraph",
    "KnowledgeGraph",
    "Neo4jClient",
    "Neo4jQueries",
    "Neo4jSettings",
    "RegionRef",
    "SchemeEdge",
    "apply_schema",
    "available",
    "build_graph_spec",
    "load_districts",
    "seed_graph",
    "unavailable_reason",
]


class KnowledgeGraph:
    """Facade over ``Neo4jQueries`` / ``InMemoryGraph``; ``source`` tells which one answers.

    If a Neo4j call fails at runtime (server stopped), the facade logs a warning and switches to the
    in-memory fallback for the rest of its lifetime.
    """

    def __init__(self, spec: GraphSpec, client: Neo4jClient | None = None) -> None:
        self.spec = spec
        self._fallback = InMemoryGraph(spec)
        self._client = client
        self._backend: Neo4jQueries | InMemoryGraph = Neo4jQueries(client) if client else self._fallback

    @classmethod
    def connect(
        cls,
        rules: Sequence[SchemeRule] | None = None,
        districts: Sequence[District] | None = None,
        settings: Neo4jSettings | None = None,
        use_neo4j: bool = True,
        auto_seed: bool = True,
    ) -> KnowledgeGraph:
        """Build the graph spec from ``rules`` (default: ``RuleEngine().schemes()``) and connect.

        With ``auto_seed`` the Neo4j graph is (re)seeded when its stored run id differs from the
        spec, so Neo4j and the fallback always answer from the same rule set.
        """
        if rules is None:
            from ai_modules.rule_engine import RuleEngine

            rules = RuleEngine().schemes()
        spec = build_graph_spec(rules, load_districts() if districts is None else districts)
        if not use_neo4j:
            return cls(spec)
        reason = unavailable_reason(settings)
        if reason is not None:
            logger.warning("Neo4j unavailable (%s); using in-memory knowledge graph", reason)
            return cls(spec)
        client = Neo4jClient(settings)
        try:
            if seeded_run_id(client) != spec.run_id:
                if not auto_seed:
                    client.close()
                    logger.warning("Neo4j graph is not seeded with the current rules; using fallback")
                    return cls(spec)
                seed_graph(client, spec)
        except Exception:
            client.close()
            logger.exception("Neo4j seeding failed; using in-memory knowledge graph")
            return cls(spec)
        return cls(spec, client)

    @property
    def source(self) -> Literal["neo4j", "fallback"]:
        return "fallback" if self._backend is self._fallback else "neo4j"

    def _call(self, method: Callable[[Neo4jQueries | InMemoryGraph], T]) -> T:
        if self._backend is self._fallback:
            return method(self._fallback)
        from neo4j.exceptions import ServiceUnavailable, SessionExpired

        try:
            return method(self._backend)
        except (ServiceUnavailable, SessionExpired) as exc:
            # Connection lost; query/Cypher errors are bugs and propagate.
            logger.warning("Neo4j connection lost (%s); switching to in-memory knowledge graph", exc)
            self.close()
            return method(self._fallback)

    def complementary_schemes(self, scheme_ids: Iterable[str]) -> list[SchemeEdge]:
        ids = list(scheme_ids)
        return self._call(lambda b: b.complementary_schemes(ids))

    def documents_unlocking(self, missing: Iterable[tuple[str, str]]) -> list[DocumentUnlock]:
        pairs = list(missing)
        return self._call(lambda b: b.documents_unlocking(pairs))

    def schemes_requiring_document(self, doc_id: str) -> list[DocumentUsage]:
        return self._call(lambda b: b.schemes_requiring_document(doc_id))

    def schemes_available_in(self, region_id: str) -> list[str]:
        return self._call(lambda b: b.schemes_available_in(region_id))

    def scheme_regions(self, scheme_id: str) -> list[RegionRef]:
        return self._call(lambda b: b.scheme_regions(scheme_id))

    def districts(self, state_id: str = MP_STATE_ID) -> list[RegionRef]:
        return self._call(lambda b: b.districts(state_id))

    def counts(self) -> GraphCounts:
        return self._call(lambda b: b.counts())

    def record_evaluations(self, citizen_id: str, evaluations: Sequence[SchemeEvaluation]) -> int:
        """Persist latest evaluations (Neo4j) or keep them in process memory (fallback)."""
        return self._call(lambda b: b.record_evaluations(citizen_id, evaluations))

    def citizen_evaluations(self, citizen_id: str) -> list[CitizenEvaluationRef]:
        return self._call(lambda b: b.citizen_evaluations(citizen_id))

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        self._backend = self._fallback

    def __enter__(self) -> KnowledgeGraph:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
