"""Read/write queries against the Neo4j knowledge graph.

Every query is parametrised; user input is never interpolated into Cypher text.
``fallback.InMemoryGraph`` implements the same methods with the same return types.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ai_modules.rule_engine.result import SchemeEvaluation

from .graph_builder import MP_STATE_ID, condition_key
from .models import (
    EDGE_TYPES,
    NODE_LABELS,
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
from .neo4j_client import Neo4jClient

COMPLEMENTARY_SCHEMES = """
MATCH (a:Scheme)-[r]->(b:Scheme)
WHERE type(r) IN $relations AND (a.scheme_id IN $ids OR b.scheme_id IN $ids)
RETURN a.scheme_id AS from_scheme, b.scheme_id AS to_scheme, type(r) AS relation,
       r.reason AS reason, r.source_url AS source_url
"""

DOCUMENTS_UNLOCKING = """
UNWIND $keys AS condition_key
MATCH (c:Condition {key: condition_key})-[:HAS_PART*0..]->(leaf:Condition)-[:RESOLVED_BY]->(d:DocumentType)
RETURN DISTINCT d.doc_id AS doc_id, d.label AS label, d.ocr_supported AS ocr_supported,
       leaf.scheme_id AS scheme_id, leaf.condition_id AS condition_id, leaf.field AS field
"""

SCHEMES_REQUIRING_DOCUMENT = """
MATCH (d:DocumentType {doc_id: $doc_id})
MATCH (s:Scheme)
OPTIONAL MATCH (s)-[req:REQUIRES_DOCUMENT]->(d)
OPTIONAL MATCH (s)-[:HAS_CONDITION]->(c:Condition)-[:RESOLVED_BY]->(d)
WITH s, req IS NOT NULL AS required, collect(DISTINCT c.condition_id) AS condition_ids
WHERE required OR size(condition_ids) > 0
RETURN s.scheme_id AS scheme_id, s.name AS scheme_name, required, condition_ids
ORDER BY scheme_id
"""

SCHEMES_AVAILABLE_IN = """
MATCH (:Region {region_id: $region_id})-[:PART_OF*0..]->(:Region)<-[:AVAILABLE_IN]-(s:Scheme)
RETURN DISTINCT s.scheme_id AS scheme_id
ORDER BY scheme_id
"""

SCHEME_REGIONS = """
MATCH (:Scheme {scheme_id: $scheme_id})-[:AVAILABLE_IN]->(r:Region)
RETURN r.region_id AS region_id, r.name AS name, r.kind AS kind
ORDER BY region_id
"""

DISTRICTS = """
MATCH (r:Region {kind: 'district'})-[:PART_OF]->(:Region {region_id: $state_id})
RETURN r.region_id AS region_id, r.name AS name, r.kind AS kind
ORDER BY name, region_id
"""

NODE_COUNTS = """
MATCH (n)
UNWIND labels(n) AS label
WITH label WHERE label IN $labels
RETURN label, count(*) AS count
"""

RELATIONSHIP_COUNTS = """
MATCH ()-[r]->()
WHERE type(r) IN $types
RETURN type(r) AS type, count(*) AS count
"""

RECORD_EVALUATIONS = """
MERGE (c:Citizen {citizen_id: $citizen_id})
WITH c
UNWIND $rows AS row
MATCH (s:Scheme {scheme_id: row.scheme_id})
MERGE (c)-[e:EVALUATED]->(s)
SET e.status = row.status, e.rule_version = row.rule_version, e.rule_hash = row.rule_hash,
    e.input_hash = row.input_hash, e.as_of = row.as_of
RETURN count(e) AS written
"""

CITIZEN_EVALUATIONS = """
MATCH (:Citizen {citizen_id: $citizen_id})-[e:EVALUATED]->(s:Scheme)
RETURN s.scheme_id AS scheme_id, e.status AS status, e.rule_version AS rule_version,
       e.rule_hash AS rule_hash, e.input_hash AS input_hash, e.as_of AS as_of
