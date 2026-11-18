"""Schemes and immutable, versioned snapshots of the rule definitions used for evaluations."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ai_modules.rule_engine.definitions import SchemeLevel, VerificationStatus
from app.database import Base
from app.models.common import values_in


class Scheme(Base):
    __tablename__ = "schemes"
    __table_args__ = (values_in("level", SchemeLevel, "level_valid"),)

    scheme_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str | None] = mapped_column(String(64))
    department: Mapped[str] = mapped_column(String(255), nullable=False)
    benefit: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # False when the scheme is no longer present in the loaded rule set (history is kept).
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class SchemeRuleVersion(Base):
    __tablename__ = "scheme_rule_versions"
    __table_args__ = (
        UniqueConstraint("scheme_id", "version", name="uq_scheme_rule_versions_scheme_version"),
        values_in("verification_status", VerificationStatus, "verification_status_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scheme_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("schemes.scheme_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    effective_to: Mapped[date | None] = mapped_column(Date)
    rule_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    definition: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    loaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
