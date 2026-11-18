from datetime import date

import pytest

from ai_modules.rule_engine import (
    EligibilityStatus,
    FieldValue,
    MissingReason,
    NoEffectiveRuleError,
    Outcome,
    ProfileValueError,
    RuleEngine,
    UnknownSchemeError,
    format_value,
    parse_rule,
)

from .rule_factory import declared, extracted, leaf, rule_dict, verified

AS_OF = date(2026, 11, 18)


def engine_for(*conditions, **overrides) -> RuleEngine:
    data = rule_dict(conditions=list(conditions), **overrides) if conditions else rule_dict(**overrides)
    return RuleEngine(rules=[parse_rule(data)])


def evaluate(engine: RuleEngine, profile, as_of=AS_OF):
    return engine.evaluate("test_scheme", profile, as_of=as_of)


# ---------------------------------------------------------------- leaf outcomes and messages


def test_income_messages_use_indian_grouping():
    engine = engine_for()
    failed = evaluate(engine, {"annual_family_income": declared(700000)})
    assert failed.status is EligibilityStatus.NOT_ELIGIBLE
    assert (
        failed.conditions[0].message == "Annual family income INR 7,00,000 exceeds the limit of INR 6,00,000"
    )
    passed = evaluate(engine, {"annual_family_income": declared(600000)})
    assert passed.status is EligibilityStatus.ELIGIBLE
    assert (
        passed.conditions[0].message
        == "Annual family income INR 6,00,000 is within the limit of INR 6,00,000"
    )


def test_failure_message_is_appended():
    engine = engine_for(
        leaf("not_gov", "occupation", "ne", "government_employee", failure_message="Excluded.")
    )
    result = evaluate(engine, {"occupation": declared("government_employee")})
    assert (
        result.conditions[0].message
        == "Occupation / status is government employee, which is excluded. Excluded."
    )


def test_missing_value():
    result = evaluate(engine_for(), {})
    cond = result.conditions[0]
    assert result.status is EligibilityStatus.POTENTIALLY_ELIGIBLE
    assert cond.outcome is Outcome.MISSING
    assert cond.missing_reason is MissingReason.NO_VALUE
    assert cond.actual is None and cond.provenance is None
    assert cond.resolves_with == ("income_certificate",)
    assert "Provide: MP Income Certificate" in cond.message
    explicit_none = evaluate(engine_for(), {"annual_family_income": FieldValue(None, "self_declared")})
    assert explicit_none.conditions[0].missing_reason is MissingReason.NO_VALUE


def test_self_declared_caste_is_insufficient_where_certificate_required():
    engine = engine_for(
        leaf(
            "obc",
            "social_category",
            "eq",
            "obc",
            min_trust="document_extracted",
            resolves_with=["caste_certificate"],
        )
    )
    declared_result = evaluate(engine, {"social_category": declared("obc")})
    cond = declared_result.conditions[0]
    assert declared_result.status is EligibilityStatus.POTENTIALLY_ELIGIBLE
    assert cond.missing_reason is MissingReason.INSUFFICIENT_TRUST
    assert cond.actual == "obc" and cond.provenance == "self_declared"
    assert "Caste Certificate" in cond.message
    # A self-declared value that would FAIL is still MISSING: untrusted data never decides either way.
    assert evaluate(engine, {"social_category": declared("sc")}).conditions[0].outcome is Outcome.MISSING
    assert evaluate(engine, {"social_category": extracted("obc")}).status is EligibilityStatus.ELIGIBLE
    assert evaluate(engine, {"social_category": verified("obc")}).status is EligibilityStatus.ELIGIBLE
    assert evaluate(engine, {"social_category": extracted("sc")}).status is EligibilityStatus.NOT_ELIGIBLE


