"""Deterministic eligibility evaluation over validated scheme rules.

Outcome of a leaf condition, checked in this order:

1. no value in the profile                                   -> MISSING (no_value)
2. provenance trust below the condition's ``min_trust``      -> MISSING (insufficient_trust)
3. ``document_extracted`` value with OCR confidence missing
   or below ``confidence_threshold``                         -> MISSING (low_confidence)
4. otherwise the typed operator decides                      -> PASS / FAIL

Groups: ALL -> FAIL if any FAIL, else MISSING if any MISSING, else PASS.
        ANY -> PASS if any PASS, else MISSING if any MISSING, else FAIL.
Scheme: any FAIL -> not_eligible; else any MISSING -> potentially_eligible; else eligible.

Nothing here is random or reads the clock except the default ``as_of`` (today). The LLM never
participates: explanations are produced elsewhere from these results.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

from .definitions import Condition, ConditionGroup, GroupMode, SchemeRule
from .fields import DOCUMENT_TYPES, PROFILE_FIELDS, TRUST, FieldType, FieldValue, Provenance, coerce_value
from .loader import DEFAULT_RULES_DIR, canonical_json, load_rules, sha256_hex, validate_rule_set
from .operators import apply_operator
from .result import ConditionResult, EligibilityStatus, MissingReason, Outcome, SchemeEvaluation

DEFAULT_CONFIDENCE_THRESHOLD = 0.80

_PROVENANCE_LABEL = {
    Provenance.SELF_DECLARED: "self-declared",
    Provenance.DOCUMENT_EXTRACTED: "read from an uploaded document",
    Provenance.VERIFIED: "verified",
}
_UPPERCASE_VALUE_FIELDS = frozenset({"social_category", "class12_board"})

# Profile combinations that cannot both be true. Conditions are still evaluated independently on the
# recorded values (so the stricter, failing condition decides); the engine only adds a note.
_CONTRADICTIONS: tuple[tuple[str, str, Any, str, Any, str], ...] = (
    (
        "occupation_vs_family_govt_employee",
        "occupation",
        "government_employee",
        "family_member_government_employee",
        False,
        "Profile is inconsistent: occupation is 'government employee' but 'any family member is a serving "
        "government employee' is recorded as no (the applicant is part of the family). Each condition was "
        "evaluated on the value recorded, so any condition that fails on either value makes the scheme "
        "not eligible. Please correct the profile.",
    ),
)


class UnknownSchemeError(KeyError):
    """No rule file defines this scheme id."""


class NoEffectiveRuleError(LookupError):
    """The scheme exists but no rule version is effective on the requested date."""


class ProfileValueError(ValueError):
    """A profile value does not match the declared field type."""


# --------------------------------------------------------------------------- formatting helpers


def _indian_grouping(number: int) -> str:
    """12345678 -> '1,23,45,678' (Indian numbering system)."""
    digits = str(abs(number))
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        digits = ",".join(groups) + "," + tail
    return ("-" if number < 0 else "") + digits


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.2f}".rstrip("0").rstrip(".")


def format_value(field: str, value: Any) -> str:
    """Deterministic human-readable form of a field value, e.g. 'INR 6,00,000', '72.5%', 'yes'."""
    if value is None:
        return "not provided"
    spec = PROFILE_FIELDS[field]
    if isinstance(value, list | tuple):
        return ", ".join(format_value(field, item) for item in value)
    if spec.type is FieldType.BOOLEAN:
        return "yes" if value else "no"
    if spec.type in (FieldType.INTEGER, FieldType.NUMBER):
        if spec.unit == "INR":
            if float(value).is_integer():
                return f"INR {_indian_grouping(int(value))}"
            whole = int(value)
            return f"INR {_indian_grouping(whole)}.{round((abs(value) - abs(whole)) * 100):02d}"
        if spec.unit == "%":
            return f"{_number(value)}%"
        if spec.unit:
            return f"{_number(value)} {spec.unit}"
        return _number(value)
    text = str(value).replace("_", " ")
    return text.upper() if field in _UPPERCASE_VALUE_FIELDS else text


def _label(field: str) -> str:
    """Field label without a trailing unit: 'Annual family income (INR)' -> 'Annual family income'."""
    return PROFILE_FIELDS[field].label.split(" (")[0]


def _requirement(cond: Condition) -> str:
    """What the condition requires, in words: 'at most INR 6,00,000'."""
    shown = format_value(cond.field, cond.value)
    match cond.operator:
        case "eq":
            return shown
        case "ne":
            return f"anything except {shown}"
        case "in":
            return f"one of: {shown}"
        case "not_in":
            return f"none of: {shown}"
        case "lt":
            return f"below {shown}"
        case "lte":
            return f"at most {shown}"
        case "gt":
            return f"above {shown}"
        case "gte":
            return f"at least {shown}"
        case "between":
            low, high = cond.value
            return f"between {format_value(cond.field, low)} and {format_value(cond.field, high)}"
        case "is_true":
            return "yes"
        case "is_false":
            return "no"
    raise ValueError(f"Unknown operator '{cond.operator}'")


def _outcome_message(cond: Condition, actual: Any, outcome: Outcome) -> str:
    label = _label(cond.field)
    got = format_value(cond.field, actual)
    want = format_value(cond.field, cond.value)
    passed = outcome is Outcome.PASS
    match cond.operator:
        case "lte":
            text = f"is within the limit of {want}" if passed else f"exceeds the limit of {want}"
            message = f"{label} {got} {text}"
        case "lt":
            message = f"{label} {got} is {'below' if passed else 'not below'} {want}"
        case "gte":
            text = f"meets the minimum of {want}" if passed else f"is below the minimum of {want}"
            message = f"{label} {got} {text}"
        case "gt":
            message = f"{label} {got} is {'above' if passed else 'not above'} {want}"
        case "between":
            verdict = "within" if passed else "outside"
            message = f"{label} {got} is {verdict} the required range ({_requirement(cond)})"
        case "is_true" | "is_false":
            message = f"{label}: {got}" if passed else f"{label}: {got} (required: {_requirement(cond)})"
        case "eq" | "in":
            message = f"{label} is {got}" if passed else f"{label} is {got}; required {_requirement(cond)}"
        case _:  # ne, not_in
            message = f"{label} is {got}" if passed else f"{label} is {got}, which is excluded"
    if not passed and cond.failure_message:
        message = f"{message}. {cond.failure_message}"
    return message


def _resolution_hint(refs: Sequence[str]) -> str:
    names = [DOCUMENT_TYPES[r].label if r in DOCUMENT_TYPES else PROFILE_FIELDS[r].label for r in refs]
    return f" Provide: {', '.join(names)}." if names else ""


# --------------------------------------------------------------------------- engine


class RuleEngine:
    """Evaluate citizen profiles against versioned, validated scheme rules."""

    def __init__(
        self,
        rules_dir: str | Path | None = None,
        *,
        rules: Iterable[SchemeRule] | None = None,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be within [0, 1]")
        if rules is not None and rules_dir is not None:
            raise ValueError("Pass either rules_dir or rules, not both")
        if rules is None:
            loaded = load_rules(rules_dir if rules_dir is not None else DEFAULT_RULES_DIR)
        else:
            loaded = list(rules)
            validate_rule_set(loaded)
        self.confidence_threshold = confidence_threshold
        self._versions: dict[str, list[SchemeRule]] = {}
        for rule in sorted(loaded, key=lambda r: (r.scheme_id, r.effective_from)):
            self._versions.setdefault(rule.scheme_id, []).append(rule)

    # ---- rule lookup

    def scheme_ids(self) -> list[str]:
        """Every scheme id with at least one rule version (effective or not)."""
        return sorted(self._versions)

    def versions(self, scheme_id: str) -> list[SchemeRule]:
        """All versions of a scheme, oldest first."""
        if scheme_id not in self._versions:
            raise UnknownSchemeError(scheme_id)
        return list(self._versions[scheme_id])

    def schemes(self, as_of: date | None = None) -> list[SchemeRule]:
        """Rule versions effective on ``as_of`` (default today), one per scheme, sorted by scheme id."""
        day = as_of or date.today()
        active = (self._effective(versions, day) for versions in self._versions.values())
        return sorted((rule for rule in active if rule is not None), key=lambda r: r.scheme_id)

    def get(self, scheme_id: str, as_of: date | None = None) -> SchemeRule:
        """The version of ``scheme_id`` effective on ``as_of`` (default today)."""
        day = as_of or date.today()
        rule = self._effective(self.versions(scheme_id), day)
        if rule is None:
            raise NoEffectiveRuleError(f"No rule version of '{scheme_id}' is effective on {day.isoformat()}")
        return rule

    @staticmethod
    def _effective(versions: Sequence[SchemeRule], day: date) -> SchemeRule | None:
        for rule in reversed(versions):  # newest first; the loader forbids overlapping periods
            if rule.is_effective(day):
                return rule
        return None

    # ---- evaluation

    def evaluate(
        self, scheme_id: str, profile: Mapping[str, FieldValue], as_of: date | None = None
    ) -> SchemeEvaluation:
        day = as_of or date.today()
        return self.evaluate_rule(self.get(scheme_id, day), profile, day)

    def evaluate_all(
        self, profile: Mapping[str, FieldValue], as_of: date | None = None
    ) -> list[SchemeEvaluation]:
        """Evaluate every scheme that has a rule version effective on ``as_of``."""
        day = as_of or date.today()
        return [self.evaluate_rule(rule, profile, day) for rule in self.schemes(day)]

    def evaluate_rule(
        self, rule: SchemeRule, profile: Mapping[str, FieldValue], as_of: date
    ) -> SchemeEvaluation:
        """Evaluate one specific rule version (used by ``evaluate``; also handy for audits)."""
        values = self._normalise_profile(profile, _referenced_fields(rule.conditions))
        results = tuple(self._evaluate_item(item, values) for item in rule.conditions)
        outcomes = {r.outcome for r in results}
        if Outcome.FAIL in outcomes:
            status = EligibilityStatus.NOT_ELIGIBLE
        elif Outcome.MISSING in outcomes:
            status = EligibilityStatus.POTENTIALLY_ELIGIBLE
        else:
            status = EligibilityStatus.ELIGIBLE
        return SchemeEvaluation(
            scheme_id=rule.scheme_id,
            scheme_name=rule.name,
            rule_version=rule.version,
            rule_hash=rule.rule_hash,
            verification_status=rule.verification_status.value,
            status=status,
            conditions=results,
            input_hash=self._input_hash(rule, values, as_of),
            as_of=as_of,
            notes=self._notes(rule, values),
        )

    def _normalise_profile(
        self, profile: Mapping[str, FieldValue], fields: Sequence[str]
    ) -> dict[str, FieldValue | None]:
        """Referenced fields only, with values coerced to their declared types."""
        values: dict[str, FieldValue | None] = {}
        for name in fields:
            item = profile.get(name)
            if item is not None and not isinstance(item, FieldValue):
                raise ProfileValueError(
                    f"Profile field '{name}' must be a FieldValue, got {type(item).__name__}"
                )
            if item is None or item.value is None:
                values[name] = None
                continue
            try:
                value = coerce_value(name, item.value)
                provenance = Provenance(item.provenance)
            except ValueError as exc:
                raise ProfileValueError(f"Invalid profile value for '{name}': {exc}") from exc
            values[name] = FieldValue(value, provenance, item.confidence, item.source_document_id)
        return values

    def _input_hash(self, rule: SchemeRule, values: Mapping[str, FieldValue | None], as_of: date) -> str:
        payload = {
            "rule_hash": rule.rule_hash,
            "as_of": as_of.isoformat(),
            "confidence_threshold": self.confidence_threshold,
            "fields": {
                name: None
                if fv is None
                else {"value": fv.value, "provenance": fv.provenance.value, "confidence": fv.confidence}
                for name, fv in values.items()
            },
        }
        return sha256_hex(canonical_json(payload))

    def _evaluate_item(
        self, item: Condition | ConditionGroup, values: Mapping[str, FieldValue | None]
    ) -> ConditionResult:
        if isinstance(item, ConditionGroup):
            return self._evaluate_group(item, values)
        return self._evaluate_condition(item, values)

    def _evaluate_condition(
        self, cond: Condition, values: Mapping[str, FieldValue | None]
    ) -> ConditionResult:
        fv = values.get(cond.field)
        label = _label(cond.field)
        hint = _resolution_hint(cond.resolves_with)

        def result(outcome: Outcome, reason: MissingReason | None, message: str) -> ConditionResult:
            return ConditionResult(
                condition_id=cond.id,
                description=cond.description,
                field=cond.field,
                operator=cond.operator,
                expected=cond.value,
                actual=None if fv is None else fv.value,
                provenance=None if fv is None else fv.provenance.value,
                outcome=outcome,
                missing_reason=reason,
                message=message,
                resolves_with=cond.resolves_with,
            )

        if fv is None:
            return result(
                Outcome.MISSING,
                MissingReason.NO_VALUE,
                f"{label} is not provided; required: {_requirement(cond)}.{hint}",
            )
        shown = format_value(cond.field, fv.value)
        if TRUST[fv.provenance] < TRUST[cond.min_trust]:
            return result(
                Outcome.MISSING,
                MissingReason.INSUFFICIENT_TRUST,
                f"{label} {shown} is {_PROVENANCE_LABEL[fv.provenance]}, but this condition needs a value "
                f"that is at least {_PROVENANCE_LABEL[cond.min_trust]}.{hint}",
            )
        if fv.provenance is Provenance.DOCUMENT_EXTRACTED and (
            fv.confidence is None or fv.confidence < self.confidence_threshold
        ):
            confidence = "unknown" if fv.confidence is None else f"{fv.confidence:.2f}"
            return result(
                Outcome.MISSING,
                MissingReason.LOW_CONFIDENCE,
                f"{label} {shown} was read from a document with confidence {confidence}, below the "
                f"{self.confidence_threshold:.2f} threshold; please confirm the value or upload a "
                "clearer copy.",
            )
        outcome = apply_operator(cond.operator, fv.value, cond.value)
        return result(outcome, None, _outcome_message(cond, fv.value, outcome))

    def _evaluate_group(
        self, group: ConditionGroup, values: Mapping[str, FieldValue | None]
    ) -> ConditionResult:
        children = tuple(self._evaluate_item(item, values) for item in group.items)
        by_outcome = {o: [c for c in children if c.outcome is o] for o in Outcome}
        total = len(children)
        if group.mode is GroupMode.ALL:
            if by_outcome[Outcome.FAIL]:
                outcome, shown = Outcome.FAIL, by_outcome[Outcome.FAIL]
                summary = f"not met ({len(shown)} of {total} required conditions failed)"
            elif by_outcome[Outcome.MISSING]:
                outcome, shown = Outcome.MISSING, by_outcome[Outcome.MISSING]
                summary = (
                    f"cannot be decided yet ({len(shown)} of {total} required conditions lack usable data)"
                )
            else:
                outcome, shown = Outcome.PASS, children
                summary = f"met (all {total} conditions passed)"
        elif by_outcome[Outcome.PASS]:
            outcome, shown = Outcome.PASS, by_outcome[Outcome.PASS][:1]
            summary = f"met (alternative '{shown[0].description}' passed)"
        elif by_outcome[Outcome.MISSING]:
            outcome, shown = Outcome.MISSING, by_outcome[Outcome.MISSING]
            summary = (
                f"cannot be decided yet (no alternative passed; {len(shown)} of {total} lack usable data)"
            )
        else:
            outcome, shown = Outcome.FAIL, children
            summary = f"not met (none of the {total} alternatives passed)"
        details = "; ".join(f"[{c.description}] {c.message}" for c in shown)
        message = f"{group.description}: {summary}: {details}"
        if outcome is Outcome.FAIL and group.failure_message:
            message = f"{message}. {group.failure_message}"
        refs_from = shown if outcome is Outcome.MISSING else children
        resolves: list[str] = []
        for child in refs_from:
            resolves.extend(r for r in child.resolves_with if r not in resolves)
        fields = _referenced_fields(group.items)
        return ConditionResult(
            condition_id=group.id,
            description=group.description,
            field=", ".join(fields),
            operator=group.mode.value,
            expected=None,
            actual=None,
            provenance=None,
            outcome=outcome,
            missing_reason=None,
            message=message,
            resolves_with=tuple(resolves),
            children=children,
        )

    def _notes(self, rule: SchemeRule, values: Mapping[str, FieldValue | None]) -> tuple[str, ...]:
        notes: list[str] = []
        if rule.verification_status.value != "verified":
            notes.append(
                f"Rule evidence level is '{rule.verification_status.value}': some conditions could not be "
                "traced to an official source section; see the scheme's limitations."
            )
        for _, field_a, value_a, field_b, value_b, message in _CONTRADICTIONS:
            a, b = values.get(field_a), values.get(field_b)
            if a is not None and b is not None and a.value == value_a and b.value == value_b:
                notes.append(message)
        return tuple(notes)


def _referenced_fields(items: Iterable[Condition | ConditionGroup]) -> list[str]:
    """Distinct profile fields used by conditions, in first-use order."""
    fields: list[str] = []
    for item in items:
        nested = _referenced_fields(item.items) if isinstance(item, ConditionGroup) else [item.field]
        fields.extend(f for f in nested if f not in fields)
    return fields
