"""Regression tests for profile value validation (``coerce_value``) and engine-side FieldValue checks."""

import math
from datetime import date
from decimal import Decimal
from fractions import Fraction

import pytest

from ai_modules.rule_engine import (
    PROFILE_FIELDS,
    EligibilityStatus,
    FieldValue,
    MissingReason,
    ProfileValueError,
    Provenance,
    RuleEngine,
    coerce_value,
    parse_rule,
)
from ai_modules.rule_engine.fields import MAX_ANNUAL_FAMILY_INCOME_INR

from .rule_factory import declared, extracted, leaf, rule_dict

AS_OF = date(2026, 11, 18)
INCOME = "annual_family_income"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (INCOME, float("nan")),
        (INCOME, float("inf")),
        (INCOME, float("-inf")),
        (INCOME, "nan"),
        (INCOME, "NaN"),
        (INCOME, "inf"),
        (INCOME, "-Infinity"),
        (INCOME, Decimal("NaN")),
        (INCOME, Decimal("Infinity")),
        ("class12_percentage", "nan"),
        ("class12_percentage", float("nan")),
        ("age", float("nan")),
        ("age", float("inf")),  # used to raise OverflowError instead of ValueError
        ("age", "inf"),
    ],
)
def test_non_finite_numbers_rejected(name, value):
    with pytest.raises(ValueError, match=name):
        coerce_value(name, value)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (INCOME, "12abc"),
        (INCOME, ""),
        (INCOME, " "),
        (INCOME, "1_000"),  # Python's float() accepts underscores; a form value must not
        (INCOME, "1,00,000"),
        (INCOME, "0x10"),
        (INCOME, b"1000"),
        (INCOME, [1000]),
        (INCOME, {"value": 1000}),
        (INCOME, (1000,)),
        (INCOME, True),
        (INCOME, False),
        ("age", True),
        ("age", "18 years"),
        ("age", b"18"),  # int(b"18") used to succeed
        ("age", [18]),
        ("class12_percentage", {"pct": 80}),
    ],
)
def test_malformed_numbers_rejected(name, value):
    with pytest.raises(ValueError, match=name):
        coerce_value(name, value)


@pytest.mark.parametrize("value", [18.5, "18.5", Decimal("18.5"), Fraction(37, 2), "18.0000000000000001"])
def test_fractional_integers_rejected(value):
    with pytest.raises(ValueError, match="must be an integer"):
        coerce_value("age", value)


@pytest.mark.parametrize("value", [18, 18.0, "18", " 18 ", "18.0", "+18", "1.8e1", Decimal("18.00")])
def test_integral_values_accepted_for_integer_fields(value):
    result = coerce_value("age", value)
    assert result == 18 and type(result) is int


@pytest.mark.parametrize(
    ("name", "value", "fragment"),
    [
        (INCOME, -1, ">= 0"),
        (INCOME, "-0.01", ">= 0"),
        (INCOME, 1e30, f"<= {MAX_ANNUAL_FAMILY_INCOME_INR}"),
        (INCOME, 10**400, f"<= {MAX_ANNUAL_FAMILY_INCOME_INR}"),  # used to raise OverflowError
        (INCOME, "1e999999", f"<= {MAX_ANNUAL_FAMILY_INCOME_INR}"),
        (INCOME, MAX_ANNUAL_FAMILY_INCOME_INR + 1, f"<= {MAX_ANNUAL_FAMILY_INCOME_INR}"),
        ("class12_percentage", -0.01, ">= 0"),
        ("class12_percentage", 100.01, "<= 100"),
        ("class12_percentage", "101", "<= 100"),
        ("age", -1, ">= 0"),
        ("age", 121, "<= 120"),
        ("class12_passing_year", 1949, ">= 1950"),
    ],
)
def test_out_of_range_numbers_rejected(name, value, fragment):
    with pytest.raises(ValueError, match=fragment):
        coerce_value(name, value)


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        (INCOME, 0, 0.0),
        (INCOME, -0.0, 0.0),
        (INCOME, "450000", 450000.0),
        (INCOME, " 4.5e5 ", 450000.0),
        (INCOME, Decimal("450000.50"), 450000.5),
        (INCOME, MAX_ANNUAL_FAMILY_INCOME_INR, float(MAX_ANNUAL_FAMILY_INCOME_INR)),
        ("class12_percentage", 0, 0.0),
        ("class12_percentage", "100", 100.0),
        ("class12_percentage", "72.5", 72.5),
        ("age", 0, 0),
        ("age", 120, 120),
    ],
)
def test_valid_numbers_accepted(name, value, expected):
    result = coerce_value(name, value)
    assert result == expected and type(result) is type(expected)
    assert math.copysign(1.0, result) == 1.0  # -0.0 normalised so equal inputs hash equally


