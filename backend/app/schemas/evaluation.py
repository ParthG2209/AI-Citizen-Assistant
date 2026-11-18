"""Eligibility evaluation request / response schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_modules.rule_engine.fields import Provenance


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    as_of: date | None = Field(
        default=None, description="Evaluate rules effective on this date (default: today)."
    )


class ProfileValueIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: Any
    provenance: Provenance = Provenance.SELF_DECLARED
    confidence: float | None = Field(default=None, ge=0, le=1)


class StatelessEvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: dict[str, ProfileValueIn] = Field(
        description="Field name -> value with provenance. Validated against GET /profile-fields.",
        examples=[
            {
                "age": {"value": 19, "provenance": "self_declared"},
                "annual_family_income": {
                    "value": 180000,
                    "provenance": "document_extracted",
                    "confidence": 0.94,
                },
            }
        ],
    )
    as_of: date | None = None


class ConditionResultOut(BaseModel):
    condition_id: str
    description: str
    field: str
    operator: str
    expected: Any
    actual: Any
    provenance: Provenance | None
    outcome: str
    missing_reason: str | None
    message: str
    resolves_with: list[str]
    children: list[ConditionResultOut] = Field(default_factory=list)


class SchemeEvaluationOut(BaseModel):
    id: uuid.UUID | None = Field(description="Persisted evaluation id; null for stateless evaluations.")
    scheme_id: str
    scheme_name: str
    rule_version: str
    rule_hash: str
    verification_status: str
    status: str
    input_hash: str
    as_of: date
    notes: list[str]
    conditions: list[ConditionResultOut]


class EvaluationSummary(BaseModel):
    eligible: int
    potentially_eligible: int
    not_eligible: int


class EvaluationRunOut(BaseModel):
    run_id: uuid.UUID | None = Field(
        description="Shared id of the persisted run; null for stateless evaluations."
    )
    citizen_id: uuid.UUID | None
    as_of: date
    evaluated_at: datetime
    summary: EvaluationSummary
    eligible: list[SchemeEvaluationOut]
    potentially_eligible: list[SchemeEvaluationOut]
    not_eligible: list[SchemeEvaluationOut]
    disclaimer: str


class EvaluationDetailOut(SchemeEvaluationOut):
    run_id: uuid.UUID
    citizen_id: uuid.UUID
    evaluated_at: datetime
    disclaimer: str
