from __future__ import annotations

import json
import random

import pytest

from ai_modules.recommendation import build_recommendations
from ai_modules.recommendation.ranker import RankInput, rank
from ai_modules.rule_engine.definitions import BenefitFrequency, BenefitType, VerificationStatus

from .factories import (
    ELIGIBLE,
    NOT_ELIGIBLE,
    POTENTIAL,
    edge,
    evaluation,
    failed,
    make_rule,
    missing,
    passed,
)


def _rules(*rules):
    return {r.scheme_id: r for r in rules}


def test_complementary_target_presented_by_its_own_status():
    rules = _rules(make_rule("anchor"), make_rule("t_ok"), make_rule("t_pot"), make_rule("t_no"))
    evals = [
        evaluation("anchor", ELIGIBLE, passed("c")),
        evaluation("t_ok", ELIGIBLE, passed("c")),
        evaluation("t_pot", POTENTIAL, missing("age", "age")),
        evaluation("t_no", NOT_ELIGIBLE, failed("age")),
    ]
    edges = [edge("anchor", "COMPLEMENTED_BY", t) for t in ("t_ok", "t_pot", "t_no")]
    report = build_recommendations(evals, [], edges, rules)
    by_id = {c.scheme_id: c for c in report.complementary}
    assert by_id["t_ok"].presentation == "available"
    assert by_id["t_pot"].presentation == "check_eligibility"
    assert [m.condition_id for m in by_id["t_pot"].missing_conditions] == ["age"]
    assert by_id["t_no"].presentation == "check_eligibility"
    assert by_id["t_no"].target_status == "not_eligible"
    assert [f.condition_id for f in by_id["t_no"].failed_conditions] == ["age"]
    assert by_id["t_no"].next_actions == ()
    assert [c.scheme_id for c in report.complementary] == ["t_ok", "t_pot", "t_no"]


def test_no_suggestion_when_anchor_not_eligible():
    rules = _rules(make_rule("anchor"), make_rule("target"))
    evals = [
        evaluation("anchor", POTENTIAL, missing("age", "age")),
        evaluation("target", ELIGIBLE, passed("c")),
    ]
    report = build_recommendations(evals, [], [edge("anchor", "COMPLEMENTED_BY", "target")], rules)
    assert report.complementary == ()


def test_tops_up_points_from_top_up_to_base_and_enrolment_anchors():
    rules = _rules(make_rule("base"), make_rule("topup"))
    edges = [edge("topup", "TOPS_UP", "base")]
    evals = [
        evaluation("base", POTENTIAL, missing("age", "age")),
        evaluation("topup", POTENTIAL, missing("age", "age")),
    ]
    assert build_recommendations(evals, [], edges, rules).complementary == ()
    report = build_recommendations(evals, ["base"], edges, rules)
    [rec] = report.complementary
    assert rec.scheme_id == "topup" and rec.presentation == "check_eligibility"
    assert rec.via[0].anchor_scheme == "base" and "tops up" in rec.via[0].text
    # an enrolled target is never suggested again
    assert build_recommendations(evals, ["base", "topup"], edges, rules).complementary == ()


def test_mutual_exclusion_is_a_warning_and_not_double_counted():
    rules = _rules(make_rule("big", amount=5000.0), make_rule("small", amount=1000.0))
    evals = [evaluation("big", ELIGIBLE, passed("c")), evaluation("small", ELIGIBLE, passed("c"))]
    report = build_recommendations(evals, [], [edge("small", "MUTUALLY_EXCLUSIVE_WITH", "big")], rules)
    assert report.complementary == ()
    [warning] = report.warnings
    assert warning.scheme_ids == ("big", "small")
    totals = report.unclaimed_totals
    assert totals.recurring_annual_cash_inr == 5000.0
    assert totals.excluded_mutually_exclusive == (("small", "big"),)
    assert all(rec.warnings for rec in report.unclaimed)


