"""Liveness and readiness probes."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.database import get_engine
from app.services.explanation_service import rag_status
from app.services.rules_state import ensure_rules_synced

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness probe")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _check_database() -> dict[str, Any]:
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # any driver/network error means "not ready"
        return {"status": "error", "error": type(exc).__name__}
    return {"status": "ok"}


def _check_neo4j() -> dict[str, Any]:
    settings = get_settings()
    try:
        from neo4j import GraphDatabase

        with GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password.get_secret_value()),
            connection_timeout=2,
        ) as driver:
            driver.verify_connectivity()
    except Exception as exc:
        return {"status": "unreachable", "error": type(exc).__name__}
    return {"status": "reachable"}


def _check_ollama() -> dict[str, Any]:
    settings = get_settings()
    try:
        response = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=2)
        response.raise_for_status()
        models = {m.get("name") for m in response.json().get("models", [])}
    except Exception as exc:
        return {"status": "unreachable", "error": type(exc).__name__}
    return {
        "status": "reachable",
        "model": settings.ollama_model,
        "model_available": settings.ollama_model in models,
    }


def _check_knowledge_graph(request: Request) -> dict[str, Any]:
    graph = getattr(request.app.state, "knowledge_graph", None)
    if graph is None:
        return {"status": "unavailable"}
    return {"status": "ok", "source": graph.source}


@router.get(
    "/ready",
    summary="Readiness probe",
    description="200 when PostgreSQL is reachable and the rule engine is loaded and synced; 503 otherwise. "
    "Optional services (Neo4j, Ollama, knowledge graph, RAG index) are reported but never fail readiness.",
)
def ready(request: Request) -> JSONResponse:
    database = _check_database()
    rules = ensure_rules_synced(request.app) if database["status"] == "ok" else request.app.state.rules_status
    checks = {
        "database": database,
        "rule_engine": rules,
        "neo4j": _check_neo4j(),
        "ollama": _check_ollama(),
        "knowledge_graph": _check_knowledge_graph(request),
        "rag": rag_status(),
    }
    is_ready = database["status"] == "ok" and rules["status"] == "ok"
    return JSONResponse(
        status_code=200 if is_ready else 503,
        content={"status": "ready" if is_ready else "not_ready", "checks": checks},
    )
