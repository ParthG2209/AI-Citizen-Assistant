"""Scheme rule schemas (read-only views of ``SchemeRule`` objects loaded by the rule engine)."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel


class SourceOut(BaseModel):
    title: str
    url: str
    publisher: str
    accessed_on: date
    section: str | None


class BenefitOut(BaseModel):
    type: str
    description: str
    amount_inr: float | None
    frequency: str | None
    is_cash: bool


class RelationshipOut(BaseModel):
    scheme_id: str
    relation: str
    reason: str
    source_url: str | None


class ConditionOut(BaseModel):
    kind: Literal["condition"] = "condition"
    id: str
    description: str
    field: str
    operator: str
    value: Any
    min_trust: str
    resolves_with: list[str]
    failure_message: str | None
    source_ref: str | None


class ConditionGroupOut(BaseModel):
    kind: Literal["group"] = "group"
    id: str
    description: str
    mode: str
    items: list[ConditionOut | ConditionGroupOut]
    failure_message: str | None
    source_ref: str | None


class SchemeSummary(BaseModel):
    scheme_id: str
    name: str
    level: str
    state: str | None
    department: str
    version: str
    effective_from: date
    effective_to: date | None
    verification_status: str
    benefit: BenefitOut
    required_documents: list[str]


class SchemeDetail(SchemeSummary):
    rule_hash: str
    conditions: list[ConditionOut | ConditionGroupOut]
    sources: list[SourceOut]
    relationships: list[RelationshipOut]
    limitations: list[str]
    application_steps: list[str]
