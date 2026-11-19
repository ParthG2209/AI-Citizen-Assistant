"""Plain data types shared by the Neo4j queries, the in-memory fallback and the recommender.

Everything here is dependency-free so the backend can import it without the Neo4j driver.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

# Scheme -> scheme relationship types allowed in rule YAML ``relationships``.
# Direction: declared in scheme A's file as {scheme_id: B, relation: R} -> (A)-[R]->(B), read
# subject-verb-object: "A COMPLEMENTED_BY B", "A TOPS_UP B" (A is the top-up), "A PREREQUISITE_FOR B".
# MUTUALLY_EXCLUSIVE_WITH is symmetric.
RELATION_TYPES: tuple[str, ...] = (
    "COMPLEMENTED_BY",
    "TOPS_UP",
    "PREREQUISITE_FOR",
    "MUTUALLY_EXCLUSIVE_WITH",
)
MUTUALLY_EXCLUSIVE = "MUTUALLY_EXCLUSIVE_WITH"

NODE_LABELS: tuple[str, ...] = ("Scheme", "Condition", "Benefit", "DocumentType", "Field", "Region")
EDGE_TYPES: tuple[str, ...] = (
    "HAS_CONDITION",
    "HAS_PART",
    "ON_FIELD",
    "RESOLVED_BY",
    "REQUIRES_DOCUMENT",
    "PROVIDES",
    "AVAILABLE_IN",
    "PROVIDES_FIELD",
    "PART_OF",
    *RELATION_TYPES,
)


@dataclass(frozen=True, slots=True)
class SchemeEdge:
    """A scheme-to-scheme relationship exactly as declared: (from_scheme)-[relation]->(to_scheme)."""

    from_scheme: str
    to_scheme: str
    relation: str
    reason: str
    source_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ConditionRef:
    scheme_id: str
    condition_id: str
    field: str


@dataclass(frozen=True, slots=True)
class DocumentUnlock:
    """One document type and the missing scheme conditions it resolves (per rule ``resolves_with``)."""

    doc_id: str
    label: str
    ocr_supported: bool
    conditions: tuple[ConditionRef, ...]

    @property
    def scheme_ids(self) -> tuple[str, ...]:
        return tuple(sorted({c.scheme_id for c in self.conditions}))

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scheme_ids"] = list(self.scheme_ids)
        return data


@dataclass(frozen=True, slots=True)
class DocumentUsage:
    """How one scheme uses a document type: listed as required and/or resolving conditions."""

    scheme_id: str
    scheme_name: str
    required: bool
    condition_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RegionRef:
    region_id: str
    name: str
    kind: str  # country | state | district

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CitizenEvaluationRef:
    """Latest stored evaluation of one scheme for an opaque citizen id (no PII)."""

    scheme_id: str
    status: str
    rule_version: str
    rule_hash: str
    input_hash: str
    as_of: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GraphCounts:
    nodes: dict[str, int] = field(default_factory=dict)
    relationships: dict[str, int] = field(default_factory=dict)

    @property
    def total_nodes(self) -> int:
        return sum(self.nodes.values())

    @property
    def total_relationships(self) -> int:
        return sum(self.relationships.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": dict(self.nodes),
            "relationships": dict(self.relationships),
            "total_nodes": self.total_nodes,
            "total_relationships": self.total_relationships,
        }


_CITIZEN_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def validate_citizen_id(citizen_id: str) -> str:
    """Citizen nodes are keyed by an opaque id (e.g. a UUID). Purely numeric ids are rejected so an
    Aadhaar or Samagra number can never become a graph key by mistake."""
    if not _CITIZEN_ID.match(citizen_id) or citizen_id.replace("-", "").isdigit():
        raise ValueError("citizen_id must be an opaque 8-64 character token (not a numeric identity number)")
    return citizen_id


def group_document_unlocks(rows: Iterable[Mapping[str, Any]]) -> list[DocumentUnlock]:
    """Group (doc, scheme, condition) rows into one sorted ``DocumentUnlock`` per document."""
    docs: dict[str, tuple[str, bool]] = {}
    conditions: dict[str, set[ConditionRef]] = {}
    for row in rows:
        docs[row["doc_id"]] = (row["label"], bool(row["ocr_supported"]))
        conditions.setdefault(row["doc_id"], set()).add(
            ConditionRef(row["scheme_id"], row["condition_id"], row["field"])
        )
    return [
        DocumentUnlock(
            doc_id,
            docs[doc_id][0],
            docs[doc_id][1],
            tuple(sorted(conditions[doc_id], key=lambda c: (c.scheme_id, c.condition_id))),
        )
        for doc_id in sorted(docs)
    ]


def sort_edges(edges: Iterable[SchemeEdge]) -> list[SchemeEdge]:
    return sorted(set(edges), key=lambda e: (e.from_scheme, e.relation, e.to_scheme))
