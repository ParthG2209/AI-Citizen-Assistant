"""Provenance transitions for profile values and conversion of DB rows to rule-engine inputs.

Rules (docs/backend_implementation_plan.md section 3.1):
* A value read by OCR becomes ``document_extracted`` (never ``verified``).
* A user edit that changes the value becomes ``self_declared`` and loses OCR confidence/source.
* A user edit that repeats the stored value leaves the field untouched (keeps its provenance).
* OCR never downgrades a value that has higher trust than ``document_extracted``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import Any

from ai_modules.rule_engine.fields import TRUST, FieldValue, Provenance
from app.models.citizen import ProfileField


def user_edit(current: FieldValue | None, new_value: Any) -> FieldValue | None:
    """Result of a citizen/volunteer setting ``new_value``; ``None`` means "no change needed"."""
    if current is not None and current.value == new_value:
        return None
    return FieldValue(value=new_value, provenance=Provenance.SELF_DECLARED)


def ocr_extraction(
    current: FieldValue | None, value: Any, confidence: float | None, document_id: uuid.UUID | str
) -> FieldValue | None:
    """Result of OCR reading ``value`` from a document; ``None`` means "keep the current value"."""
    if current is not None and TRUST[current.provenance] > TRUST[Provenance.DOCUMENT_EXTRACTED]:
        return None
    return FieldValue(
        value=value,
        provenance=Provenance.DOCUMENT_EXTRACTED,
        confidence=confidence,
        source_document_id=str(document_id),
    )


def field_value_from_row(row: ProfileField) -> FieldValue:
    return FieldValue(
        value=row.value,
        provenance=Provenance(row.provenance),
        confidence=row.confidence,
        source_document_id=str(row.source_document_id) if row.source_document_id else None,
    )


def field_values_from_rows(rows: Iterable[ProfileField]) -> dict[str, FieldValue]:
    """Build the ``Mapping[str, FieldValue]`` profile the rule engine evaluates."""
    return {row.field_name: field_value_from_row(row) for row in rows}


def apply_to_row(row: ProfileField, value: FieldValue) -> None:
    """Copy a ``FieldValue`` onto an ORM row (new or existing)."""
    row.value = value.value
    row.provenance = value.provenance.value
    row.confidence = value.confidence
    row.source_document_id = uuid.UUID(value.source_document_id) if value.source_document_id else None
