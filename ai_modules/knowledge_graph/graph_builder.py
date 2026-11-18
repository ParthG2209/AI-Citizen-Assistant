"""Build the knowledge graph from ``SchemeRule`` objects and seed it into Neo4j.

``build_graph_spec`` is a pure function: it turns rules + reference data into node/relationship
rows. The same spec feeds the Neo4j seeding (``seed_graph``, statements in ``cypher/seed.cypher``)
and the in-memory fallback (``fallback.InMemoryGraph``), so both always hold identical data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ai_modules.rule_engine.definitions import Condition, ConditionGroup, SchemeLevel, SchemeRule
from ai_modules.rule_engine.fields import DOCUMENT_TYPES, PROFILE_FIELDS

from .models import EDGE_TYPES, NODE_LABELS, RELATION_TYPES, GraphCounts
from .neo4j_client import Neo4jClient, load_cypher_script, load_named_statements

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DISTRICTS_PATH = REPO_ROOT / "data" / "geo" / "mp_districts.geojson"

COUNTRY_ID = "india"
COUNTRY_NAME = "India"
MP_STATE_ID = "madhya_pradesh"
MP_STATE_NAME = "Madhya Pradesh"
_STATE_ALIASES = {"mp": MP_STATE_ID}


@dataclass(frozen=True, slots=True)
class District:
    district_id: str
    name: str


@dataclass(frozen=True, slots=True)
class NodeRow:
    key: str
    props: dict[str, Any]


@dataclass(frozen=True, slots=True)
class EdgeRow:
    start: str
    end: str
    props: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class GraphSpec:
    """Deterministic graph content. ``nodes`` keys are seed groups (``Scheme``, ``Region_state``...),
    ``edges`` keys are relationship types; rows are sorted; ``run_id`` hashes everything."""

    nodes: dict[str, tuple[NodeRow, ...]]
    edges: dict[str, tuple[EdgeRow, ...]]
    run_id: str
    scheme_count: int

    def counts(self) -> GraphCounts:
        nodes: dict[str, int] = {label: 0 for label in NODE_LABELS}
        for group, rows in self.nodes.items():
            nodes[group.split("_")[0]] += len(rows)
        relationships = {rel: len(self.edges.get(rel, ())) for rel in EDGE_TYPES}
        return GraphCounts(nodes=nodes, relationships=relationships)


def region_id_for_state(state: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", state.strip().lower()).strip("_")
    return _STATE_ALIASES.get(slug, slug)


def load_districts(path: Path | None = None) -> tuple[District, ...]:
    """Read MP districts (``properties.district_id`` / ``properties.name``) from the GeoJSON.

    Returns an empty tuple (and logs a warning) when the file is absent or empty, so scheme data
    can still be seeded; district nodes then simply do not exist.
    """
    path = path or DEFAULT_DISTRICTS_PATH
    if not path.is_file() or path.stat().st_size == 0:
        logger.warning("District list %s not found or empty; seeding without district nodes", path)
        return ()
    data = json.loads(path.read_text(encoding="utf-8"))
    districts = {
        feature["properties"]["district_id"]: District(
            feature["properties"]["district_id"], feature["properties"]["name"]
        )
        for feature in data.get("features", [])
    }
    return tuple(districts[key] for key in sorted(districts))


def _iter_conditions(
    items: Iterable[Condition | ConditionGroup], parent: ConditionGroup | None = None, depth: int = 0
) -> Iterable[tuple[Condition | ConditionGroup, ConditionGroup | None, int]]:
    for item in items:
        yield item, parent, depth
        if isinstance(item, ConditionGroup):
            yield from _iter_conditions(item.items, item, depth + 1)


def condition_key(scheme_id: str, condition_id: str) -> str:
    return f"{scheme_id}:{condition_id}"


def build_graph_spec(rules: Sequence[SchemeRule], districts: Sequence[District] = ()) -> GraphSpec:
    """Translate rules and reference data into sorted node and relationship rows."""
    nodes: dict[str, list[NodeRow]] = {
        "DocumentType": [],
        "Field": [],
        "Region_country": [],
        "Region_state": [],
        "Region_district": [],
        "Scheme": [],
        "Condition": [],
        "Benefit": [],
    }
    edges: dict[str, list[EdgeRow]] = {rel: [] for rel in EDGE_TYPES}

    for doc in DOCUMENT_TYPES.values():
        nodes["DocumentType"].append(
            NodeRow(doc.id, {"label": doc.label, "ocr_supported": doc.ocr_supported})
        )
        for field_name in doc.provides_fields:
            edges["PROVIDES_FIELD"].append(EdgeRow(doc.id, field_name))
    for spec in PROFILE_FIELDS.values():
        nodes["Field"].append(
            NodeRow(spec.name, {"label": spec.label, "type": str(spec.type), "sensitive": spec.sensitive})
        )

    nodes["Region_country"].append(NodeRow(COUNTRY_ID, {"name": COUNTRY_NAME, "kind": "country"}))
    states: dict[str, str] = {MP_STATE_ID: MP_STATE_NAME}
    for rule in rules:
        if rule.level is SchemeLevel.STATE:
            if not rule.state:
                raise ValueError(f"State scheme '{rule.scheme_id}' has no 'state'")
            states.setdefault(region_id_for_state(rule.state), rule.state)
    for state_id, state_name in states.items():
        nodes["Region_state"].append(NodeRow(state_id, {"name": state_name, "kind": "state"}))
        edges["PART_OF"].append(EdgeRow(state_id, COUNTRY_ID))
    for district in districts:
        nodes["Region_district"].append(
            NodeRow(district.district_id, {"name": district.name, "kind": "district"})
        )
        edges["PART_OF"].append(EdgeRow(district.district_id, MP_STATE_ID))

    scheme_ids = {rule.scheme_id for rule in rules}
    if len(scheme_ids) != len(rules):
        raise ValueError("Duplicate scheme_id in rules")
    for rule in rules:
        _add_scheme(rule, scheme_ids, nodes, edges)

    sorted_nodes = {group: tuple(sorted(rows, key=lambda r: r.key)) for group, rows in nodes.items()}
    sorted_edges = {rel: tuple(sorted(rows, key=lambda r: (r.start, r.end))) for rel, rows in edges.items()}
    canonical = json.dumps(
        {
            "nodes": {g: [[r.key, r.props] for r in rows] for g, rows in sorted_nodes.items()},
            "edges": {t: [[r.start, r.end, r.props] for r in rows] for t, rows in sorted_edges.items()},
        },
        sort_keys=True,
        default=str,
    )
    run_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return GraphSpec(sorted_nodes, sorted_edges, run_id, len(rules))


def _add_scheme(
    rule: SchemeRule,
    scheme_ids: set[str],
    nodes: dict[str, list[NodeRow]],
    edges: dict[str, list[EdgeRow]],
) -> None:
    sid = rule.scheme_id
    nodes["Scheme"].append(
        NodeRow(
            sid,
            {
                "name": rule.name,
                "version": rule.version,
                "level": str(rule.level),
                "state": rule.state,
                "department": rule.department,
                "effective_from": rule.effective_from.isoformat(),
                "effective_to": rule.effective_to.isoformat() if rule.effective_to else None,
                "verification_status": str(rule.verification_status),
                "rule_hash": rule.rule_hash,
                "source_urls": [source.url for source in rule.sources],
            },
        )
    )

    benefit = rule.benefit
    benefit_key = f"{sid}:benefit"
    nodes["Benefit"].append(
        NodeRow(
            benefit_key,
            {
                "scheme_id": sid,
                "type": str(benefit.type),
                "description": benefit.description,
                "amount_inr": benefit.amount_inr,
                "frequency": str(benefit.frequency) if benefit.frequency else None,
                "is_cash": benefit.is_cash,
            },
        )
    )
    edges["PROVIDES"].append(EdgeRow(sid, benefit_key))

    region = COUNTRY_ID if rule.level is SchemeLevel.CENTRAL else region_id_for_state(rule.state or "")
    edges["AVAILABLE_IN"].append(EdgeRow(sid, region))

    for doc_id in dict.fromkeys(rule.required_documents):
        if doc_id not in DOCUMENT_TYPES:
            raise ValueError(f"{sid}: unknown required document '{doc_id}'")
        edges["REQUIRES_DOCUMENT"].append(EdgeRow(sid, doc_id))

    for item, parent, depth in _iter_conditions(rule.conditions):
        key = condition_key(sid, item.id)
        props: dict[str, Any] = {
            "scheme_id": sid,
            "condition_id": item.id,
            "description": item.description,
            "source_ref": item.source_ref,
            "depth": depth,
            "parent_id": parent.id if parent else None,
        }
        if isinstance(item, ConditionGroup):
            props.update(kind="group", operator=str(item.mode), field=None)
            for child in item.items:
                edges["HAS_PART"].append(EdgeRow(key, condition_key(sid, child.id)))
        else:
            if item.field not in PROFILE_FIELDS:
                raise ValueError(f"{sid}/{item.id}: unknown profile field '{item.field}'")
            props.update(
                kind="condition",
                operator=item.operator,
                field=item.field,
                value_json=json.dumps(item.value, sort_keys=True, default=str),
                min_trust=str(item.min_trust),
            )
            edges["ON_FIELD"].append(EdgeRow(key, item.field))
            for order, target in enumerate(dict.fromkeys(item.resolves_with)):
                if target in DOCUMENT_TYPES:
                    edges["RESOLVED_BY"].append(EdgeRow(key, target, {"order": order}))
        nodes["Condition"].append(NodeRow(key, props))
        edges["HAS_CONDITION"].append(EdgeRow(sid, key, {"top_level": depth == 0}))

    declared: set[tuple[str, str]] = set()
    for rel in rule.relationships:
        if rel.relation not in RELATION_TYPES:
            raise ValueError(f"{sid}: unknown relation '{rel.relation}'")
        if rel.scheme_id not in scheme_ids:
            raise ValueError(f"{sid}: relationship target '{rel.scheme_id}' has no rule")
        if (rel.relation, rel.scheme_id) in declared:
            raise ValueError(f"{sid}: duplicate relationship {rel.relation} -> {rel.scheme_id}")
        declared.add((rel.relation, rel.scheme_id))
        edges[rel.relation].append(
            EdgeRow(sid, rel.scheme_id, {"reason": rel.reason, "source_url": rel.source_url})
        )


def apply_schema(client: Neo4jClient) -> int:
    """Create constraints and indexes from ``cypher/schema.cypher``; returns statements run."""
    statements = load_cypher_script("schema.cypher")
    for statement in statements:
        client.run(statement)
    return len(statements)


def seed_graph(client: Neo4jClient, spec: GraphSpec) -> None:
    """Idempotently write ``spec`` into Neo4j in one transaction and prune stale entities."""
    apply_schema(client)
    cypher = load_named_statements("seed.cypher")
    batch: list[tuple[str, dict[str, Any]]] = []
    for group, rows in spec.nodes.items():
        if rows:
            params = {"rows": [{"key": r.key, "props": r.props} for r in rows], "run": spec.run_id}
            batch.append((cypher[f"node_{group}"], params))
    for rel, edge_rows in spec.edges.items():
        if edge_rows:
            params = {
                "rows": [{"start": r.start, "end": r.end, "props": r.props} for r in edge_rows],
                "run": spec.run_id,
            }
            batch.append((cypher[f"rel_{rel}"], params))
    batch.append((cypher["prune_relationships"], {"types": list(EDGE_TYPES), "run": spec.run_id}))
    batch.append((cypher["prune_nodes"], {"labels": list(NODE_LABELS), "run": spec.run_id}))
    batch.append((cypher["set_meta"], {"run": spec.run_id, "scheme_count": spec.scheme_count}))
    client.run_in_transaction(batch)
    logger.info("Seeded knowledge graph run %s (%d schemes)", spec.run_id[:12], spec.scheme_count)


def seeded_run_id(client: Neo4jClient) -> str | None:
    """The ``run_id`` of the spec last seeded into Neo4j, or None if never seeded."""
    rows = client.run(load_named_statements("seed.cypher")["get_meta"])
    return rows[0]["seed_run"] if rows else None
