"""Uploaded-document metadata. File bytes, file paths and raw OCR text are never stored."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.common import values_in

DOCUMENT_STATUSES = ("uploaded", "processing", "processed", "failed")


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        values_in("status", DOCUMENT_STATUSES, "status_valid"),
        CheckConstraint("size_bytes >= 0", name="size_non_negative"),
        CheckConstraint(
            "mean_confidence IS NULL OR (mean_confidence >= 0 AND mean_confidence <= 1)",
            name="mean_confidence_range",
        ),
        CheckConstraint("char_length(sha256) = 64", name="sha256_length"),
        CheckConstraint(
            "classification_confidence IS NULL OR "
            "(classification_confidence >= 0 AND classification_confidence <= 1)",
            name="classification_confidence_range",
        ),
        CheckConstraint("page_count IS NULL OR page_count >= 0", name="page_count_non_negative"),
        # Re-uploading the same file for the same citizen returns the existing document.
        UniqueConstraint("citizen_id", "sha256", name="uq_documents_citizen_sha256"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    citizen_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("citizens.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # DOCUMENT_TYPES id (declared by the user or classified by OCR); NULL until known.
    document_type: Mapped[str | None] = mapped_column(String(64))
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)  # sanitized basename
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="uploaded", server_default="uploaded"
    )
    ocr_engine: Mapped[str | None] = mapped_column(String(128))
    mean_confidence: Mapped[float | None] = mapped_column(Float)
    classification_confidence: Mapped[float | None] = mapped_column(Float)
    page_count: Mapped[int | None] = mapped_column(Integer)
    # [{name, value, confidence, needs_review, validation_errors}]: extracted values only, no raw text.
    extracted_fields: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Fields this document type should provide but OCR could not find.
    missing_fields: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
