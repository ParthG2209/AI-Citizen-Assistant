"""Tests over the shipped scheme rule files in ai_modules/rule_engine/rules/."""

from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pytest

from ai_modules.rule_engine import (
    DEFAULT_RULES_DIR,
    Condition,
    ConditionGroup,
    EligibilityStatus,
    MissingReason,
    Outcome,
    RuleEngine,
    load_rule_file,
    load_rules,
)

from .rule_factory import declared, extracted, verified

AS_OF = date(2026, 11, 18)
RULE_FILES = sorted(Path(DEFAULT_RULES_DIR).rglob("*.yaml"))


@pytest.fixture(scope="module")
def engine() -> RuleEngine:
    return RuleEngine()


def leaves(items):
    for item in items:
        if isinstance(item, ConditionGroup):
            yield from leaves(item.items)
        else:
            yield item


def find_result(results, condition_id):
    for result in results:
        if result.condition_id == condition_id:
            return result
        found = find_result(result.children, condition_id)
        if found is not None:
            return found
    return None


@pytest.mark.parametrize("path", RULE_FILES, ids=lambda p: p.name)
def test_every_rule_file_loads_with_an_https_source(path):
    rule = load_rule_file(path)
    assert any(urlparse(s.url).scheme == "https" for s in rule.sources)
    assert all(s.accessed_on == AS_OF for s in rule.sources)
    assert all(leaf.source_ref for leaf in leaves(rule.conditions))
    assert rule.application_steps, "application steps must come from the official sources"
    if rule.verification_status.value != "verified":
        assert rule.limitations, "non-verified rules must explain their gaps"


def test_shipped_rule_set_is_consistent(engine):
    rules = load_rules()
    assert len(rules) == len(RULE_FILES)
    expected = {
        "gaon_ki_beti",
        "hostel_assistance",
        "laptop_protsahan",
        "mmvy",
        "mp_kisan_kalyan",
        "pm_jay",
        "pm_kisan",
        "pmay_gramin",
        "post_matric_scholarship_obc",
    }
    assert set(engine.scheme_ids()) == expected
    assert {r.scheme_id for r in engine.schemes(AS_OF)} == expected


NUMERIC_LEAVES = [
    (rule.scheme_id, rule.version, leaf)
    for rule in load_rules()
    for leaf in leaves(rule.conditions)
    if leaf.operator in ("lt", "lte", "gt", "gte")
]


@pytest.mark.parametrize(
    ("scheme_id", "version", "leaf"), NUMERIC_LEAVES, ids=lambda v: v if isinstance(v, str) else v.id
)
def test_threshold_boundaries_of_every_numeric_condition(engine, scheme_id, version, leaf: Condition):
    """Exactly at, just above and just below every numeric limit in the shipped rules."""
    rule = next(r for r in engine.versions(scheme_id) if r.version == version)
    step = 1 if leaf.field in ("age", "annual_family_income", "class12_passing_year") else 0.01
    limit = leaf.value
    expectations = {
        "lt": {limit - step: Outcome.PASS, limit: Outcome.FAIL, limit + step: Outcome.FAIL},
        "lte": {limit - step: Outcome.PASS, limit: Outcome.PASS, limit + step: Outcome.FAIL},
        "gt": {limit - step: Outcome.FAIL, limit: Outcome.FAIL, limit + step: Outcome.PASS},
        "gte": {limit - step: Outcome.FAIL, limit: Outcome.PASS, limit + step: Outcome.PASS},
    }[leaf.operator]
    for value, outcome in expectations.items():
        evaluation = engine.evaluate_rule(rule, {leaf.field: verified(round(value, 2))}, AS_OF)
        assert find_result(evaluation.conditions, leaf.id).outcome is outcome, (value, outcome)


# ------------------------------------------------------------ scheme-level scenarios with real thresholds


def profile(**fields):
    return {name: declared(value) for name, value in fields.items()}