@pytest.mark.parametrize(
    ("confidence", "outcome"),
    [(0.79, Outcome.MISSING), (0.8, Outcome.PASS), (0.95, Outcome.PASS), (None, Outcome.MISSING)],
)
def test_low_confidence_document_value(confidence, outcome):
    result = evaluate(engine_for(), {"annual_family_income": extracted(500000, confidence)})
    cond = result.conditions[0]
    assert cond.outcome is outcome
    if outcome is Outcome.MISSING:
        assert cond.missing_reason is MissingReason.LOW_CONFIDENCE
        assert cond.actual == 500000
        assert "below the 0.80 threshold" in cond.message


def test_confidence_threshold_is_configurable():
    engine = RuleEngine(rules=[parse_rule(rule_dict())], confidence_threshold=0.9)
    cond = evaluate(engine, {"annual_family_income": extracted(500000, 0.85)}).conditions[0]
    assert cond.missing_reason is MissingReason.LOW_CONFIDENCE
    with pytest.raises(ValueError):
        RuleEngine(rules=[parse_rule(rule_dict())], confidence_threshold=1.5)


def test_verified_and_self_declared_values_skip_confidence_check():
    assert evaluate(engine_for(), {"annual_family_income": declared(1)}).status is EligibilityStatus.ELIGIBLE
    assert evaluate(engine_for(), {"annual_family_income": verified(1)}).status is EligibilityStatus.ELIGIBLE


def test_profile_values_are_coerced_and_invalid_values_rejected():
    engine = engine_for(leaf("cat", "social_category", "in", ["obc", "sc"]))
    assert evaluate(engine, {"social_category": declared(" OBC ")}).status is EligibilityStatus.ELIGIBLE
    with pytest.raises(ProfileValueError, match="social_category"):
        evaluate(engine, {"social_category": declared("brahmin")})
    with pytest.raises(ProfileValueError, match="FieldValue"):
        evaluate(engine, {"social_category": "obc"})


# ---------------------------------------------------------------- groups and scheme status


def any_group():
    return {
        "id": "route",
        "description": "Listed household or senior citizen",
        "any": [leaf("listed", "listed_in_secc_or_nfsa", "is_true"), leaf("senior", "age", "gte", 70)],
    }


def all_group():
    return {
        "id": "both",
        "description": "Rural and landowning",
        "all": [
            leaf("rural", "residence_type", "eq", "rural"),
            leaf("land", "family_owns_cultivable_land", "is_true"),
        ],
    }


@pytest.mark.parametrize(
    ("profile", "outcome"),
    [
        ({"listed_in_secc_or_nfsa": declared(True)}, Outcome.PASS),  # PASS + MISSING -> PASS
        ({"listed_in_secc_or_nfsa": declared(False), "age": declared(72)}, Outcome.PASS),
        ({"listed_in_secc_or_nfsa": declared(False)}, Outcome.MISSING),  # FAIL + MISSING -> MISSING
        ({}, Outcome.MISSING),
        ({"listed_in_secc_or_nfsa": declared(False), "age": declared(69)}, Outcome.FAIL),
    ],
)
def test_any_group_semantics(profile, outcome):
    result = evaluate(engine_for(any_group()), profile)
    group = result.conditions[0]
    assert group.outcome is outcome
    assert group.operator == "any"
    assert [c.condition_id for c in group.children] == ["listed", "senior"]
    assert group.field == "listed_in_secc_or_nfsa, age"


@pytest.mark.parametrize(
    ("profile", "outcome"),
    [
        ({"residence_type": declared("rural"), "family_owns_cultivable_land": declared(True)}, Outcome.PASS),
        ({"residence_type": declared("rural")}, Outcome.MISSING),  # PASS + MISSING -> MISSING
        ({"residence_type": declared("urban")}, Outcome.FAIL),  # FAIL + MISSING -> FAIL
        ({"residence_type": declared("urban"), "family_owns_cultivable_land": declared(True)}, Outcome.FAIL),
    ],
)
def test_all_group_semantics(profile, outcome):
    assert evaluate(engine_for(all_group()), profile).conditions[0].outcome is outcome


