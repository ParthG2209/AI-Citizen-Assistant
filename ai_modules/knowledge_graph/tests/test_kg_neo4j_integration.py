"""Integration tests against a live Neo4j (NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD, defaults match
docker-compose). They seed the SHIPPED rules (the same graph scripts/load_knowledge_graph.py builds) and
leave the database in that seeded state."""

from __future__ import annotations

import os
import uuid
from datetime import date

import pytest

from ai_modules.knowledge_graph import KnowledgeGraph
from ai_modules.knowledge_graph.fallback import InMemoryGraph
from ai_modules.knowledge_graph.graph_builder import (
    GraphSpec,
    build_graph_spec,
    load_districts,
    seed_graph,
    seeded_run_id,
)
from ai_modules.knowledge_graph.neo4j_client import Neo4jClient, Neo4jSettings, unavailable_reason
from ai_modules.knowledge_graph.queries import Neo4jQueries
from ai_modules.rule_engine import RuleEngine
from ai_modules.rule_engine.definitions import ConditionGroup, SchemeRule
from ai_modules.rule_engine.fields import DOCUMENT_TYPES
from ai_modules.rule_engine.result import EligibilityStatus, SchemeEvaluation

pytestmark = pytest.mark.integration

# CI sets REQUIRE_SERVICES=1: an unreachable Neo4j then fails instead of skipping (as in backend/tests).
REQUIRE_SERVICES = os.environ.get("REQUIRE_SERVICES", "").strip().lower() in {"1", "true", "yes"}

SNAPSHOT = """
MATCH (n) WHERE n.seed_run IS NOT NULL
OPTIONAL MATCH (n)-[r]->(m) WHERE r.seed_run IS NOT NULL
RETURN labels(n) AS labels, properties(n) AS props,
       collect([type(r), properties(r), properties(m)]) AS out
"""


@pytest.fixture(scope="module")
def client():
    settings = Neo4jSettings.from_env()
    reason = unavailable_reason(settings)
    if reason:
        message = f"Neo4j not reachable at {settings.uri}: {reason}"
        if REQUIRE_SERVICES:
            pytest.fail(f"REQUIRE_SERVICES=1 but {message}", pytrace=False)
        pytest.skip(message)
    with Neo4jClient(settings) as c:
        yield c


@pytest.fixture(scope="module")
def rules() -> list[SchemeRule]:
    loaded = RuleEngine().schemes()
    assert loaded, "no shipped rules found"
    return loaded


@pytest.fixture(scope="module")
def spec(rules) -> GraphSpec:
    return build_graph_spec(rules, load_districts())


@pytest.fixture()
def seeded(client, spec) -> Neo4jQueries:
    seed_graph(client, spec)
    return Neo4jQueries(client)


def _snapshot(client: Neo4jClient) -> list:
    rows = client.run(SNAPSHOT)
    canonical = [
        (sorted(r["labels"]), sorted(r["props"].items()), sorted(repr(o) for o in r["out"])) for r in rows
    ]
    return sorted(canonical, key=repr)


def test_seed_counts_match_spec(seeded, client, spec):
    assert seeded.counts() == spec.counts()
    assert seeded_run_id(client) == spec.run_id


def test_reseeding_is_idempotent(seeded, client, spec):
    before = _snapshot(client)
    seed_graph(client, spec)
    seed_graph(client, spec)
    assert _snapshot(client) == before
    assert seeded.counts() == spec.counts()


def test_stale_scheme_is_pruned_and_restored(seeded, client, rules, spec):
    targets = {rel.scheme_id for rule in rules for rel in rule.relationships}
    removable = next(rule for rule in reversed(rules) if rule.scheme_id not in targets)
    reduced = build_graph_spec([r for r in rules if r is not removable], load_districts())
    try:
        seed_graph(client, reduced)
        assert seeded.counts() == reduced.counts()
        assert client.run("MATCH (s:Scheme {scheme_id: $id}) RETURN s", {"id": removable.scheme_id}) == []
    finally:
        seed_graph(client, spec)
    assert seeded.counts() == spec.counts()


def _all_condition_ids(rule: SchemeRule) -> list[str]:
    ids, stack = [], list(rule.conditions)
    while stack:
        item = stack.pop()
        ids.append(item.id)
        if isinstance(item, ConditionGroup):
            stack.extend(item.items)
    return sorted(ids)


