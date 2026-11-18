from __future__ import annotations

from datetime import date

import pytest

from ai_modules.knowledge_graph import KnowledgeGraph
from ai_modules.knowledge_graph.fallback import InMemoryGraph
from ai_modules.knowledge_graph.graph_builder import build_graph_spec
from ai_modules.knowledge_graph.models import ConditionRef, RegionRef, SchemeEdge
from ai_modules.rule_engine.result import EligibilityStatus, SchemeEvaluation

from .synthetic_rules import DISTRICTS, URL, synthetic_rules


@pytest.fixture()
def graph() -> InMemoryGraph:
    return InMemoryGraph(build_graph_spec(synthetic_rules(), DISTRICTS))


def test_complementary_schemes_returns_edges_touching_the_ids(graph):
    assert graph.complementary_schemes(["syn_alpha"]) == [
        SchemeEdge("syn_alpha", "syn_beta", "COMPLEMENTED_BY", "Synthetic complement", URL),
        SchemeEdge("syn_beta", "syn_alpha", "TOPS_UP", "Synthetic top-up", URL),
    ]
    assert graph.complementary_schemes(["syn_gamma"]) == [
        SchemeEdge("syn_gamma", "syn_beta", "MUTUALLY_EXCLUSIVE_WITH", "Synthetic exclusion", None),
    ]
    assert graph.complementary_schemes([]) == []


def test_documents_unlocking_groups_by_document_and_expands_groups(graph):
    unlocks = graph.documents_unlocking(
        [("syn_alpha", "income"), ("syn_beta", "residence"), ("syn_gamma", "marks")]
    )
    assert [u.doc_id for u in unlocks] == ["class12_marksheet", "income_certificate", "samagra_domicile"]
    samagra = unlocks[2]
    assert samagra.ocr_supported is True
    assert samagra.conditions == (
        ConditionRef("syn_beta", "domicile", "domicile_state"),
        ConditionRef("syn_beta", "samagra", "has_samagra_id"),
    )
    assert samagra.scheme_ids == ("syn_beta",)
    assert graph.documents_unlocking([("syn_alpha", "unknown")]) == []


def test_schemes_requiring_document(graph):
    usages = graph.schemes_requiring_document("income_certificate")
    assert [(u.scheme_id, u.required, u.condition_ids) for u in usages] == [("syn_alpha", True, ("income",))]
    usages = graph.schemes_requiring_document("samagra_domicile")
    assert [(u.scheme_id, u.required, u.condition_ids) for u in usages] == [
        ("syn_beta", False, ("domicile", "samagra"))
    ]
    assert graph.schemes_requiring_document("ration_card") == []
    assert graph.schemes_requiring_document("not_a_document") == []


def test_region_queries(graph):
    assert graph.schemes_available_in("bhopal") == ["syn_alpha", "syn_beta", "syn_gamma"]
    assert graph.schemes_available_in("india") == ["syn_alpha"]
    assert graph.schemes_available_in("atlantis") == []
    assert graph.scheme_regions("syn_beta") == [RegionRef("madhya_pradesh", "Madhya Pradesh", "state")]
    assert graph.districts() == [
        RegionRef("bhopal", "Bhopal", "district"),
        RegionRef("indore", "Indore", "district"),
    ]


def _evaluation(scheme_id: str) -> SchemeEvaluation:
    return SchemeEvaluation(
        scheme_id,
        scheme_id,
        "1.0.0",
        "rh",
        "unverified",
        EligibilityStatus.ELIGIBLE,
        (),
        "ih",
        date(2026, 11, 18),
    )


def test_record_evaluations_uses_opaque_ids_only(graph):
    citizen = "c-7f3a9e2b41d0"
    assert graph.record_evaluations(citizen, [_evaluation("syn_alpha"), _evaluation("unknown")]) == 1
    [stored] = graph.citizen_evaluations(citizen)
    assert (stored.scheme_id, stored.status, stored.as_of) == ("syn_alpha", "eligible", "2026-11-18")
    for bad in ("123456789012", "1234-5678-9012", "short", "has space in it"):
        with pytest.raises(ValueError):
            graph.record_evaluations(bad, [])


def test_facade_without_neo4j_uses_fallback():
    kg = KnowledgeGraph.connect(rules=synthetic_rules(), districts=DISTRICTS, use_neo4j=False)
    assert kg.source == "fallback"
    assert kg.counts() == kg.spec.counts()
    assert kg.complementary_schemes(["syn_gamma"])[0].relation == "MUTUALLY_EXCLUSIVE_WITH"


def test_facade_falls_back_when_neo4j_is_unreachable():
    from ai_modules.knowledge_graph.neo4j_client import Neo4jSettings

    unreachable = Neo4jSettings(uri="bolt://127.0.0.1:1", connect_timeout=1.0)
    kg = KnowledgeGraph.connect(rules=synthetic_rules(), districts=DISTRICTS, settings=unreachable)
    assert kg.source == "fallback"
    assert kg.schemes_available_in("bhopal") == ["syn_alpha", "syn_beta", "syn_gamma"]


class _DroppedConnection:
    """Stands in for a Neo4jClient whose server went away after connect()."""

    def __init__(self) -> None:
        self.closed = False

    def run(self, query, parameters=None):
        from neo4j.exceptions import ServiceUnavailable

        raise ServiceUnavailable("connection refused")

    def close(self) -> None:
        self.closed = True


def test_facade_switches_to_identical_fallback_when_neo4j_drops_at_runtime():
    spec = build_graph_spec(synthetic_rules(), DISTRICTS)
    memory = InMemoryGraph(spec)
    client = _DroppedConnection()
    kg = KnowledgeGraph(spec, client)  # type: ignore[arg-type]
    assert kg.source == "neo4j"
    ids = ["syn_alpha", "syn_beta", "syn_gamma"]
    assert kg.complementary_schemes(ids) == memory.complementary_schemes(ids)
    assert kg.source == "fallback" and client.closed
    assert kg.schemes_available_in("bhopal") == memory.schemes_available_in("bhopal")
    assert kg.counts() == memory.counts()
