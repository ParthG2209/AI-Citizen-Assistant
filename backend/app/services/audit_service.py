"""Write audit rows. Callers pass ids, field names and counts only -- never profile values."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditLog


def record(
    db: Session, entity_type: str, entity_id: object, action: str, details: dict[str, Any] | None = None
) -> None:
    db.add(AuditLog(entity_type=entity_type, entity_id=str(entity_id), action=action, details=details or {}))
