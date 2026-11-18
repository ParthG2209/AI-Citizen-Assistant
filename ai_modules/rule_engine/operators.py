"""Explicit, typed comparison operators used by scheme rules.

Every operator receives a value that is already present and coerced to the declared field type
(see ``fields.coerce_value``). It only ever answers PASS or FAIL; deciding MISSING (no value,
insufficient trust, low OCR confidence) is the engine's job, not the operator's.

String comparisons are case-insensitive and ignore repeated whitespace, so
"Madhya  pradesh" equals "Madhya Pradesh".
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from .fields import FieldType
from .result import Outcome


class ValueShape(StrEnum):
    """What the rule's ``value`` must look like for an operator."""

    NONE = "none"  # no value (is_true / is_false)
    SCALAR = "scalar"  # one value of the field's type
    LIST = "list"  # non-empty list of values of the field's type
    RANGE = "range"  # [low, high], low <= high, both inclusive


@dataclass(frozen=True, slots=True)
class Operator:
    name: str
    symbol: str  # short human form used in docs, e.g. "<="
    field_types: frozenset[FieldType]
    shape: ValueShape
    test: Callable[[Any, Any], bool]


_NUMERIC = frozenset({FieldType.INTEGER, FieldType.NUMBER})
_ANY_TYPE = frozenset(FieldType)
_DISCRETE = frozenset({FieldType.INTEGER, FieldType.STRING, FieldType.ENUM})


def normalize(value: Any) -> Any:
    """Canonical comparison form: strings casefolded with collapsed whitespace; others unchanged."""
    if isinstance(value, str):
        return " ".join(value.split()).casefold()
    return value


def _eq(actual: Any, expected: Any) -> bool:
    return normalize(actual) == normalize(expected)


def _in(actual: Any, expected: Any) -> bool:
    return normalize(actual) in {normalize(item) for item in expected}


def _between(actual: Any, expected: Any) -> bool:
    low, high = expected
    return low <= actual <= high


OPERATORS: dict[str, Operator] = {
    op.name: op
    for op in (
        Operator("eq", "=", _ANY_TYPE, ValueShape.SCALAR, _eq),
        Operator("ne", "!=", _ANY_TYPE, ValueShape.SCALAR, lambda a, e: not _eq(a, e)),
        Operator("in", "in", _DISCRETE, ValueShape.LIST, _in),
        Operator("not_in", "not in", _DISCRETE, ValueShape.LIST, lambda a, e: not _in(a, e)),
        Operator("lt", "<", _NUMERIC, ValueShape.SCALAR, lambda a, e: a < e),
        Operator("lte", "<=", _NUMERIC, ValueShape.SCALAR, lambda a, e: a <= e),
        Operator("gt", ">", _NUMERIC, ValueShape.SCALAR, lambda a, e: a > e),
        Operator("gte", ">=", _NUMERIC, ValueShape.SCALAR, lambda a, e: a >= e),
        Operator("between", "between", _NUMERIC, ValueShape.RANGE, _between),
        Operator(
            "is_true", "is true", frozenset({FieldType.BOOLEAN}), ValueShape.NONE, lambda a, _: a is True
        ),
        Operator(
            "is_false", "is false", frozenset({FieldType.BOOLEAN}), ValueShape.NONE, lambda a, _: a is False
        ),
    )
}


def apply_operator(name: str, actual: Any, expected: Any = None) -> Outcome:
    """Compare a present, correctly typed ``actual`` value with the rule's ``expected`` value."""
    operator = OPERATORS.get(name)
    if operator is None:
        raise ValueError(f"Unknown operator '{name}'")
    if actual is None:
        raise ValueError(f"Operator '{name}' needs a present value; missing values are handled by the engine")
    if operator.field_types == _NUMERIC and (isinstance(actual, bool) or not isinstance(actual, int | float)):
        raise TypeError(f"Operator '{name}' needs a number, got {type(actual).__name__}")
    if operator.shape is ValueShape.NONE and not isinstance(actual, bool):
        raise TypeError(f"Operator '{name}' needs a boolean, got {type(actual).__name__}")
    return Outcome.PASS if operator.test(actual, expected) else Outcome.FAIL
