import pytest

from ai_modules.rule_engine import OPERATORS, Outcome, apply_operator

PASS, FAIL = Outcome.PASS, Outcome.FAIL


@pytest.mark.parametrize(
    ("operator", "actual", "expected", "outcome"),
    [
        ("eq", "obc", "obc", PASS),
        ("eq", "sc", "obc", FAIL),
        ("eq", "Madhya  pradesh", "Madhya Pradesh", PASS),  # case/whitespace-insensitive strings
        ("eq", 5, 5.0, PASS),
        ("eq", True, True, PASS),
        ("eq", False, True, FAIL),
        ("ne", "urban", "rural", PASS),
        ("ne", "rural", "rural", FAIL),
        ("in", "obc", ("sc", "st", "obc"), PASS),
        ("in", "general", ("sc", "st", "obc"), FAIL),
        ("in", "BHOPAL", ("Bhopal", "Indore"), PASS),
        ("not_in", "farmer", ("government_employee",), PASS),
        ("not_in", "government_employee", ("government_employee",), FAIL),
        ("lt", 69.99, 70, PASS),
        ("lt", 70, 70, FAIL),
        ("lte", 600000, 600000, PASS),
        ("lte", 600000.01, 600000, FAIL),
        ("lte", 599999, 600000, PASS),
        ("gt", 70.01, 70, PASS),
        ("gt", 70, 70, FAIL),
        ("gte", 70, 70, PASS),
        ("gte", 69.99, 70, FAIL),
        ("between", 18, (18, 40), PASS),
        ("between", 40, (18, 40), PASS),
        ("between", 41, (18, 40), FAIL),
        ("between", 17, (18, 40), FAIL),
        ("is_true", True, None, PASS),
        ("is_true", False, None, FAIL),
        ("is_false", False, None, PASS),
        ("is_false", True, None, FAIL),
    ],
)
def test_operator_outcomes(operator, actual, expected, outcome):
    assert apply_operator(operator, actual, expected) is outcome


def test_every_operator_is_covered():
    assert set(OPERATORS) == {
        "eq",
        "ne",
        "in",
        "not_in",
        "lt",
        "lte",
        "gt",
        "gte",
        "between",
        "is_true",
        "is_false",
    }


def test_unknown_operator_rejected():
    with pytest.raises(ValueError, match="Unknown operator"):
        apply_operator("contains", "a", "b")


def test_missing_value_is_not_an_operator_concern():
    with pytest.raises(ValueError, match="missing values are handled by the engine"):
        apply_operator("lte", None, 10)


@pytest.mark.parametrize(
    ("operator", "actual"), [("lte", "100"), ("gte", True), ("is_true", 1), ("between", "5")]
)
def test_wrongly_typed_value_raises(operator, actual):
    expected = (1, 10) if operator == "between" else 10
    with pytest.raises(TypeError):
        apply_operator(operator, actual, expected)
