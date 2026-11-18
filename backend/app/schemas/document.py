"""Document upload / metadata schemas. File content and raw OCR text are never returned or stored."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_modules.rule_engine.fields import Provenance


class ExtractedFieldOut(BaseModel):
    name: str
    value: Any = Field(description="Null when the text was found but failed validation.")
    confidence: float
    needs_review: bool
    validation_errors: list[str]


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    citizen_id: uuid.UUID
    document_type: str | None
    original_filename: str = Field(description="Sanitized basename; identity-number-like digit runs masked.")
    content_type: str = Field(description="Detected from the file's magic bytes, not the client header.")
    size_bytes: int
    sha256: str
    status: str
    ocr_engine: str | None
    mean_confidence: float | None
    classification_confidence: float | None
    page_count: int | None
    extracted_fields: list[ExtractedFieldOut]
    missing_fields: list[str]
    warnings: list[str]
    error_code: str | None
    created_at: datetime


class AppliedField(BaseModel):
    field: str
    value: Any
    confidence: float
    previous_provenance: Provenance | None = Field(
        description="Provenance before this upload; null if absent."
    )


class ReviewField(BaseModel):
    field: str
    value: Any = Field(description="Null when validation failed; confirm or correct via PATCH profile.")
    confidence: float
    validation_errors: list[str]


class ConflictField(BaseModel):
    field: str
    extracted_value: Any
    confidence: float
    current_value: Any
    current_provenance: Provenance


class DocumentUploadOut(BaseModel):
    document: DocumentOut
    duplicate: bool = Field(description="True when this exact file was already uploaded for the citizen.")
    applied_fields: list[AppliedField] = Field(description="Profile values set to document_extracted.")
    review_required: list[ReviewField] = Field(
        description="Low-confidence or invalid values, kept on the document until the user confirms them."
    )
    conflicts: list[ConflictField] = Field(
        description="Values that differ from what the user entered (or a verified value); not applied."
    )
    missing_fields: list[str]
    warnings: list[str]


class DocumentDeleteOut(BaseModel):
    document_id: uuid.UUID
    removed_profile_fields: list[str] = Field(description="Profile values that were still sourced from it.")