def test_mutual_exclusion_with_enrolled_scheme_excludes_from_totals():
    rules = _rules(make_rule("held"), make_rule("other", amount=2000.0))
    evals = [evaluation("held", ELIGIBLE, passed("c")), evaluation("other", ELIGIBLE, passed("c"))]
    report = build_recommendations(evals, ["held"], [edge("held", "MUTUALLY_EXCLUSIVE_WITH", "other")], rules)
    assert [r.scheme_id for r in report.unclaimed] == ["other"]
    assert report.unclaimed_totals.recurring_annual_cash_inr == 0.0
    assert report.unclaimed_totals.excluded_mutually_exclusive == (("other", "held"),)


def test_totals_never_double_count_and_separate_categories():
    rules = _rules(
        make_rule("annual", amount=6000.0),
        make_rule("monthly", amount=500.0, frequency=BenefitFrequency.MONTHLY),
        make_rule("onetime", BenefitType.ONE_TIME_CASH, 25000.0, BenefitFrequency.ONE_TIME),
        make_rule("fees", BenefitType.FEE_REIMBURSEMENT, None, BenefitFrequency.ANNUAL),
        make_rule("health", BenefitType.INSURANCE_COVER, 500000.0, BenefitFrequency.ANNUAL),
        make_rule("house", BenefitType.HOUSING_ASSISTANCE, 120000.0, BenefitFrequency.ONE_TIME),
        make_rule("enrolled", amount=9999.0),
        make_rule("anchor", amount=100.0),
    )
    evals = [evaluation(sid, ELIGIBLE, passed("c")) for sid in rules]
    # 'annual' is reachable via two complementary paths and is also directly eligible
    edges = [edge("anchor", "COMPLEMENTED_BY", "annual"), edge("enrolled", "COMPLEMENTED_BY", "annual")]
    report = build_recommendations(evals, ["enrolled"], edges, rules)
    totals = report.unclaimed_totals
    assert totals.recurring_annual_cash_inr == 6100.0  # annual + anchor, enrolled excluded
    assert totals.recurring_monthly_cash_inr == 500.0
    assert totals.one_time_cash_inr == 25000.0
    assert totals.fee_reimbursement_inr == 0.0
    assert totals.amount_not_published == ("fees",)
    assert sorted(line.scheme_id for line in totals.non_cash) == ["health", "house"]
    assert len(totals.counted_scheme_ids) == len(set(totals.counted_scheme_ids)) == 7
    assert "enrolled" not in totals.counted_scheme_ids
    [comp] = report.complementary
    assert comp.scheme_id == "annual" and len(comp.via) == 2
    json.dumps(report.to_dict())  # API-serialisable


def test_potential_totals_are_separate_from_unclaimed():
    rules = _rules(make_rule("now", amount=100.0), make_rule("later", amount=300.0))
    evals = [evaluation("now", ELIGIBLE, passed("c")), evaluation("later", POTENTIAL, missing("age", "age"))]
    report = build_recommendations(evals, [], [], rules)
    assert report.unclaimed_totals.recurring_annual_cash_inr == 100.0
    assert report.potential_totals.recurring_annual_cash_inr == 300.0


def test_ranking_is_deterministic_and_explained():
    rules = [
        make_rule("cash_small", amount=1000.0),
        make_rule("cash_big", amount=6000.0),
        make_rule("cash_unknown", amount=None),
        make_rule("insurance", BenefitType.INSURANCE_COVER, 500000.0),
        make_rule("onetime", BenefitType.ONE_TIME_CASH, 25000.0, BenefitFrequency.ONE_TIME),
        make_rule("cash_big_unverified", amount=6000.0, verification=VerificationStatus.UNVERIFIED),
    ]
    inputs = [RankInput(r.scheme_id, "eligible", r.benefit, 0, str(r.verification_status)) for r in rules]
    expected = ["cash_big", "cash_big_unverified", "cash_small", "cash_unknown", "onetime", "insurance"]
    for seed in range(5):
        shuffled = inputs[:]
        random.Random(seed).shuffle(shuffled)
        ranked = rank(shuffled)
        assert [r.item.scheme_id for r in ranked] == expected
        assert [r.rank for r in ranked] == [1, 2, 3, 4, 5, 6]
    reasons = rank(inputs)[0].reasons
    assert any("cash benefits rank above non-cash" in r for r in reasons)
    assert any("INR 6,000 per year" in r for r in reasons)


