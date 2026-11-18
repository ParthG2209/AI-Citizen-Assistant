"""Deterministic in-memory traversal of the same ``GraphSpec`` that is seeded into Neo4j.

Used when Neo4j is unreachable. Method names, arguments and return types match
``queries.Neo4jQueries`` exactly; parity is checked by the integration tests.
Citizen evaluations recorded here live only in process memory.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from ai_modules.rule_engine.result import SchemeEvaluation

from .graph_builder import MP_STATE_ID, GraphSpec, condition_key
from .models import (
    EDGE_TYPES,
    RELATION_TYPES,
    CitizenEvaluationRef,
    DocumentUnlock,
    DocumentUsage,
    GraphCounts,
    RegionRef,
    SchemeEdge,
    group_document_unlocks,
    sort_edges,
    validate_citizen_id,
)


class InMemoryGraph:
    def __init__(self, spec: GraphSpec) -> None:
        self.spec = spec
        self._nodes: dict[str, dict[str, dict[str, Any]]] = {}
        for group, rows in spec.nodes.items():
            label = group.split("_")[0]
            self._nodes.setdefault(label, {}).update({row.key: row.props for row in rows})
        self._out: dict[str, dict[str, list[str]]] = {rel: {} for rel in EDGE_TYPES}
        for rel, edge_rows in spec.edges.items():
            for edge in edge_rows:
                self._out[rel].setdefault(edge.start, []).append(edge.end)
        self._citizens: dict[str, dict[str, CitizenEvaluationRef]] = {}

    def complementary_schemes(self, scheme_ids: Iterable[str]) -> list[SchemeEdge]:
        ids = set(scheme_ids)
        return sort_edges(
            SchemeEdge(e.start, e.end, rel, e.props["reason"], e.props.get("source_url"))
            for rel in RELATION_TYPES
            for e in self.spec.edges.get(rel, ())
            if e.start in ids or e.end in ids
        )

    def documents_unlocking(self, missing: Iterable[tuple[str, str]]) -> list[DocumentUnlock]:
        conditions = self._nodes["Condition"]
        documents = self._nodes["DocumentType"]
        rows = []
        for scheme_id, condition_id in missing:
            for leaf_key in self._leaves(condition_key(scheme_id, condition_id)):
                leaf = conditions[leaf_key]
                for doc_id in self._out["RESOLVED_BY"].get(leaf_key, []):
                    rows.append(
                        {
                            "doc_id": doc_id,
                            "label": documents[doc_id]["label"],
                            "ocr_supported": documents[doc_id]["ocr_supported"],
                            "scheme_id": leaf["scheme_id"],
                            "condition_id": leaf["condition_id"],
                            "field": leaf["field"],
                        }
                    )
        return group_document_unlocks(rows)

    def _leaves(self, key: str) -> list[str]:
        """``key`` itself plus every condition reachable through HAS_PART (Cypher ``*0..``)."""
        if key not in self._nodes["Condition"]:
            return []
        found, stack = [], [key]
        while stack:
            current = stack.pop()
            found.append(current)
            stack.extend(self._out["HAS_PART"].get(current, []))
        return found

    def schemes_requiring_document(self, doc_id: str) -> list[DocumentUsage]:
        if doc_id not in self._nodes["DocumentType"]:
            return []
        usages = []
        for scheme_id, props in sorted(self._nodes["Scheme"].items()):
            required = doc_id in self._out["REQUIRES_DOCUMENT"].get(scheme_id, [])
            condition_ids = sorted(
                self._nodes["Condition"][key]["condition_id"]
                for key in self._out["HAS_CONDITION"].get(scheme_id, [])
                if doc_id in self._out["RESOLVED_BY"].get(key, [])
            )
            if required or condition_ids:
                usages.append(DocumentUsage(scheme_id, props["name"], required, tuple(condition_ids)))
        return usages

    def schemes_available_in(self, region_id: str) -> list[str]:
        if region_id not in self._nodes["Region"]:
            return []
        regions, current = {region_id}, region_id
        while self._out["PART_OF"].get(current):
            current = self._out["PART_OF"][current][0]
            regions.add(current)
        return sorted(
            scheme_id
            for scheme_id, targets in self._out["AVAILABLE_IN"].items()
            if regions.intersection(targets)
        )

    def scheme_regions(self, scheme_id: str) -> list[RegionRef]:
        return [self._region(rid) for rid in sorted(self._out["AVAILABLE_IN"].get(scheme_id, []))]

    def districts(self, state_id: str = MP_STATE_ID) -> list[RegionRef]:
        found = [
            self._region(rid)
            for rid, props in self._nodes["Region"].items()
            if props["kind"] == "district" and state_id in self._out["PART_OF"].get(rid, [])
        ]
        return sorted(found, key=lambda r: (r.name, r.region_id))

    def _region(self, region_id: str) -> RegionRef:
        props = self._nodes["Region"][region_id]
        return RegionRef(region_id, props["name"], props["kind"])

    def counts(self) -> GraphCounts:
        return self.spec.counts()

    def record_evaluations(self, citizen_id: str, evaluations: Sequence[SchemeEvaluation]) -> int:
        stored = self._citizens.setdefault(validate_citizen_id(citizen_id), {})
        written = 0
        for e in evaluations:
            if e.scheme_id in self._nodes["Scheme"]:
                stored[e.scheme_id] = CitizenEvaluationRef(
                    e.scheme_id, str(e.status), e.rule_version, e.rule_hash, e.input_hash, e.as_of.isoformat()
                )
                written += 1
        return written

    def citizen_evaluations(self, citizen_id: str) -> list[CitizenEvaluationRef]:
        stored = self._citizens.get(validate_citizen_id(citizen_id), {})
        return [stored[scheme_id] for scheme_id in sorted(stored)]
