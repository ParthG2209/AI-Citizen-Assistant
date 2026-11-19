"""Citizen, profile, profile-catalog and enrollment schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_modules.rule_engine.fields import Provenance

EnrollmentStatus = Literal["enrolled", "applied", "not_enrolled"]


class CitizenCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str | None = Field(
        default=None,
        max_length=120,
        description="Optional non-identifying label, e.g. 'Synthetic demo: rural student'. "
        "Never a real name or ID.",
    )
    is_synthetic: bool = Field(default=True, description="True for demo / test records (all data today).")


class CitizenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str | None
    is_synthetic: bool
    created_at: datetime
    updated_at: datetime


class ProfileFieldOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    value: Any
    provenance: Provenance
    confidence: float | None
    source_document_id: uuid.UUID | None
    updated_at: datetime


class ProfileOut(BaseModel):
    citizen_id: uuid.UUID
    fields: dict[str, ProfileFieldOut]
    missing_fields: list[str] = Field(description="Catalog fields with no value yet, in catalog order.")


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, Any] = Field(
        min_length=1,
        description="Field name -> new value (validated against GET /profile-fields); null clears the field. "
        "Values entered here are recorded as self_declared.",
        examples=[{"age": 19, "gender": "female", "district": None}],
    )


class FieldSpecOut(BaseModel):
    name: str
    type: str
    label: str
    choices: list[str]
    minimum: float | None
    maximum: float | None
    unit: str | None
    sensitive: bool


class DocumentTypeOut(BaseModel):
    id: str
    label: str
    ocr_supported: bool
    provides_fields: list[str]


class ProvenanceLevelOut(BaseModel):
    id: Provenance
    trust: int


class ProfileCatalogOut(BaseModel):
    fields: list[FieldSpecOut]
    document_types: list[DocumentTypeOut]
    provenance_levels: list[ProvenanceLevelOut]


class EnrollmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: EnrollmentStatus


class EnrollmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scheme_id: str
    status: EnrollmentStatus
    provenance: Provenance
    updated_at: datetime