def test_group_message_and_resolves_with_summarise_children():
    engine = engine_for(
        {
            "id": "both",
            "description": "Income and caste",
            "all": [
                leaf("inc", "annual_family_income", "lte", 250000, resolves_with=["income_certificate"]),
                leaf("cat", "social_category", "eq", "obc", resolves_with=["caste_certificate"]),
            ],
        }
    )
    group = evaluate(engine, {"annual_family_income": declared(100000)}).conditions[0]
    assert group.outcome is Outcome.MISSING
    assert group.resolves_with == ("caste_certificate",)
    assert group.message.startswith("Income and caste: cannot be decided yet (1 of 2 required conditions")
    assert "[social_category eq obc] Social category is not provided" in group.message


def test_nested_groups_any_of_all():
    engine = engine_for(
        {
            "id": "marks",
            "description": "Board-wise marks",
            "any": [
                {
                    "id": "mp",
                    "description": "MP Board route",
                    "all": [
                        leaf("mp_board", "class12_board", "eq", "mpbse"),
                        leaf("mp_marks", "class12_percentage", "gte", 75),
                    ],
                },
                {
                    "id": "cbse",
                    "description": "CBSE route",
                    "all": [
                        leaf("cbse_board", "class12_board", "eq", "cbse"),
                        leaf("cbse_marks", "class12_percentage", "gte", 85),
                    ],
                },
            ],
        }
    )

    def outcome(**fields):
        return evaluate(engine, {k: declared(v) for k, v in fields.items()}).conditions[0].outcome

    assert outcome(class12_board="mpbse", class12_percentage=75) is Outcome.PASS
    assert outcome(class12_board="cbse", class12_percentage=80) is Outcome.FAIL
    assert outcome(class12_percentage=90) is Outcome.MISSING
    assert outcome(class12_percentage=60) is Outcome.FAIL  # fails both routes without knowing the board


def test_scheme_status_precedence():
    engine = engine_for(
        leaf("inc", "annual_family_income", "lte", 600000), leaf("rural", "residence_type", "eq", "rural")
    )
    assert (
        evaluate(engine, {"annual_family_income": declared(700000)}).status is EligibilityStatus.NOT_ELIGIBLE
    )
    assert (
        evaluate(engine, {"annual_family_income": declared(1)}).status
        is EligibilityStatus.POTENTIALLY_ELIGIBLE
    )
    full = {"annual_family_income": declared(1), "residence_type": declared("rural")}
    assert evaluate(engine, full).status is EligibilityStatus.ELIGIBLE


def test_contradictory_occupation_and_family_flag():
    """occupation=government_employee + family_member_government_employee=false: each condition is
    evaluated on the recorded value, the failing one decides, and a note flags the inconsistency."""
    engine = engine_for(
        leaf("not_gov", "occupation", "ne", "government_employee"),
        leaf("no_family_gov", "family_member_government_employee", "is_false"),
    )
    profile = {
        "occupation": declared("government_employee"),
        "family_member_government_employee": declared(False),
    }
    first = evaluate(engine, profile)
    assert first.status is EligibilityStatus.NOT_ELIGIBLE
    assert [c.outcome for c in first.conditions] == [Outcome.FAIL, Outcome.PASS]
    assert any("Profile is inconsistent" in note for note in first.notes)
    assert evaluate(engine, profile).to_dict() == first.to_dict()
    consistent = {"occupation": declared("farmer"), "family_member_government_employee": declared(False)}
    assert not any("inconsistent" in note for note in evaluate(engine, consistent).notes)


def test_unverified_rule_adds_note():
    notes = evaluate(engine_for(), {"annual_family_income": declared(1)}).notes
    assert any("'unverified'" in note for note in notes)


# ---------------------------------------------------------------- versions and lookups


def versioned_engine():
    old = parse_rule(
        rule_dict(
            version="2024.1",
            effective_from="2024-01-01",
            effective_to="2024-12-31",
            conditions=[leaf("income_limit", "annual_family_income", "lte", 500000)],
        )
    )
    new = parse_rule(rule_dict(version="2025.1", effective_from="2025-01-01"))
    future = parse_rule(rule_dict(scheme_id="future_scheme", effective_from="2027-04-01"))
    return RuleEngine(rules=[new, old, future])