def test_neo4j_and_fallback_return_identical_results(seeded, spec, rules):
    memory = InMemoryGraph(spec)
    scheme_ids = [rule.scheme_id for rule in rules]

    assert seeded.complementary_schemes(scheme_ids) == memory.complementary_schemes(scheme_ids)
    for sid in scheme_ids:
        assert seeded.complementary_schemes([sid]) == memory.complementary_schemes([sid])
        assert seeded.scheme_regions(sid) == memory.scheme_regions(sid)
        pairs = [(sid, cid) for cid in _all_condition_ids(next(r for r in rules if r.scheme_id == sid))]
        assert seeded.documents_unlocking(pairs) == memory.documents_unlocking(pairs)
    every_pair = [(r.scheme_id, cid) for r in rules for cid in _all_condition_ids(r)]
    assert seeded.documents_unlocking(every_pair) == memory.documents_unlocking(every_pair)

    for doc_id in [*DOCUMENT_TYPES, "not_a_document"]:
        assert seeded.schemes_requiring_document(doc_id) == memory.schemes_requiring_document(doc_id)
    region_ids = [row.key for group, rows in spec.nodes.items() if group.startswith("Region") for row in rows]
    for region_id in [*region_ids, "atlantis"]:
        assert seeded.schemes_available_in(region_id) == memory.schemes_available_in(region_id)
    assert seeded.districts() == memory.districts()
    assert seeded.counts() == memory.counts()


def test_citizen_evaluations_round_trip(seeded, client, rules):
    citizen = uuid.uuid4().hex
    evaluation = SchemeEvaluation(
        rules[0].scheme_id,
        rules[0].name,
        rules[0].version,
        rules[0].rule_hash,
        "verified",
        EligibilityStatus.POTENTIALLY_ELIGIBLE,
        (),
        "input-hash",
        date(2026, 11, 18),
    )
    try:
        assert seeded.record_evaluations(citizen, [evaluation]) == 1
        assert (
            seeded.record_evaluations(citizen, [evaluation]) == 1
        )  # latest only: overwritten, not duplicated
        [stored] = seeded.citizen_evaluations(citizen)
        assert (stored.scheme_id, stored.status) == (rules[0].scheme_id, "potentially_eligible")
        props = client.run("MATCH (c:Citizen {citizen_id: $id}) RETURN properties(c) AS p", {"id": citizen})
        assert props == [{"p": {"citizen_id": citizen}}]
    finally:
        client.run("MATCH (c:Citizen {citizen_id: $id}) DETACH DELETE c", {"id": citizen})


def test_facade_connects_to_neo4j(seeded, rules):
    with KnowledgeGraph.connect(rules=rules, districts=load_districts()) as kg:
        assert kg.source == "neo4j"
        ids = [r.scheme_id for r in rules]
        assert kg.complementary_schemes(ids) == InMemoryGraph(kg.spec).complementary_schemes(ids)
    assert kg.source == "fallback"  # closed facades answer from memory


def test_recommendations_are_identical_from_neo4j_and_fallback_edges(seeded, spec, rules):
    from ai_modules.recommendation import build_recommendations
    from ai_modules.rule_engine.fields import FieldValue, Provenance

    engine = RuleEngine()
    profile = {
        name: FieldValue(value, Provenance.SELF_DECLARED)
        for name, value in {"domicile_state": "Madhya Pradesh", "family_owns_cultivable_land": True}.items()
    }
    evaluations = engine.evaluate_all(profile)
    # Enrolments that anchor the shipped TOPS_UP / COMPLEMENTED_BY edges, so suggestions are produced
    # (mmvy is reachable from two anchors).
    enrolled = ["gaon_ki_beti", "pm_kisan", "post_matric_scholarship_obc"]
    ids = sorted({e.scheme_id for e in evaluations} | set(enrolled))
    from_neo4j = build_recommendations(evaluations, enrolled, seeded.complementary_schemes(ids), rules)
    from_memory = build_recommendations(
        evaluations, enrolled, InMemoryGraph(spec).complementary_schemes(ids), rules
    )
    assert from_neo4j.to_dict() == from_memory.to_dict()
    assert from_neo4j.complementary, "shipped relationships should yield at least one suggestion"