MMVY_BASE = dict(
    domicile_state="Madhya Pradesh",
    annual_family_income=500000,
    class12_board="mpbse",
    class12_percentage=82,
    enrolled_in_higher_education=True,
    has_samagra_id=True,
)


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({}, EligibilityStatus.ELIGIBLE),
        ({"annual_family_income": 799999}, EligibilityStatus.ELIGIBLE),
        ({"annual_family_income": 800000}, EligibilityStatus.NOT_ELIGIBLE),  # "less than 8 lakh"
        ({"class12_percentage": 70}, EligibilityStatus.ELIGIBLE),
        ({"class12_percentage": 69.99}, EligibilityStatus.NOT_ELIGIBLE),
        ({"class12_board": "cbse", "class12_percentage": 85}, EligibilityStatus.ELIGIBLE),
        ({"class12_board": "icse", "class12_percentage": 84.99}, EligibilityStatus.NOT_ELIGIBLE),
        ({"class12_board": "other", "class12_percentage": 95}, EligibilityStatus.NOT_ELIGIBLE),
        ({"domicile_state": "Uttar Pradesh"}, EligibilityStatus.NOT_ELIGIBLE),
        ({"class12_board": None}, EligibilityStatus.POTENTIALLY_ELIGIBLE),
    ],
)
def test_mmvy_thresholds(engine, changes, status):
    fields = {k: v for k, v in {**MMVY_BASE, **changes}.items() if v is not None}
    assert engine.evaluate("mmvy", profile(**fields), AS_OF).status is status


def test_mmvy_income_message(engine):
    result = engine.evaluate("mmvy", profile(**{**MMVY_BASE, "annual_family_income": 900000}), AS_OF)
    message = find_result(result.conditions, "income_below_8_lakh").message
    assert message == "Annual family income INR 9,00,000 is not below INR 8,00,000"


OBC_BASE = dict(
    domicile_state="Madhya Pradesh",
    annual_family_income=300000,
    enrolled_in_higher_education=True,
    has_samagra_id=True,
)


def test_post_matric_obc_needs_certificate_backed_category(engine):
    declared_only = {**profile(**OBC_BASE), "social_category": declared("obc")}
    result = engine.evaluate("post_matric_scholarship_obc", declared_only, AS_OF)
    category = find_result(result.conditions, "category_obc")
    assert result.status is EligibilityStatus.POTENTIALLY_ELIGIBLE
    assert category.missing_reason is MissingReason.INSUFFICIENT_TRUST
    assert category.resolves_with == ("caste_certificate",)

    with_certificate = {**profile(**OBC_BASE), "social_category": extracted("obc", 0.93)}
    assert (
        engine.evaluate("post_matric_scholarship_obc", with_certificate, AS_OF).status
        is EligibilityStatus.ELIGIBLE
    )

    blurry = {**profile(**OBC_BASE), "social_category": extracted("obc", 0.55)}
    category = find_result(
        engine.evaluate("post_matric_scholarship_obc", blurry, AS_OF).conditions, "category_obc"
    )
    assert category.missing_reason is MissingReason.LOW_CONFIDENCE

    above = {**with_certificate, "annual_family_income": declared(300001)}
    assert (
        engine.evaluate("post_matric_scholarship_obc", above, AS_OF).status is EligibilityStatus.NOT_ELIGIBLE
    )


def test_hostel_assistance_income_and_category(engine):
    base = {
        **profile(domicile_state="Madhya Pradesh", enrolled_in_higher_education=True, has_samagra_id=True),
        "social_category": verified("st"),
    }
    at_limit = {**base, "annual_family_income": declared(600000)}
    above = {**base, "annual_family_income": declared(600001)}
    assert engine.evaluate("hostel_assistance", at_limit, AS_OF).status is EligibilityStatus.ELIGIBLE
    assert engine.evaluate("hostel_assistance", above, AS_OF).status is EligibilityStatus.NOT_ELIGIBLE
    obc = {**at_limit, "social_category": verified("obc")}
    assert engine.evaluate("hostel_assistance", obc, AS_OF).status is EligibilityStatus.NOT_ELIGIBLE


def test_pm_jay_any_route(engine):
    assert engine.evaluate("pm_jay", profile(age=70), AS_OF).status is EligibilityStatus.ELIGIBLE
    assert engine.evaluate("pm_jay", profile(age=69), AS_OF).status is EligibilityStatus.POTENTIALLY_ELIGIBLE
    not_listed = profile(age=69, listed_in_secc_or_nfsa=False)
    assert engine.evaluate("pm_jay", not_listed, AS_OF).status is EligibilityStatus.NOT_ELIGIBLE
    listed = profile(age=30, listed_in_secc_or_nfsa=True)
    assert engine.evaluate("pm_jay", listed, AS_OF).status is EligibilityStatus.ELIGIBLE


PM_KISAN_BASE = dict(
    family_owns_cultivable_land=True,
    family_member_income_tax_payer=False,
    family_member_government_employee=False,
    family_member_pensioner_above_10k=False,
    occupation="farmer",
)


