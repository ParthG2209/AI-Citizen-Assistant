"""Deterministic eligibility evaluation (rule engine only; no LLM involvement)."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_db, get_rule_engine
from app.schemas.common import ERROR_RESPONSES
from app.schemas.evaluation import (
    EvaluationDetailOut,
    EvaluationRequest,
    EvaluationRunOut,
    StatelessEvaluationRequest,
)
from app.services import eligibility_service

router = APIRouter(tags=["eligibility"], responses=ERROR_RESPONSES)


@router.post(
    "/citizens/{citizen_id}/evaluations",
    response_model=EvaluationRunOut,
    status_code=201,
    summary="Evaluate a stored citizen against all effective schemes",
    description="Runs the rule engine on the stored profile, persists one evaluation per scheme with "
    "condition-level results in a single transaction, and returns them grouped by status.",
)
def create_evaluation(
    citizen_id: uuid.UUID,
    data: EvaluationRequest | None = None,
    db: Session = Depends(get_db),
    engine: Any = Depends(get_rule_engine),
) -> EvaluationRunOut:
    as_of = data.as_of if data else None
    return eligibility_service.evaluate_citizen(db, engine, citizen_id, as_of)


@router.get(
    "/citizens/{citizen_id}/evaluations/latest",
    response_model=EvaluationRunOut,
    summary="Most recent persisted evaluation run",
)
def latest_evaluation(citizen_id: uuid.UUID, db: Session = Depends(get_db)) -> EvaluationRunOut:
    return eligibility_service.latest_run(db, citizen_id)


@router.get(
    "/evaluations/{evaluation_id}", response_model=EvaluationDetailOut, summary="One scheme evaluation"
)
def get_evaluation(evaluation_id: uuid.UUID, db: Session = Depends(get_db)) -> EvaluationDetailOut:
    return eligibility_service.get_evaluation(db, evaluation_id)


@router.post(
    "/eligibility/evaluate",
    response_model=EvaluationRunOut,
    summary="Stateless evaluation of a supplied profile",
    description="Nothing is stored. Each field carries its own provenance and optional OCR confidence.",
)
def evaluate_stateless(
    data: StatelessEvaluationRequest, engine: Any = Depends(get_rule_engine)
) -> EvaluationRunOut:
    return eligibility_service.evaluate_stateless(engine, data.profile, data.as_of)
