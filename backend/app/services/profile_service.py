"""Citizens, typed profile fields (with provenance transitions) and self-declared enrollments."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai_modules.rule_engine.fields import (
    DOCUMENT_TYPES,
    PROFILE_FIELDS,
    TRUST,
    FieldType,
    coerce_value,
)
from app.errors import ApiError, not_found
from app.models.citizen import BenefitEnrollment, Citizen, ProfileField
from app.models.scheme import Scheme
from app.schemas.citizen import (
    CitizenCreate,
    DocumentTypeOut,
    FieldSpecOut,
    ProfileCatalogOut,
    ProfileFieldOut,
    ProfileOut,
    ProvenanceLevelOut,
)
from app.services import audit_service
from app.utils.logger import get_logger
from app.utils.provenance import apply_to_row, field_value_from_row, user_edit

logger = get_logger(__name__)

# 9+ digits (spaces/hyphens allowed between them): Aadhaar (12) / Samagra (8-9) style identity numbers.
ID_NUMBER = re.compile(r"\d(?:[\s-]?\d){8,}")


class InvalidProfileValue(ValueError):
    def __init__(self, message: str, error_type: str = "profile_field") -> None:
        super().__init__(message)
        self.error_type = error_type


def contains_id_number(text: str) -> bool:
    return ID_NUMBER.search(text) is not None


def check_profile_value(name: str, value: Any) -> Any:
    """Coerce one value with ``coerce_value`` and reject identity numbers in text fields."""
    try:
        coerced = coerce_value(name, value)
    except ValueError as exc:
        raise InvalidProfileValue(str(exc)) from exc
    if PROFILE_FIELDS[name].type is FieldType.STRING and contains_id_number(coerced):
        raise InvalidProfileValue(
            f"Field '{name}' looks like an identity number (Aadhaar / Samagra ID); "
            "identity numbers must not be stored",
            "sensitive_identifier",
        )
    return coerced


def validate_profile_values(
    raw: Mapping[str, Any], loc: tuple[str, ...] = ("body", "fields"), allow_null: bool = True
) -> dict[str, Any]:
    """Validate every value with ``check_profile_value``; raise one 422 listing all invalid fields.

    ``None`` is kept (meaning "clear this field") when ``allow_null`` is true. Error details carry
    the field name and reason only, never the rejected value.
    """
    clean: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    for name, value in raw.items():
        if value is None and allow_null and name in PROFILE_FIELDS:
            clean[name] = None
            continue
        try:
            clean[name] = check_profile_value(name, value)
        except InvalidProfileValue as exc:
            errors.append({"loc": [*loc, name], "msg": str(exc), "type": exc.error_type})
    if errors:
        raise ApiError(422, "validation_error", "Profile validation failed", errors)
    return clean


def profile_catalog() -> ProfileCatalogOut:
    return ProfileCatalogOut(
        fields=[
            FieldSpecOut(
                name=spec.name,
                type=spec.type.value,
                label=spec.label,
                choices=list(spec.choices),
                minimum=spec.minimum,
                maximum=spec.maximum,
                unit=spec.unit,
                sensitive=spec.sensitive,
            )
            for spec in PROFILE_FIELDS.values()
        ],
        document_types=[
            DocumentTypeOut(
                id=doc.id,
                label=doc.label,
                ocr_supported=doc.ocr_supported,
                provides_fields=list(doc.provides_fields),
            )
            for doc in DOCUMENT_TYPES.values()
        ],
        provenance_levels=[ProvenanceLevelOut(id=p, trust=int(level)) for p, level in TRUST.items()],
    )


def create_citizen(db: Session, data: CitizenCreate) -> Citizen:
    citizen = Citizen(label=data.label, is_synthetic=data.is_synthetic)
    db.add(citizen)
    db.flush()
    audit_service.record(db, "citizen", citizen.id, "create", {"is_synthetic": citizen.is_synthetic})
    db.commit()
    db.refresh(citizen)
    logger.info("citizen_created", extra={"citizen_id": str(citizen.id)})
    return citizen


def get_citizen(db: Session, citizen_id: uuid.UUID) -> Citizen:
    citizen = db.get(Citizen, citizen_id)
    if citizen is None:
        raise not_found("citizen", citizen_id)
    return citizen


def delete_citizen(db: Session, citizen_id: uuid.UUID) -> None:
    """Hard delete; profile, documents, evaluations and enrollments cascade in the database."""
    citizen = get_citizen(db, citizen_id)
    db.delete(citizen)
    audit_service.record(db, "citizen", citizen_id, "delete", {"is_synthetic": citizen.is_synthetic})
    db.commit()
    logger.info("citizen_deleted", extra={"citizen_id": str(citizen_id)})


def _profile_rows(db: Session, citizen_id: uuid.UUID) -> dict[str, ProfileField]:
    rows = db.scalars(select(ProfileField).where(ProfileField.citizen_id == citizen_id))
    return {row.field_name: row for row in rows}


def get_profile(db: Session, citizen_id: uuid.UUID) -> ProfileOut:
    get_citizen(db, citizen_id)
    rows = _profile_rows(db, citizen_id)
    ordered = [name for name in PROFILE_FIELDS if name in rows] + sorted(set(rows) - set(PROFILE_FIELDS))
    return ProfileOut(
        citizen_id=citizen_id,
        fields={name: ProfileFieldOut.model_validate(rows[name]) for name in ordered},
        missing_fields=[name for name in PROFILE_FIELDS if name not in rows],
    )


def update_profile(db: Session, citizen_id: uuid.UUID, raw_fields: Mapping[str, Any]) -> ProfileOut:
    """Apply user edits: changed values become self_declared, repeated values keep their provenance."""
    citizen = get_citizen(db, citizen_id)
    values = validate_profile_values(raw_fields)
    rows = _profile_rows(db, citizen_id)
    changed: list[dict[str, str | None]] = []
    cleared: list[str] = []
    unchanged: list[str] = []

    for name, value in values.items():
        row = rows.get(name)
        if value is None:
            if row is not None:
                db.delete(row)
                cleared.append(name)
            continue
        current = field_value_from_row(row) if row is not None else None
        new = user_edit(current, value)
        if new is None:
            unchanged.append(name)
            continue
        if row is None:
            row = ProfileField(citizen_id=citizen_id, field_name=name)
            db.add(row)
        changed.append(
            {"field": name, "from": current.provenance.value if current else None, "to": new.provenance}
        )
        apply_to_row(row, new)

    if changed or cleared:
        citizen.updated_at = func.now()
        audit_service.record(
            db, "citizen", citizen_id, "profile_update", {"changed": changed, "cleared": cleared}
        )
    db.commit()
    logger.info(
        "profile_updated",
        extra={
            "citizen_id": str(citizen_id),
            "changed_fields": [c["field"] for c in changed],
            "cleared_fields": cleared,
            "unchanged_fields": unchanged,
        },
    )
    return get_profile(db, citizen_id)


def set_enrollment(db: Session, citizen_id: uuid.UUID, scheme_id: str, status: str) -> BenefitEnrollment:
    get_citizen(db, citizen_id)
    if db.get(Scheme, scheme_id) is None:
        raise not_found("scheme", scheme_id)
    enrollment = db.scalar(
        select(BenefitEnrollment).where(
            BenefitEnrollment.citizen_id == citizen_id, BenefitEnrollment.scheme_id == scheme_id
        )
    )
    if enrollment is None:
        enrollment = BenefitEnrollment(citizen_id=citizen_id, scheme_id=scheme_id, provenance="self_declared")
        db.add(enrollment)
    enrollment.status = status
    audit_service.record(
        db, "citizen", citizen_id, "enrollment_update", {"scheme_id": scheme_id, "status": status}
    )
    db.commit()
    db.refresh(enrollment)
    return enrollment


def list_enrollments(db: Session, citizen_id: uuid.UUID) -> list[BenefitEnrollment]:
    get_citizen(db, citizen_id)
    return list(
        db.scalars(
            select(BenefitEnrollment)
            .where(BenefitEnrollment.citizen_id == citizen_id)
            .order_by(BenefitEnrollment.scheme_id)
        )
    )
