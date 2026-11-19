"""Benefit-gap detection over rule-engine results (no LLM, no new eligibility decisions).

Classifies every MISSING leaf condition of a potentially-eligible, not-enrolled scheme into one
next-action kind:

* ``upload_document``      an OCR-supported document in the condition's ``resolves_with`` can supply
                           the value at the required trust level;
* ``provide_information``  the citizen can self-declare the value (non-OCR documents are listed as
                           supporting proof only);
* ``obtain_verification``  the rule needs a higher trust level than the citizen can provide here
                           (``verified``, or document-backed with no OCR-supported document);
* ``recheck_field``        an OCR value exists but its confidence is below the threshold.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from ai_modules.rule_engine.definitions import Condition, ConditionGroup, SchemeRule
from ai_modules.rule_engine.fields import DOCUMENT_TYPES, PROFILE_FIELDS, Provenance
from ai_modules.rule_engine.result import (
    ConditionResult,
    EligibilityStatus,
    MissingReason,
    Outcome,
    SchemeEvaluation,
)


class ActionKind(StrEnum):
    APPLY = "apply"
    UPLOAD_DOCUMENT = "upload_document"
    PROVIDE_INFORMATION = "provide_information"
    OBTAIN_VERIFICATION = "obtain_verification"
    RECHECK_FIELD = "recheck_field"


@dataclass(frozen=True, slots=True)
class MissingItem:
    """One MISSING leaf condition with its classified next action."""

    scheme_id: str
    condition_id: str
    field: str
    description: str
    reason: MissingReason | None
    kind: ActionKind
    documents: tuple[str, ...]  # upload: OCR-supported docs; otherwise: supporting / proof documents
    required_trust: str | None  # Condition.min_trust from the rule
    provenance: str | None  # provenance of the current value (None when no value)
    actual: Any = None
    can_self_declare: bool = False  # the value may alternatively be typed in (min_trust self_declared)
    any_group: str | None = None  # id of the nearest enclosing ANY group: items sharing it are alternatives


@dataclass(frozen=True, slots=True)
class DocumentGapScheme:
    scheme_id: str
    scheme_name: str
    condition_ids: tuple[str, ...]
    fields: tuple[str, ...]
    fully_unlocks: bool  # every MISSING condition of the scheme is resolved by this document alone


@dataclass(frozen=True, slots=True)
class DocumentGap:
    doc_id: str
    label: str
    schemes: tuple[DocumentGapScheme, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FieldNeed:
    """A field the citizen should provide, get verified, or re-check, across schemes."""

    kind: ActionKind
    field: str
    label: str
    scheme_ids: tuple[str, ...]
    condition_ids: tuple[str, ...]  # "scheme_id:condition_id"
    documents: tuple[str, ...]
    required_trust: str | None = None
    provenance: str | None = None
    value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GapAnalysis:
    unclaimed_eligible: tuple[str, ...]
    potentially_eligible: tuple[str, ...]  # not enrolled
    missing_by_scheme: dict[str, tuple[MissingItem, ...]]
    document_gaps: tuple[DocumentGap, ...]
    information_needs: tuple[FieldNeed, ...]
    verification_needs: tuple[FieldNeed, ...]
    low_confidence_fields: tuple[FieldNeed, ...]


def condition_index(rule: SchemeRule) -> dict[str, Condition | ConditionGroup]:
    """All conditions and groups of a rule keyed by id (ids are unique within a scheme)."""
    index: dict[str, Condition | ConditionGroup] = {}
    stack: list[Condition | ConditionGroup] = list(rule.conditions)
    while stack:
        item = stack.pop()
        index[item.id] = item
        if isinstance(item, ConditionGroup):
            stack.extend(item.items)
    return index


def children(result: ConditionResult) -> tuple[ConditionResult, ...]:
    return tuple(getattr(result, "children", ()) or ())


def missing_leaves(
    result: ConditionResult, any_group: str | None = None
) -> list[tuple[ConditionResult, str | None]]:
    """MISSING leaf results under ``result`` with the id of their nearest enclosing ANY group."""
    if result.outcome is not Outcome.MISSING:
        return []
    nested = children(result)
    if not nested:
        return [(result, any_group)]
    group = result.condition_id if result.operator == "any" else any_group
    return [pair for child in nested for pair in missing_leaves(child, group)]


def classify(result: ConditionResult, condition: Condition | ConditionGroup | None) -> ActionKind:
    docs = [d for d in result.resolves_with if d in DOCUMENT_TYPES]
    ocr_docs = [d for d in docs if DOCUMENT_TYPES[d].ocr_supported]
    required = condition.min_trust if isinstance(condition, Condition) else None
    if result.missing_reason is MissingReason.LOW_CONFIDENCE:
        return ActionKind.RECHECK_FIELD
    if required is Provenance.VERIFIED:
        return ActionKind.OBTAIN_VERIFICATION
    if ocr_docs:
        return ActionKind.UPLOAD_DOCUMENT
    if result.missing_reason is MissingReason.NO_VALUE and required in (None, Provenance.SELF_DECLARED):
        return ActionKind.PROVIDE_INFORMATION
    return ActionKind.OBTAIN_VERIFICATION


def missing_item(
    scheme_id: str,
    result: ConditionResult,
    conditions: Mapping[str, Condition | ConditionGroup],
    any_group: str | None = None,
) -> MissingItem:
    condition = conditions.get(result.condition_id)
    kind = classify(result, condition)
    docs = tuple(d for d in result.resolves_with if d in DOCUMENT_TYPES)
    if kind in (ActionKind.UPLOAD_DOCUMENT, ActionKind.RECHECK_FIELD):
        docs = tuple(d for d in docs if DOCUMENT_TYPES[d].ocr_supported)
    required = str(condition.min_trust) if isinstance(condition, Condition) else None
    return MissingItem(
        scheme_id=scheme_id,
        condition_id=result.condition_id,
        field=result.field,
        description=result.description,
        reason=result.missing_reason,
        kind=kind,
        documents=docs,
        required_trust=required,
        provenance=result.provenance,
        actual=result.actual,
        can_self_declare=kind is ActionKind.UPLOAD_DOCUMENT
        and result.missing_reason is MissingReason.NO_VALUE
        and required == Provenance.SELF_DECLARED,
        any_group=any_group,
    )


def resolved_by_document(result: ConditionResult, doc_id: str, items: Mapping[str, MissingItem]) -> bool:
    """Would uploading ``doc_id`` resolve this MISSING result (respecting ALL / ANY groups)?"""
    if result.outcome is not Outcome.MISSING:
        return True
    nested = children(result)
    if not nested:
        item = items.get(result.condition_id)
        return bool(item and item.kind is ActionKind.UPLOAD_DOCUMENT and doc_id in item.documents)
    missing_children = [c for c in nested if c.outcome is Outcome.MISSING]
    if result.operator == "any":
        return any(resolved_by_document(c, doc_id, items) for c in missing_children)
    return all(resolved_by_document(c, doc_id, items) for c in missing_children)


def detect_gaps(
    evaluations: Sequence[SchemeEvaluation],
    enrolled_scheme_ids: Iterable[str],
    rules: Mapping[str, SchemeRule],
) -> GapAnalysis:
    enrolled = set(enrolled_scheme_ids)
    unclaimed = tuple(
        sorted(
            e.scheme_id
            for e in evaluations
            if e.status is EligibilityStatus.ELIGIBLE and e.scheme_id not in enrolled
        )
    )
    potential = [
        e
        for e in evaluations
        if e.status is EligibilityStatus.POTENTIALLY_ELIGIBLE and e.scheme_id not in enrolled
    ]
    potential.sort(key=lambda e: e.scheme_id)

    missing_by_scheme: dict[str, tuple[MissingItem, ...]] = {}
    doc_rows: dict[str, list[DocumentGapScheme]] = {}
    for evaluation in potential:
        rule = rules.get(evaluation.scheme_id)
        conditions = condition_index(rule) if rule else {}
        items = [
            missing_item(evaluation.scheme_id, leaf, conditions, group)
            for top in evaluation.conditions
            for leaf, group in missing_leaves(top)
        ]
        missing_by_scheme[evaluation.scheme_id] = tuple(items)
        by_id = {item.condition_id: item for item in items}
        upload_docs = sorted({d for i in items if i.kind is ActionKind.UPLOAD_DOCUMENT for d in i.documents})
        for doc_id in upload_docs:
            resolved = [i for i in items if i.kind is ActionKind.UPLOAD_DOCUMENT and doc_id in i.documents]
            doc_rows.setdefault(doc_id, []).append(
                DocumentGapScheme(
                    scheme_id=evaluation.scheme_id,
                    scheme_name=evaluation.scheme_name,
                    condition_ids=tuple(i.condition_id for i in resolved),
                    fields=tuple(dict.fromkeys(i.field for i in resolved)),
                    fully_unlocks=all(
                        resolved_by_document(top, doc_id, by_id)
                        for top in evaluation.conditions
                        if top.outcome is Outcome.MISSING
                    ),
                )
            )

    document_gaps = tuple(
        DocumentGap(doc_id, DOCUMENT_TYPES[doc_id].label, tuple(rows))
        for doc_id, rows in sorted(doc_rows.items())
    )
    all_items = [item for items in missing_by_scheme.values() for item in items]
    return GapAnalysis(
        unclaimed_eligible=unclaimed,
        potentially_eligible=tuple(e.scheme_id for e in potential),
        missing_by_scheme=missing_by_scheme,
        document_gaps=document_gaps,
        information_needs=_field_needs(all_items, ActionKind.PROVIDE_INFORMATION),
        verification_needs=_field_needs(all_items, ActionKind.OBTAIN_VERIFICATION),
        low_confidence_fields=_field_needs(all_items, ActionKind.RECHECK_FIELD),
    )


def _field_needs(items: Sequence[MissingItem], kind: ActionKind) -> tuple[FieldNeed, ...]:
    grouped: dict[tuple[str, str | None], list[MissingItem]] = {}
    for item in items:
        if item.kind is kind:
            grouped.setdefault((item.field, item.required_trust), []).append(item)
    needs = []
    for (field_name, required), group in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        spec = PROFILE_FIELDS.get(field_name)
        needs.append(
            FieldNeed(
                kind=kind,
                field=field_name,
                label=spec.label if spec else field_name,
                scheme_ids=tuple(sorted({i.scheme_id for i in group})),
                condition_ids=tuple(sorted(f"{i.scheme_id}:{i.condition_id}" for i in group)),
                documents=tuple(sorted({d for i in group for d in i.documents})),
                required_trust=required,
                provenance=group[0].provenance,
                value=group[0].actual if kind is ActionKind.RECHECK_FIELD else None,
            )
        )
    return tuple(needs)
