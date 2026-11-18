"""Grounded explanations (RAG over official scheme documents). The rule engine decides; the LLM explains."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_explainer, get_rule_engine
from app.schemas.common import ERROR_RESPONSES, ErrorResponse
from app.schemas.explanation import (
    AskRequest,
    ExplanationOut,
    ExplanationRequest,
    StatelessExplanationRequest,
)
from app.services import explanation_service

RESPONSES: dict[int | str, dict[str, Any]] = {
    **ERROR_RESPONSES,
    503: {"model": ErrorResponse, "description": "rag_unavailable: run scripts/build_vector_index.py"},
}
router = APIRouter(tags=["explanations"], responses=RESPONSES)

# Sync endpoints: FastAPI runs them in its threadpool, so slow LLM calls (seconds) never block the loop.


@router.post(
    "/citizens/{citizen_id}/schemes/{scheme_id}/explanation",
    response_model=ExplanationOut,
    summary="Explain the citizen's eligibility decision for a scheme, with citations",
    description="Uses the latest persisted evaluation run while it matches the stored profile and the "
    "rules in effect today; otherwise (or if none exists) a new run is evaluated and persisted first "
    "(`evaluated_now`, `stale_reasons`). `decision` is always the rule-engine status of `evaluation_id`; "
    "the answer cites official documents.",
)
def explain_for_citizen(
    citizen_id: uuid.UUID,
    scheme_id: str,
    data: ExplanationRequest | None = None,
    db: Session = Depends(get_db),
    engine: Any = Depends(get_rule_engine),
    explainer: Any = Depends(get_explainer),
) -> ExplanationOut:
    data = data or ExplanationRequest()
    return explanation_service.explain_for_citizen(
        db, engine, explainer, citizen_id, scheme_id, data.question, data.language
    )


@router.post(
    "/explain",
    response_model=ExplanationOut,
    summary="Evaluate a supplied profile for one scheme and explain it (nothing persisted)",
    description="Same profile rules as POST /eligibility/evaluate: `verified` provenance is rejected "
    "(422 provenance_not_allowed) and the response is marked `simulation: true`.",
)
def explain_stateless(
    data: StatelessExplanationRequest,
    engine: Any = Depends(get_rule_engine),
    explainer: Any = Depends(get_explainer),
) -> ExplanationOut:
    return explanation_service.explain_stateless(
        engine, explainer, data.scheme_id, data.profile, data.as_of, data.question, data.language
    )


@router.post(
    "/schemes/{scheme_id}/ask",
    response_model=ExplanationOut,
    summary="General question about a scheme, answered from its official documents",
    description="No personal decision (`decision` is null); off-topic or injection attempts are refused.",
)
def ask(
    scheme_id: str,
    data: AskRequest,
    engine: Any = Depends(get_rule_engine),
    explainer: Any = Depends(get_explainer),
) -> ExplanationOut:
    return explanation_service.ask(engine, explainer, scheme_id, data.question, data.language)
