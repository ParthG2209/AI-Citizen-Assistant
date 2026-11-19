"""End-to-end over the SHIPPED rules with SYNTHETIC citizen profiles (no real citizen data).

Asserts invariants that must hold for any rule content, not specific scheme outcomes."""

from __future__ import annotations

import json
from datetime import date

import pytest

from ai_modules.knowledge_graph import KnowledgeGraph
from ai_modules.recommendation import build_recommendations
from ai_modules.rule_engine import FieldValue, Provenance, RuleEngine
from ai_modules.rule_engine.result import EligibilityStatus

AS_OF = date(2026, 11, 18)

SYNTHETIC_PROFILES = {
    "empty": {},
    "rural_student": {
        "age": 18,
        "gender": "female",
        "domicile_state": "Madhya Pradesh",
        "residence_type": "rural",
        "annual_family_income": 150000,
        "social_category": "obc",
        "occupation": "student",
        "enrolled_in_higher_education": True,
        "class12_percentage": 82.0,
        "class12_board": "mpbse",
        "class12_passing_year": 2026,
    },
    "farmer_family": {
        "age": 45,
        "domicile_state": "Madhya Pradesh",
        "residence_type": "rural",
        "annual_family_income": 90000,
        "occupation": "farmer",
        "family_owns_cultivable_land": True,
        "owns_pucca_house": False,
        "family_member_income_tax_payer": False,
        "family_member_government_employee": False,
        "family_member_pensioner_above_10k": False,
        "listed_in_secc_or_nfsa": True,
        "has_samagra_id": True,
    },
}


@pytest.fixture(scope="module")
def engine() -> RuleEngine:
    return RuleEngine()


@pytest.mark.parametrize("profile_name", sorted(SYNTHETIC_PROFILES))
def test_report_invariants_on_shipped_rules(engine, profile_name):
    profile = {
        k: FieldValue(v, Provenance.SELF_DECLARED) for k, v in SYNTHETIC_PROFILES[profile_name].items()
    }
    evaluations = engine.evaluate_all(profile, as_of=AS_OF)
    rules = engine.schemes(as_of=AS_OF)
    kg = KnowledgeGraph.connect(rules=rules, districts=(), use_neo4j=False)
    status = {e.scheme_id: e.status for e in evaluations}
    eligible = sorted(sid for sid, s in status.items() if s is EligibilityStatus.ELIGIBLE)
    enrolled = eligible[:1]  # pretend the citizen already receives the first eligible scheme

    complements = kg.complementary_schemes([*status, *enrolled])
    report = build_recommendations(evaluations, enrolled, complements, rules)

    assert all(status[r.scheme_id] is EligibilityStatus.ELIGIBLE for r in report.unclaimed)
    assert not {r.scheme_id for r in report.unclaimed} & set(enrolled)
    assert all(
        status[r.scheme_id] is EligibilityStatus.POTENTIALLY_ELIGIBLE for r in report.potentially_eligible
    )
    for rec in report.complementary:
        assert (rec.presentation == "available") == (status.get(rec.scheme_id) is EligibilityStatus.ELIGIBLE)
        assert rec.scheme_id not in enrolled
    held_or_counted = set(enrolled) | set(report.unclaimed_totals.counted_scheme_ids)
    for e in complements:
        if e.relation == "MUTUALLY_EXCLUSIVE_WITH":
            assert not {e.from_scheme, e.to_scheme} <= held_or_counted
    for totals in (report.unclaimed_totals, report.potential_totals):
        assert len(set(totals.counted_scheme_ids)) == len(totals.counted_scheme_ids)
        assert not set(totals.counted_scheme_ids) & set(enrolled)
    assert not set(report.unclaimed_totals.counted_scheme_ids) & set(
        report.potential_totals.counted_scheme_ids
    )
    assert [r.rank for r in report.unclaimed] == list(range(1, len(report.unclaimed) + 1))
    json.dumps(report.to_dict())
    again = build_recommendations(evaluations, enrolled, complements, rules)
    assert again.to_dict() == report.to_dict()
