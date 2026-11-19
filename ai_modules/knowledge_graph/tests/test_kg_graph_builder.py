from __future__ import annotations

import json
from dataclasses import replace

import pytest

from ai_modules.knowledge_graph.graph_builder import build_graph_spec, load_districts
from ai_modules.knowledge_graph.models import EDGE_TYPES, NODE_LABELS
from ai_modules.knowledge_graph.neo4j_client import load_cypher_script, load_named_statements
from ai_modules.rule_engine import RuleEngine
from ai_modules.rule_engine.definitions import Condition, ConditionGroup, Relationship, SchemeLevel
from ai_modules.rule_engine.fields import DOCUMENT_TYPES, PROFILE_FIELDS

from .synthetic_rules import DISTRICTS, synthetic_rules


def test_spec_counts_for_synthetic_rules():
    counts = build_graph_spec(synthetic_rules(), DISTRICTS).counts()
    provides_field = sum(len(doc.provides_fields) for doc in DOCUMENT_TYPES.values())
    assert counts.nodes == {
        "Scheme": 3,
        "Condition": 7,
        "Benefit": 3,
        "DocumentType": len(DOCUMENT_TYPES),
        "Field": len(PROFILE_FIELDS),
        "Region": 4,  # India, Madhya Pradesh, 2 districts
    }
    assert counts.relationships == {
        "HAS_CONDITION": 7,
        "HAS_PART": 2,
        "ON_FIELD": 6,
        "RESOLVED_BY": 5,  # 'has_samagra_id' in resolves_with is a field, not a document
        "REQUIRES_DOCUMENT": 3,
        "PROVIDES": 3,
        "AVAILABLE_IN": 3,
        "PROVIDES_FIELD": provides_field,
        "PART_OF": 3,
        "COMPLEMENTED_BY": 1,
        "TOPS_UP": 1,
        "PREREQUISITE_FOR": 0,
        "MUTUALLY_EXCLUSIVE_WITH": 1,
    }


def test_spec_is_deterministic_and_order_independent():
    rules = synthetic_rules()
    first = build_graph_spec(rules, DISTRICTS)
    second = build_graph_spec(list(reversed(rules)), tuple(reversed(DISTRICTS)))
    assert first == second
    assert first.run_id == second.run_id


def test_run_id_changes_when_a_rule_changes():
    rules = synthetic_rules()
    changed = [replace(rules[0], version="1.0.1"), *rules[1:]]
    assert build_graph_spec(rules).run_id != build_graph_spec(changed).run_id


def test_availability_edges_follow_scheme_level():
    spec = build_graph_spec(synthetic_rules(), DISTRICTS)
    available = {(e.start, e.end) for e in spec.edges["AVAILABLE_IN"]}
    assert available == {
        ("syn_alpha", "india"),
        ("syn_beta", "madhya_pradesh"),
        ("syn_gamma", "madhya_pradesh"),
    }


def test_relationship_target_without_rule_is_rejected():
    rules = synthetic_rules()
    bad = replace(rules[0], relationships=(Relationship("missing_scheme", "COMPLEMENTED_BY", "x", None),))
    with pytest.raises(ValueError, match="has no rule"):
        build_graph_spec([bad, *rules[1:]])


def test_unknown_relation_is_rejected():
    rules = synthetic_rules()
    bad = replace(rules[0], relationships=(Relationship("syn_beta", "RELATED_TO", "x", None),))
    with pytest.raises(ValueError, match="unknown relation"):
        build_graph_spec([bad, *rules[1:]])


def test_load_districts_reads_geojson(tmp_path):
    path = tmp_path / "districts.geojson"
    features = [
        {"type": "Feature", "properties": {"district_id": "sagar", "name": "Sagar"}, "geometry": None},
        {
            "type": "Feature",
            "properties": {"district_id": "agar_malwa", "name": "Agar Malwa"},
            "geometry": None,
        },
    ]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    districts = load_districts(path)
    assert [d.district_id for d in districts] == ["agar_malwa", "sagar"]
    assert load_districts(tmp_path / "absent.geojson") == ()


def test_cypher_files_cover_every_seed_group():
    statements = load_named_statements("seed.cypher")
    spec = build_graph_spec(synthetic_rules(), DISTRICTS)
    for group in spec.nodes:
        assert f"node_{group}" in statements
    for rel in EDGE_TYPES:
        assert f"rel_{rel}" in statements
    assert {"prune_nodes", "prune_relationships", "set_meta", "get_meta"} <= set(statements)
    schema = "\n".join(load_cypher_script("schema.cypher"))
    for label in (*NODE_LABELS, "Citizen"):
        assert f"(n:{label})" in schema


def _items(conditions):
    for item in conditions:
        yield item
        if isinstance(item, ConditionGroup):
            yield from _items(item.items)


def test_spec_counts_for_shipped_rules():
    """Counts derived independently from the shipped rule objects and the MP district file."""
    rules = RuleEngine().schemes()
    districts = load_districts()
    counts = build_graph_spec(rules, districts).counts()
    items = [item for rule in rules for item in _items(rule.conditions)]
    leaves = [item for item in items if isinstance(item, Condition)]
    groups = [item for item in items if isinstance(item, ConditionGroup)]
    states = {"madhya_pradesh"} | {
        rule.state.strip().lower().replace(" ", "_") for rule in rules if rule.level is SchemeLevel.STATE
    }
    assert counts.nodes["Scheme"] == counts.nodes["Benefit"] == len(rules)
    assert counts.nodes["Condition"] == len(items)
    assert counts.nodes["Region"] == 1 + len(states) + len(districts)
    rel = counts.relationships
    assert rel["HAS_CONDITION"] == len(items)
    assert rel["HAS_PART"] == sum(len(g.items) for g in groups)
    assert rel["ON_FIELD"] == len(leaves)
    assert rel["RESOLVED_BY"] == sum(1 for c in leaves for ref in c.resolves_with if ref in DOCUMENT_TYPES)
    assert rel["REQUIRES_DOCUMENT"] == sum(len(rule.required_documents) for rule in rules)
    assert rel["AVAILABLE_IN"] == rel["PROVIDES"] == len(rules)
    assert rel["PART_OF"] == len(states) + len(districts)
    for relation in ("COMPLEMENTED_BY", "TOPS_UP", "PREREQUISITE_FOR", "MUTUALLY_EXCLUSIVE_WITH"):
        assert rel[relation] == sum(1 for r in rules for x in r.relationships if x.relation == relation)
