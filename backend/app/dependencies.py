"""FastAPI dependencies shared by the routers."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.database import get_db
from app.services.scheme_service import require_engine

__all__ = ["get_db", "get_rule_engine"]


def get_rule_engine(request: Request) -> Any:
    """The ``RuleEngine`` loaded once at startup (503 ``rule_engine_unavailable`` if loading failed)."""
    return require_engine(getattr(request.app.state, "rule_engine", None))
