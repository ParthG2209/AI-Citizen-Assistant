"""Persisted rule-engine results: one row per scheme per evaluation run, plus condition rows."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ai_modules.rule_engine.result import EligibilityStatus, MissingReason, Outcome
from app.database import Base
from app.models.common import PROVENANCE_VALUES, values_in


class EligibilityEvaluation(Base):
    __tablename__ = "eligibility_evaluations"
    __table_args__ = (
        values_in("status", EligibilityStatus, "status_valid"),
        Index("ix_eligibility_evaluations_citizen_evaluated_at", "citizen_id", "evaluated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # All schemes evaluated by one request share a run_id.
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    citizen_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("citizens.id", ondelete="CASCADE"), nullable=False
    )
    scheme_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("schemes.scheme_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    rule_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scheme_rule_versions.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    as_of: Mapped[date] = mapped_column(Date, nullable=False)
    notes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # Run-level freshness keys (same on every row of a run; NULL for runs stored before they existed):
    # sha256 of every profile value + provenance + confidence the run read (no timestamps) ...
    profile_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # ... and of the effective rule versions + engine confidence threshold that evaluated it.
    rule_set_hash: Mapped[str | None] = mapped_column(String(64))

    conditions: Mapped[list[ConditionResultRow]] = relationship(
        back_populates="evaluation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ConditionResultRow.position",
    )


class ConditionResultRow(Base):
    __tablename__ = "condition_results"
    __table_args__ = (
        values_in("outcome", Outcome, "outcome_valid"),
        values_in("missing_reason", MissingReason, "missing_reason_valid"),
        values_in("provenance", PROVENANCE_VALUES, "provenance_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    evaluation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("eligibility_evaluations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    condition_id: Mapped[str] = mapped_column(String(128), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)  # pre-order index within the evaluation
    # Set for results nested inside a condition group (engine ConditionResult.children).
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("condition_results.id", ondelete="CASCADE"), index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    field: Mapped[str] = mapped_column(Text, nullable=False)  # comma-joined field names for groups
    operator: Mapped[str] = mapped_column(String(32), nullable=False)
    expected: Mapped[Any] = mapped_column(JSONB, nullable=True)  # None for groups
    actual: Mapped[Any] = mapped_column(JSONB, nullable=True)  # None when the value is missing
    provenance: Mapped[str | None] = mapped_column(String(32))
    missing_reason: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, nullable=False)
    resolves_with: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")

    evaluation: Mapped[EligibilityEvaluation] = relationship(back_populates="conditions")