def test_version_selected_by_as_of():
    engine = versioned_engine()
    profile = {"annual_family_income": declared(550000)}
    old = engine.evaluate("test_scheme", profile, as_of=date(2024, 6, 1))
    new = engine.evaluate("test_scheme", profile, as_of=date(2025, 6, 1))
    assert (old.rule_version, old.status) == ("2024.1", EligibilityStatus.NOT_ELIGIBLE)
    assert (new.rule_version, new.status) == ("2025.1", EligibilityStatus.ELIGIBLE)
    assert engine.get("test_scheme", date(2024, 12, 31)).version == "2024.1"
    assert engine.get("test_scheme", date(2025, 1, 1)).version == "2025.1"
    assert [r.version for r in engine.versions("test_scheme")] == ["2024.1", "2025.1"]


def test_not_yet_effective_rules_are_excluded():
    engine = versioned_engine()
    assert [e.scheme_id for e in engine.evaluate_all({}, as_of=AS_OF)] == ["test_scheme"]
    assert [r.scheme_id for r in engine.schemes(AS_OF)] == ["test_scheme"]
    assert [r.scheme_id for r in engine.schemes(date(2027, 4, 1))] == ["future_scheme", "test_scheme"]
    with pytest.raises(NoEffectiveRuleError):
        engine.evaluate("future_scheme", {}, as_of=AS_OF)
    with pytest.raises(NoEffectiveRuleError):
        engine.get("test_scheme", date(2023, 12, 31))
    with pytest.raises(UnknownSchemeError):
        engine.get("nope", AS_OF)


# ---------------------------------------------------------------- reproducibility


def test_identical_inputs_give_identical_results_and_hashes():
    engine = engine_for(any_group(), leaf("inc", "annual_family_income", "lte", 600000))
    profile = {
        "age": declared(72),
        "annual_family_income": extracted(450000, 0.91),
        "full_name": declared("Synthetic Person"),  # not referenced -> must not affect the hash
    }
    a = evaluate(engine, profile)
    b = evaluate(
        engine_for(any_group(), leaf("inc", "annual_family_income", "lte", 600000)),
        dict(reversed(profile.items())),
    )
    assert a.input_hash == b.input_hash
    assert a.to_dict() == b.to_dict()
    without_name = {k: v for k, v in profile.items() if k != "full_name"}
    assert evaluate(engine, without_name).input_hash == a.input_hash
    assert evaluate(engine, {**profile, "age": declared(71)}).input_hash != a.input_hash
    assert (
        evaluate(engine, {**profile, "age": extracted(72)}).input_hash != a.input_hash
    )  # provenance matters
    assert evaluate(engine, profile, as_of=date(2026, 11, 19)).input_hash != a.input_hash
    assert evaluate(engine, {**profile, "age": declared("72")}).input_hash == a.input_hash  # coerced first


def test_to_dict_is_json_serialisable():
    import json

    data = evaluate(engine_for(any_group()), {"age": declared(72)}).to_dict()
    text = json.dumps(data, default=str)
    assert data["as_of"] == "2026-11-18"
    assert data["conditions"][0]["children"][1]["outcome"] == "pass"
    assert "pass" in text


@pytest.mark.parametrize(
    ("field", "value", "shown"),
    [
        ("annual_family_income", 12345678, "INR 1,23,45,678"),
        ("annual_family_income", 999, "INR 999"),
        ("annual_family_income", 250000.5, "INR 2,50,000.50"),
        ("class12_percentage", 72.5, "72.5%"),
        ("class12_percentage", 70.0, "70%"),
        ("age", 70, "70 years"),
        ("social_category", "obc", "OBC"),
        ("occupation", "government_employee", "government employee"),
        ("has_samagra_id", True, "yes"),
    ],
)
def test_format_value(field, value, shown):
    assert format_value(field, value) == shown
