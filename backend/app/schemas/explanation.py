"""Grounded explanation / scheme Q&A schemas (RAG). The decision always comes from the rule engine."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.evaluation import ProfileValueIn

Language = Literal["en", "hi"]


class ExplanationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str | None = Field(default=None, max_length=500, description="Optional follow-up question.")
    language: Language = "en"


class StatelessExplanationRequest(ExplanationRequest):
    scheme_id: str
    profile: dict[str, ProfileValueIn] = Field(description="Same shape as POST /eligibility/evaluate.")
    as_of: date | None = None


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=500)
    language: Language = "en"


class CitationOut(BaseModel):
    marker: int = Field(description="The [n] marker used in the answer.")
    chunk_id: str
    doc_id: str
    source_title: str
    url: str
    page: int | None
    section: str | None
    score: float
    excerpt: str


class ExplanationOut(BaseModel):
    answer: str
    decision: str | None = Field(
        description="Rule-engine status (null for general Q&A); never set by the LLM."
    )
    citations: list[CitationOut]
    grounded: bool
    provider: str = Field(description="ollama | gemini | none (extractive / refusal, no LLM text).")
    model: str | None
    uncertainty: str | None
    language: str
    scheme_id: str | None
    refused: bool
    flags: list[str]
    latency_ms: int
    rule_version: str
    rule_hash: str
    evaluation_id: uuid.UUID | None = Field(
        description="Persisted evaluation explained (citizen endpoint only)."
    )
    run_id: uuid.UUID | None
    evaluated_at: datetime | None
    evaluated_now: bool | None = Field(
        description="True when an evaluation had to be run and persisted first."
    )
    disclaimer: str
