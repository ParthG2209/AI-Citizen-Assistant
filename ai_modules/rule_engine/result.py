"""Result data structures produced by the deterministic rule engine (shared contract)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class Outcome(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    MISSING = "missing"


class MissingReason(StrEnum):
    NO_VALUE = "no_value"  # field not present in the profile
    INSUFFICIENT_TRUST = "insufficient_trust"  # value present but provenance below the rule's min_trust
    LOW_CONFIDENCE = "low_confidence"  # document-extracted value below the OCR confidence threshold


class EligibilityStatus(StrEnum):
    ELIGIBLE = "eligible"  # every condition PASS
    POTENTIALLY_ELIGIBLE = "potentially_eligible"  # no FAIL, at least one MISSING
    NOT_ELIGIBLE = "not_eligible"  # at least one FAIL


DISCLAIMER = (
    "Indicative result from a local rule engine based on published scheme guidelines. "
    "It is not an official approval; the sanctioning authority makes the final decision."
)


@dataclass(frozen=True, slots=True)
class ConditionResult:
    condition_id: str
    description: str
    field: str
    operator: str
    expected: Any
    actual: Any  # None when missing
    provenance: str | None  # Provenance value of the profile field used, if any
    outcome: Outcome
    missing_reason: MissingReason | None
    message: str  # human-readable explanation of this specific outcome
    resolves_with: tuple[str, ...] = ()  # DOCUMENT_TYPES ids or profile field names that would resolve MISSING


@dataclass(frozen=True, slots=True)
class SchemeEvaluation:
    scheme_id: str
    scheme_name: str
    rule_version: str
    rule_hash: str  # sha256 of the canonical rule definition
    verification_status: str  # verified | partially_verified | unverified (rule-file evidence level)
    status: EligibilityStatus
    conditions: tuple[ConditionResult, ...]
    input_hash: str  # sha256 of the canonical profile values used -> reproducibility
    as_of: date
    disclaimer: str = DISCLAIMER
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> tuple[ConditionResult, ...]:
        return tuple(c for c in self.conditions if c.outcome is Outcome.PASS)

    @property
    def failed(self) -> tuple[ConditionResult, ...]:
        return tuple(c for c in self.conditions if c.outcome is Outcome.FAIL)

    @property
    def missing(self) -> tuple[ConditionResult, ...]:
        return tuple(c for c in self.conditions if c.outcome is Outcome.MISSING)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["as_of"] = self.as_of.isoformat()
        return data