def test_pm_kisan_and_mp_top_up(engine):
    assert engine.evaluate("pm_kisan", profile(**PM_KISAN_BASE), AS_OF).status is EligibilityStatus.ELIGIBLE
    taxed = profile(**{**PM_KISAN_BASE, "family_member_income_tax_payer": True})
    assert engine.evaluate("pm_kisan", taxed, AS_OF).status is EligibilityStatus.NOT_ELIGIBLE
    mp = engine.evaluate("mp_kisan_kalyan", profile(**PM_KISAN_BASE), AS_OF)
    assert mp.status is EligibilityStatus.POTENTIALLY_ELIGIBLE  # domicile not yet known
    assert find_result(mp.conditions, "mp_resident").missing_reason is MissingReason.NO_VALUE
    top_up = engine.get("mp_kisan_kalyan", AS_OF).relationships[0]
    assert (top_up.scheme_id, top_up.relation) == ("pm_kisan", "TOPS_UP")


def test_pm_kisan_contradictory_government_employee_flags(engine):
    contradictory = profile(**{**PM_KISAN_BASE, "occupation": "government_employee"})
    result = engine.evaluate("pm_kisan", contradictory, AS_OF)
    assert result.status is EligibilityStatus.NOT_ELIGIBLE
    assert find_result(result.conditions, "applicant_not_government_employee").outcome is Outcome.FAIL
    assert find_result(result.conditions, "no_government_employee_in_family").outcome is Outcome.PASS
    assert any("Profile is inconsistent" in note for note in result.notes)


def test_gaon_ki_beti_thresholds(engine):
    base = dict(
        gender="female",
        residence_type="rural",
        enrolled_in_higher_education=True,
        domicile_state="Madhya Pradesh",
    )
    assert engine.evaluate("gaon_ki_beti", profile(**base, class12_percentage=60), AS_OF).status is (
        EligibilityStatus.ELIGIBLE
    )
    assert engine.evaluate("gaon_ki_beti", profile(**base, class12_percentage=59.99), AS_OF).status is (
        EligibilityStatus.NOT_ELIGIBLE
    )
    urban = profile(**{**base, "residence_type": "urban"}, class12_percentage=80)
    assert engine.evaluate("gaon_ki_beti", urban, AS_OF).status is EligibilityStatus.NOT_ELIGIBLE


def test_pmay_gramin_exclusions(engine):
    base = dict(
        residence_type="rural",
        owns_pucca_house=False,
        family_member_government_employee=False,
        occupation="agricultural_labourer",
        family_member_income_tax_payer=False,
    )
    assert engine.evaluate("pmay_gramin", profile(**base), AS_OF).status is EligibilityStatus.ELIGIBLE
    pucca = profile(**{**base, "owns_pucca_house": True})
    assert engine.evaluate("pmay_gramin", pucca, AS_OF).status is EligibilityStatus.NOT_ELIGIBLE


def test_laptop_version_selected_by_exam_round(engine):
    student_2025 = profile(class12_board="mpbse", class12_percentage=75, class12_passing_year=2025)
    student_2026 = profile(class12_board="mpbse", class12_percentage=75, class12_passing_year=2026)
    july_2025 = date(2025, 7, 10)
    assert engine.get("laptop_protsahan", july_2025).version == "2025.1"
    assert engine.get("laptop_protsahan", AS_OF).version == "2026.1"
    assert engine.evaluate("laptop_protsahan", student_2025, july_2025).status is EligibilityStatus.ELIGIBLE
    assert engine.evaluate("laptop_protsahan", student_2025, AS_OF).status is EligibilityStatus.NOT_ELIGIBLE
    assert engine.evaluate("laptop_protsahan", student_2026, AS_OF).status is EligibilityStatus.ELIGIBLE
    low = profile(class12_board="mpbse", class12_percentage=74.99, class12_passing_year=2026)
    assert engine.evaluate("laptop_protsahan", low, AS_OF).status is EligibilityStatus.NOT_ELIGIBLE


def test_rules_not_effective_before_their_start_are_excluded(engine):
    before_pm_jay_expansion = date(2024, 10, 28)
    ids = {r.scheme_id for r in engine.schemes(before_pm_jay_expansion)}
    assert "pm_jay" not in ids and "mmvy" not in ids and "laptop_protsahan" not in ids
    assert "pm_kisan" in ids


def test_evaluate_all_is_reproducible(engine):
    citizen = {
        **profile(**MMVY_BASE, **PM_KISAN_BASE, gender="female", residence_type="rural", age=19),
        "social_category": extracted("obc", 0.9),
    }
    first = [e.to_dict() for e in engine.evaluate_all(citizen, AS_OF)]
    second = [e.to_dict() for e in RuleEngine().evaluate_all(dict(reversed(citizen.items())), AS_OF)]
    assert first == second
    assert len({e["input_hash"] for e in first}) == len(first)  # rule hash is part of every input hash
