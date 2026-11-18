"""Process-wide AI components loaded once at startup: the rule engine (synced into the database)
and the knowledge graph (Neo4j, or the in-memory fallback when Neo4j is unavailable)."""

from __future__ import annotations

import threading
from typing import Any

from fastapi import FastAPI

from app.config import get_settings
from app.database import get_sessionmaker
from app.services.scheme_service import all_rule_versions, sync_rules
from app.utils.logger import get_logger

logger = get_logger(__name__)
_sync_lock = threading.Lock()


def load_rule_engine(app: FastAPI) -> None:
    """Set ``app.state.rule_engine`` (or ``None`` plus an error status if loading fails)."""
    app.state.rule_engine = None
    app.state.rules_synced = False
    try:
        from ai_modules.rule_engine import RuleEngine

        settings = get_settings()
        app.state.rule_engine = RuleEngine(rules_dir=settings.rules_dir)
    except Exception as exc:  # the API still serves profile endpoints; /ready reports the failure
        logger.error("rule_engine_load_failed", extra={"error_type": type(exc).__name__, "error": str(exc)})
        app.state.rules_status = {"status": "error", "error": f"rule engine failed to load: {exc}"}
        return
    count = len(app.state.rule_engine.scheme_ids())
    logger.info("rule_engine_loaded", extra={"schemes": count})
    app.state.rules_status = {
        "status": "error",
        "schemes": count,
        "error": "rules not yet synced to the database",
    }


def ensure_rules_synced(app: FastAPI) -> dict[str, Any]:
    """Upsert schemes + rule versions once (retried on later calls if the database was down)."""
    engine = app.state.rule_engine
    if engine is None or app.state.rules_synced:
        return app.state.rules_status
    with _sync_lock:
        if app.state.rules_synced:
            return app.state.rules_status
        session = get_sessionmaker()()
        try:
            result = sync_rules(session, all_rule_versions(engine))
        except Exception as exc:
            session.rollback()
            logger.error("rules_sync_failed", extra={"error_type": type(exc).__name__, "error": str(exc)})
            app.state.rules_status = {
                "status": "error",
                "schemes": len(engine.scheme_ids()),
                "error": f"rules not synced to the database: {type(exc).__name__}: {exc}",
            }
        else:
            app.state.rules_synced = True
            app.state.rules_status = {"status": "ok", **result}
        finally:
            session.close()
    return app.state.rules_status


def connect_knowledge_graph(app: FastAPI) -> None:
    """Set ``app.state.knowledge_graph``; never raises (falls back to the in-memory graph, then None)."""
    app.state.knowledge_graph = None
    engine = app.state.rule_engine
    if engine is None:
        return
    try:
        from ai_modules.knowledge_graph import KnowledgeGraph, Neo4jSettings

        settings = get_settings()
        neo4j = Neo4jSettings(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password.get_secret_value(),
        )
        try:
            graph = KnowledgeGraph.connect(
                rules=engine.schemes(), settings=neo4j, use_neo4j=settings.kg_use_neo4j
            )
        except Exception as exc:
            logger.error("knowledge_graph_connect_failed", extra={"error_type": type(exc).__name__})
            graph = KnowledgeGraph.connect(rules=engine.schemes(), use_neo4j=False)
    except Exception as exc:
        logger.error(
            "knowledge_graph_unavailable", extra={"error_type": type(exc).__name__, "error": str(exc)}
        )
        return
    app.state.knowledge_graph = graph
    logger.info("knowledge_graph_ready", extra={"graph_source": graph.source})


def close_knowledge_graph(app: FastAPI) -> None:
    graph = getattr(app.state, "knowledge_graph", None)
    if graph is not None:
        graph.close()
        app.state.knowledge_graph = None
