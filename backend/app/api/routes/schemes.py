"""Scheme rules loaded by the rule engine (read-only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from app.dependencies import get_rule_engine
from app.schemas.common import ERROR_RESPONSES
from app.schemas.scheme import SchemeDetail, SchemeSummary
from app.services import scheme_service

router = APIRouter(prefix="/schemes", tags=["schemes"], responses=ERROR_RESPONSES)


@router.get(
    "", response_model=list[SchemeSummary], summary="List schemes with benefit and verification status"
)
def list_schemes(engine: Any = Depends(get_rule_engine)) -> list[SchemeSummary]:
    return [scheme_service.scheme_summary(rule) for rule in engine.schemes()]


@router.get(
    "/{scheme_id}",
    response_model=SchemeDetail,
    summary="Full scheme rule",
    description="Conditions, official sources, required documents, limitations and application steps.",
)
def get_scheme(scheme_id: str, engine: Any = Depends(get_rule_engine)) -> SchemeDetail:
    return scheme_service.scheme_detail(scheme_service.get_rule(engine, scheme_id))
