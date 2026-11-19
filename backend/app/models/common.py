"""Small helpers shared by the ORM models."""

from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from sqlalchemy import CheckConstraint

from ai_modules.rule_engine.fields import Provenance

PROVENANCE_VALUES = tuple(p.value for p in Provenance)


def values_in(column: str, values: Iterable[str | StrEnum], name: str) -> CheckConstraint:
    """CHECK constraint restricting ``column`` to a fixed set of string values."""
    quoted = ", ".join(f"'{str(v)}'" for v in values)
    return CheckConstraint(f"{column} IN ({quoted})", name=name)