def test_effort_breaks_ties_and_status_dominates():
    rule = make_rule("x")
    ranked = rank(
        [
            RankInput("b_more", "potentially_eligible", rule.benefit, 3, "verified"),
            RankInput("a_less", "potentially_eligible", rule.benefit, 1, "verified"),
            RankInput("z_elig", "eligible", rule.benefit, 0, "verified"),
        ]
    )
    assert [r.item.scheme_id for r in ranked] == ["z_elig", "a_less", "b_more"]


def test_report_is_identical_for_shuffled_inputs():
    rules = _rules(make_rule("a", amount=10.0), make_rule("b", amount=20.0), make_rule("c"))
    evals = [
        evaluation("a", ELIGIBLE, passed("c")),
        evaluation("b", ELIGIBLE, passed("c")),
        evaluation("c", POTENTIAL, missing("inc", "annual_family_income", ("income_certificate",))),
    ]
    edges = [edge("a", "COMPLEMENTED_BY", "c"), edge("b", "COMPLEMENTED_BY", "c")]
    first = build_recommendations(evals, [], edges, rules).to_dict()
    second = build_recommendations(
        list(reversed(evals)), [], list(reversed(edges)), list(rules.values())
    ).to_dict()
    assert first == second
    assert [r["scheme_id"] for r in first["unclaimed"]] == ["b", "a"]
    assert first["unclaimed"][0]["next_actions"][0]["url"] == "https://example.org/apply"


def test_monthly_fee_reimbursement_and_inconsistent_one_time_are_not_mixed_into_sums():
    rules = _rules(
        make_rule("fee_month", BenefitType.FEE_REIMBURSEMENT, 500.0, BenefitFrequency.MONTHLY),
        make_rule("fee_year", BenefitType.FEE_REIMBURSEMENT, 10000.0, BenefitFrequency.ANNUAL),
        make_rule("lump_bad", BenefitType.ONE_TIME_CASH, 1000.0, BenefitFrequency.ANNUAL),
        make_rule("lump", BenefitType.ONE_TIME_CASH, 25000.0, BenefitFrequency.ONE_TIME),
        make_rule("stipend", amount=750.0, frequency=BenefitFrequency.MONTHLY),
        make_rule("grant", amount=6000.0),
    )
    evals = [evaluation(sid, ELIGIBLE, passed("c")) for sid in rules]
    totals = build_recommendations(evals, [], [], rules).unclaimed_totals
    assert totals.fee_reimbursement_inr == 10000.0
    assert totals.one_time_cash_inr == 25000.0
    assert totals.recurring_monthly_cash_inr == 750.0  # monthly is never annualised or added to annual
    assert totals.recurring_annual_cash_inr == 6000.0
    assert {(line.scheme_id, line.category) for line in totals.not_summed} == {
        ("fee_month", "fee_reimbursement_monthly"),
        ("lump_bad", "cash_inconsistent_frequency"),
    }
    assert len(totals.counted_scheme_ids) == 6