ORDER BY scheme_id
"""


class Neo4jQueries:
    """Knowledge-graph queries executed on a live Neo4j database."""

    def __init__(self, client: Neo4jClient) -> None:
        self.client = client

    def complementary_schemes(self, scheme_ids: Iterable[str]) -> list[SchemeEdge]:
        """All scheme-to-scheme edges (as declared) that start or end at one of ``scheme_ids``."""
        rows = self.client.run(
            COMPLEMENTARY_SCHEMES, {"ids": sorted(set(scheme_ids)), "relations": list(RELATION_TYPES)}
        )
        return sort_edges(SchemeEdge(**row) for row in rows)

    def documents_unlocking(self, missing: Iterable[tuple[str, str]]) -> list[DocumentUnlock]:
        """Document types that resolve the given (scheme_id, condition_id) pairs.

        Pass leaf condition ids; a group id expands to all of its leaf conditions.
        """
        keys = sorted({condition_key(scheme_id, condition_id) for scheme_id, condition_id in missing})
        return group_document_unlocks(self.client.run(DOCUMENTS_UNLOCKING, {"keys": keys}))

    def schemes_requiring_document(self, doc_id: str) -> list[DocumentUsage]:
        rows = self.client.run(SCHEMES_REQUIRING_DOCUMENT, {"doc_id": doc_id})
        return [
            DocumentUsage(r["scheme_id"], r["scheme_name"], r["required"], tuple(sorted(r["condition_ids"])))
            for r in rows
        ]

    def schemes_available_in(self, region_id: str) -> list[str]:
        """Scheme ids available in a region, inherited from its state and country."""
        return [r["scheme_id"] for r in self.client.run(SCHEMES_AVAILABLE_IN, {"region_id": region_id})]

    def scheme_regions(self, scheme_id: str) -> list[RegionRef]:
        return [RegionRef(**r) for r in self.client.run(SCHEME_REGIONS, {"scheme_id": scheme_id})]

    def districts(self, state_id: str = MP_STATE_ID) -> list[RegionRef]:
        return [RegionRef(**r) for r in self.client.run(DISTRICTS, {"state_id": state_id})]

    def counts(self) -> GraphCounts:
        nodes = {label: 0 for label in NODE_LABELS}
        for row in self.client.run(NODE_COUNTS, {"labels": list(NODE_LABELS)}):
            nodes[row["label"]] = row["count"]
        relationships = {rel: 0 for rel in EDGE_TYPES}
        for row in self.client.run(RELATIONSHIP_COUNTS, {"types": list(EDGE_TYPES)}):
            relationships[row["type"]] = row["count"]
        return GraphCounts(nodes, relationships)

    def record_evaluations(self, citizen_id: str, evaluations: Sequence[SchemeEvaluation]) -> int:
        """Store each scheme's latest evaluation for an opaque citizen id; returns edges written."""
        rows = [_evaluation_row(e) for e in evaluations]
        result = self.client.run(
            RECORD_EVALUATIONS, {"citizen_id": validate_citizen_id(citizen_id), "rows": rows}
        )
        return result[0]["written"] if result else 0

    def citizen_evaluations(self, citizen_id: str) -> list[CitizenEvaluationRef]:
        rows = self.client.run(CITIZEN_EVALUATIONS, {"citizen_id": validate_citizen_id(citizen_id)})
        return [CitizenEvaluationRef(**r) for r in rows]


def _evaluation_row(evaluation: SchemeEvaluation) -> dict[str, str]:
    return {
        "scheme_id": evaluation.scheme_id,
        "status": str(evaluation.status),
        "rule_version": evaluation.rule_version,
        "rule_hash": evaluation.rule_hash,
        "input_hash": evaluation.input_hash,
        "as_of": evaluation.as_of.isoformat(),
    }
