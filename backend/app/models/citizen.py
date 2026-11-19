"""Citizens, their typed profile fields (with provenance) and self-declared scheme enrollments."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.common import PROVENANCE_VALUES, values_in

ENROLLMENT_STATUSES = ("enrolled", "applied", "not_enrolled")


class Citizen(Base):
    __tablename__ = "citizens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Optional non-identifying label (e.g. "Synthetic demo: rural student"); never a real name or ID.
    label: Mapped[str | None] = mapped_column(String(120))
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    profile_fields: Mapped[list[ProfileField]] = relationship(
        back_populates="citizen", cascade="all, delete-orphan", passive_deletes=True
    )


class ProfileField(Base):
    __tablename__ = "profile_fields"
    __table_args__ = (
        UniqueConstraint("citizen_id", "field_name", name="uq_profile_fields_citizen_field"),
        values_in("provenance", PROVENANCE_VALUES, "provenance_valid"),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="confidence_range"
        ),
        # A value read from a document must point at that document (until the document is deleted).
        CheckConstraint(
            "provenance = 'document_extracted' OR source_document_id IS NULL",
            name="source_document_provenance",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    citizen_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("citizens.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float)
    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("documents.id", ondelete="SET NULL"), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    citizen: Mapped[Citizen] = relationship(back_populates="profile_fields")


class BenefitEnrollment(Base):
    __tablename__ = "benefit_enrollments"
    __table_args__ = (
        UniqueConstraint("citizen_id", "scheme_id", name="uq_benefit_enrollments_citizen_scheme"),
        values_in("status", ENROLLMENT_STATUSES, "status_valid"),
        # Enrollment status is only ever reported by the citizen / volunteer today.
        values_in("provenance", ("self_declared",), "provenance_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    citizen_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("citizens.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scheme_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("schemes.scheme_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="self_declared")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
