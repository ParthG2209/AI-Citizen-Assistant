"""FastAPI dependencies shared by the routers."""

from __future__ import annotations

from typing import Any

from fastapi import Request

from app.database import get_db
from app.errors import ApiError
from app.services.scheme_service import require_engine

__all__ = ["get_db", "get_knowledge_graph", "get_rule_engine"]


def get_rule_engine(request: Request) -> Any:
    """The ``RuleEngine`` loaded once at startup (503 ``rule_engine_unavailable`` if loading failed)."""
    return require_engine(getattr(request.app.state, "rule_engine", None))


def get_knowledge_graph(request: Request) -> Any:
    """The ``KnowledgeGraph`` connected at startup (Neo4j or in-memory fallback)."""
    graph = getattr(request.app.state, "knowledge_graph", None)
    if graph is None:
        raise ApiError(
            503, "knowledge_graph_unavailable", "The knowledge graph is not loaded; see GET /api/v1/ready"
        )
    return graph
