"""Typed mirror of ``ai_modules.recommendation.RecommendationReport.to_dict()`` plus API context."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class NextActionOut(BaseModel):
    kind: str
    text: str
    document_id: str | None
    field: str | None
    url: str | None
    condition_ids: list[str]


class BenefitSummaryOut(BaseModel):
    type: str
    description: str
    amount_inr: float | None
    frequency: str | None
    is_cash: bool


class SchemeRecommendationOut(BaseModel):
    scheme_id: str
    scheme_name: str
    status: str
    verification_status: str
    benefit: BenefitSummaryOut
    rank: int
    rank_reasons: list[str]
    next_actions: list[NextActionOut]
    application_steps: list[str]
    official_url: str | None
    warnings: list[str]


class ComplementaryLinkOut(BaseModel):
    anchor_scheme: str
    relation: str
    reason: str
    source_url: str | None
    text: str


class ConditionSummaryOut(BaseModel):
    condition_id: str
    description: str
    message: str


class ComplementaryRecommendationOut(BaseModel):
    scheme_id: str
    scheme_name: str
    presentation: Literal["available", "check_eligibility"]
    target_status: str
    via: list[ComplementaryLinkOut]
    missing_conditions: list[ConditionSummaryOut]
    failed_conditions: list[ConditionSummaryOut]
    benefit: BenefitSummaryOut | None
    rank: int
    rank_reasons: list[str]
    next_actions: list[NextActionOut]
    official_url: str | None
    warnings: list[str]


class DocumentGapSchemeOut(BaseModel):
    scheme_id: str
    scheme_name: str
    condition_ids: list[str]
    fields: list[str]
    fully_unlocks: bool


class DocumentGapOut(BaseModel):
    doc_id: str
    label: str
    schemes: list[DocumentGapSchemeOut]


class FieldNeedOut(BaseModel):
    kind: str
    field: str
    label: str
    scheme_ids: list[str]
    condition_ids: list[str]
    documents: list[str]
    required_trust: str | None
    provenance: str | None
    value: Any


class MutualExclusionWarningOut(BaseModel):
    scheme_ids: list[str]
    reason: str
    source_url: str | None
    text: str


class TotalLineOut(BaseModel):
    scheme_id: str
    category: str
    amount_inr: float | None
    frequency: str | None
    description: str


class BenefitTotalsOut(BaseModel):
    recurring_annual_cash_inr: float
    recurring_monthly_cash_inr: float
    one_time_cash_inr: float
    fee_reimbursement_inr: float
    non_cash: list[TotalLineOut]
    amount_not_published: list[str]
    not_summed: list[TotalLineOut]
    excluded_mutually_exclusive: list[list[str]] = Field(
        description="[excluded_scheme, conflicts_with] pairs"
    )
    counted_scheme_ids: list[str]
    lines: list[TotalLineOut]


class RecommendationReportOut(BaseModel):
    unclaimed: list[SchemeRecommendationOut]
    potentially_eligible: list[SchemeRecommendationOut]
    complementary: list[ComplementaryRecommendationOut]
    document_gaps: list[DocumentGapOut]
    information_needs: list[FieldNeedOut]
    verification_needs: list[FieldNeedOut]
    low_confidence_fields: list[FieldNeedOut]
    warnings: list[MutualExclusionWarningOut]
    enrolled_scheme_ids: list[str]
    unclaimed_totals: BenefitTotalsOut
    potential_totals: BenefitTotalsOut
    disclaimer: str
    notes: list[str]


class EvaluationUsedOut(BaseModel):
    scheme_id: str
    evaluation_id: uuid.UUID
    rule_version: str
    rule_hash: str
    status: str


class CitizenRecommendationsOut(RecommendationReportOut):
    citizen_id: uuid.UUID
    run_id: uuid.UUID = Field(description="Persisted evaluation run the recommendations are based on.")
    as_of: date
    evaluated_at: datetime
    evaluated_now: bool = Field(
        description="True when this request had to run (and persist) a new evaluation."
    )
    stale_reasons: list[Literal["profile_changed", "rule_version_changed"]] = Field(
        description="Why the previous run could not be reused (it is kept in the history); empty when the "
        "latest run was still fresh, none existed, or refresh=true."
    )
    profile_fingerprint: str = Field(description="Profile fingerprint of the run used (matches the profile).")
    rule_set_hash: str = Field(description="Rule-set identity of the run used (matches the loaded rules).")
    evaluations: list[EvaluationUsedOut] = Field(
        description="Exact persisted evaluation and rule version used for each scheme."
    )
    profile_changed_since_evaluation: bool = Field(
        description="Deprecated, always false: stale runs are now re-evaluated automatically."
    )
    graph_source: Literal["neo4j", "fallback"]