def test_scheme_reachable_by_several_edges_is_suggested_and_counted_once():
    rules = _rules(make_rule("a1"), make_rule("a2"), make_rule("held"), make_rule("target", amount=4000.0))
    evals = [
        evaluation("a1", ELIGIBLE, passed("c")),
        evaluation("a2", ELIGIBLE, passed("c")),
        evaluation("held", ELIGIBLE, passed("c")),
        evaluation("target", ELIGIBLE, passed("c")),
    ]
    edges = [
        edge("a1", "COMPLEMENTED_BY", "target"),
        edge("a1", "COMPLEMENTED_BY", "target"),  # duplicate edge row
        edge("a2", "PREREQUISITE_FOR", "target"),
        edge("target", "TOPS_UP", "held"),
        edge("held", "COMPLEMENTED_BY", "a1"),
    ]
    report = build_recommendations(evals, ["held"], edges, rules)
    [target] = [c for c in report.complementary if c.scheme_id == "target"]
    assert target.presentation == "available"
    assert sorted((v.anchor_scheme, v.relation) for v in target.via) == [
        ("a1", "COMPLEMENTED_BY"),
        ("a2", "PREREQUISITE_FOR"),
        ("held", "TOPS_UP"),
    ]
    totals = report.unclaimed_totals
    assert totals.counted_scheme_ids == ("a1", "a2", "target")  # 'held' is enrolled
    assert totals.recurring_annual_cash_inr == 6000.0  # 1000 + 1000 + 4000, target once
    assert [r.scheme_id for r in report.unclaimed].count("target") == 1


def test_complementary_availability_follows_the_target_evaluation_only():
    rules = _rules(make_rule("held"), make_rule("pot"), make_rule("unevaluated"))
    evals = [evaluation("held", ELIGIBLE, passed("c")), evaluation("pot", POTENTIAL, missing("age", "age"))]
    edges = [edge("held", "COMPLEMENTED_BY", "pot"), edge("held", "COMPLEMENTED_BY", "unevaluated")]
    by_id = {c.scheme_id: c for c in build_recommendations(evals, ["held"], edges, rules).complementary}
    assert by_id["pot"].presentation == "check_eligibility"
    assert by_id["unevaluated"].presentation == "check_eligibility"
    assert by_id["unevaluated"].target_status == "not_evaluated"
    assert "not evaluated" in by_id["unevaluated"].next_actions[0].text


def test_eligible_complement_exclusive_with_an_enrolled_scheme_is_warned_and_not_totalled():
    rules = _rules(make_rule("held"), make_rule("anchor"), make_rule("rival", amount=9000.0))
    evals = [
        evaluation("held", ELIGIBLE, passed("c")),
        evaluation("anchor", ELIGIBLE, passed("c")),
        evaluation("rival", ELIGIBLE, passed("c")),
    ]
    edges = [edge("anchor", "COMPLEMENTED_BY", "rival"), edge("rival", "MUTUALLY_EXCLUSIVE_WITH", "held")]
    report = build_recommendations(evals, ["held"], edges, rules)
    [rival] = report.complementary
    assert rival.warnings and "cannot both be availed" in rival.warnings[0]
    assert report.unclaimed_totals.excluded_mutually_exclusive == (("rival", "held"),)
    assert "rival" not in report.unclaimed_totals.counted_scheme_ids


def test_potential_scheme_exclusive_with_a_counted_unclaimed_scheme_is_not_totalled():
    rules = _rules(
        make_rule("now", amount=100.0), make_rule("later", amount=300.0), make_rule("free", amount=None)
    )
    evals = [
        evaluation("now", ELIGIBLE, passed("c")),
        evaluation("later", POTENTIAL, missing("age", "age")),
        evaluation("free", POTENTIAL, missing("age", "age")),
    ]
    report = build_recommendations(evals, [], [edge("later", "MUTUALLY_EXCLUSIVE_WITH", "now")], rules)
    assert report.potential_totals.recurring_annual_cash_inr == 0.0
    assert report.potential_totals.excluded_mutually_exclusive == (("later", "now"),)
    assert report.potential_totals.amount_not_published == ("free",)


def test_duplicate_evaluations_for_one_scheme_are_rejected():
    rules = _rules(make_rule("a"))
    evals = [evaluation("a", ELIGIBLE, passed("c")), evaluation("a", ELIGIBLE, passed("c"))]
    with pytest.raises(ValueError, match="more than one evaluation for scheme"):
        build_recommendations(evals, [], [], rules)