def test_income_maximum_is_declared_and_far_above_every_policy_threshold():
    assert PROFILE_FIELDS[INCOME].minimum == 0
    assert PROFILE_FIELDS[INCOME].maximum == MAX_ANNUAL_FAMILY_INCOME_INR
    assert PROFILE_FIELDS["class12_percentage"].minimum == 0
    assert PROFILE_FIELDS["class12_percentage"].maximum == 100


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("domicile_state", ["Madhya Pradesh"]),
        ("domicile_state", True),
        ("district", {"name": "Sehore"}),
        ("district", 123),
        ("gender", ["female"]),
        ("social_category", 1),
        ("full_name", b"Asha"),
    ],
)
def test_text_and_enum_fields_require_strings(name, value):
    with pytest.raises(ValueError, match=f"Field '{name}' must be a string"):
        coerce_value(name, value)


@pytest.mark.parametrize("value", [1, 0, 1.0, [True], "maybe", ""])
def test_boolean_fields_reject_non_boolean_values(value):
    with pytest.raises(ValueError, match="must be a boolean"):
        coerce_value("has_samagra_id", value)


# ---------------------------------------------------------------- engine-side checks


def income_engine() -> RuleEngine:
    return RuleEngine(rules=[parse_rule(rule_dict())])


def evaluate(profile):
    return income_engine().evaluate("test_scheme", profile, as_of=AS_OF)


@pytest.mark.parametrize(
    "value", [float("nan"), "nan", float("inf"), "12abc", True, [500000], {"v": 1}, -5, 1e30, b"1"]
)
def test_engine_rejects_invalid_field_values_passed_directly(value):
    with pytest.raises(ProfileValueError, match=INCOME):
        evaluate({INCOME: FieldValue(value, Provenance.SELF_DECLARED)})


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -0.1, 1.5, "0.9", True, [0.9]])
def test_engine_rejects_invalid_confidence(confidence):
    # NaN used to pass the threshold check (NaN < 0.8 is False), letting a document value decide.
    with pytest.raises(ProfileValueError, match="confidence"):
        evaluate({INCOME: extracted(900000, confidence)})


def test_engine_rejects_unknown_provenance():
    with pytest.raises(ProfileValueError, match=INCOME):
        evaluate({INCOME: FieldValue(1000, "guessed")})


def test_engine_accepts_boundary_confidence():
    low = evaluate({INCOME: extracted(500000, 0)}).conditions[0]
    assert low.missing_reason is MissingReason.LOW_CONFIDENCE
    assert evaluate({INCOME: extracted(500000, 1)}).status is EligibilityStatus.ELIGIBLE


def test_engine_integer_field_accepts_integral_float_and_rejects_fraction():
    engine = RuleEngine(rules=[parse_rule(rule_dict(conditions=[leaf("adult", "age", "gte", 18)]))])
    ok = engine.evaluate("test_scheme", {"age": declared(18.0)}, as_of=AS_OF)
    assert ok.status is EligibilityStatus.ELIGIBLE and ok.conditions[0].actual == 18
    with pytest.raises(ProfileValueError, match="must be an integer"):
        engine.evaluate("test_scheme", {"age": declared("17.5")}, as_of=AS_OF)


def test_equal_inputs_hash_equally_after_coercion():
    assert evaluate({INCOME: declared(-0.0)}).input_hash == evaluate({INCOME: declared(0)}).input_hash
    assert (
        evaluate({INCOME: declared("600000")}).input_hash == evaluate({INCOME: declared(600000)}).input_hash
    )
