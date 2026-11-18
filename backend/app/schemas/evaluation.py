"""Eligibility evaluation request / response schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

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
    provenance: Provenance = Field(
        default=Provenance.SELF_DECLARED,
        description="self_declared or document_extracted (client-asserted; the response is marked "
        "`simulation`). `verified` is rejected with 422 provenance_not_allowed.",
    )
    # strict: a JSON number only (true / "0.9" are rejected); NaN / Infinity / out of [0, 1] -> 422.
    confidence: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False, strict=True)


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
    verification_warning: str | None = Field(
        default=None,
        description="Rule-engine warning for partially verified / unverified rules (null when verified); "
        "show it next to the status.",
    )
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
    profile_fingerprint: str | None = Field(
        default=None,
        description="sha256 of every profile value + provenance + confidence the run read (no timestamps); "
        "null for runs stored before fingerprints existed.",
    )
    rule_set_hash: str | None = Field(
        default=None,
        description="sha256 of the effective rule versions + engine confidence threshold used.",
    )
    stale: bool = Field(
        default=False,
        description="GET .../evaluations/latest only: the stored run no longer matches the current profile "
        "or the rules in effect today.",
    )
    stale_reasons: list[Literal["profile_changed", "rule_version_changed"]] = Field(default_factory=list)
    simulation: bool = Field(
        default=False, description="True for stateless evaluations: caller-supplied data, nothing stored."
    )
    client_asserted_provenance: list[str] = Field(
        default_factory=list,
        description="Stateless only: fields the caller marked document_extracted without any uploaded "
        "document behind them.",
    )
    provenance_note: str | None = Field(default=None, description="Set for simulations.")


class EvaluationDetailOut(SchemeEvaluationOut):
    run_id: uuid.UUID
    citizen_id: uuid.UUID
    evaluated_at: datetime
    profile_fingerprint: str | None
    rule_set_hash: str | None
    disclaimer: str
