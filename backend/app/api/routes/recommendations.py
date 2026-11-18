"""Benefit-gap recommendations (built on deterministic rule-engine results; no LLM involvement)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_knowledge_graph, get_rule_engine
from app.schemas.common import ERROR_RESPONSES
from app.schemas.recommendation import CitizenRecommendationsOut
from app.services import recommendation_service

router = APIRouter(tags=["recommendations"], responses=ERROR_RESPONSES)


@router.get(
    "/citizens/{citizen_id}/recommendations",
    response_model=CitizenRecommendationsOut,
    summary="Unclaimed benefits, next steps, document gaps and complementary schemes",
    description="Built from a persisted evaluation run that matches the current profile (values, "
    "provenance, OCR confidence) and the rules in effect today: the latest run is reused while it is "
    "fresh, otherwise a new run is evaluated and persisted first (older runs are kept). `evaluated_now`, "
    "`stale_reasons`, `run_id` and `evaluations` (evaluation id + rule version per scheme) say which run "
    "was used. Enrollments are read live: schemes marked enrolled or applied are treated as already "
    "claimed. `graph_source` says whether complementary schemes came from Neo4j or the identical "
    "in-memory fallback.",
)
def get_recommendations(
    citizen_id: uuid.UUID,
    refresh: bool = Query(default=False, description="Evaluate and persist a new run first."),
    db: Session = Depends(get_db),
    engine: Any = Depends(get_rule_engine),
    graph: Any = Depends(get_knowledge_graph),
) -> CitizenRecommendationsOut:
    return recommendation_service.citizen_recommendations(db, engine, graph, citizen_id, refresh)
