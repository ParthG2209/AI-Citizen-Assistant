"""Typed, immutable scheme-rule definitions (shared contract).

``loader.py`` parses and validates YAML into these objects; ``engine.py`` evaluates them;
the knowledge graph, recommendation and RAG modules read them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from .fields import Provenance


class SchemeLevel(StrEnum):
    CENTRAL = "central"
    STATE = "state"


class VerificationStatus(StrEnum):
    VERIFIED = "verified"  # every condition traced to an official source section
    PARTIALLY_VERIFIED = "partially_verified"  # some conditions rely on secondary official summaries
    UNVERIFIED = "unverified"  # demo only; must not be presented as authoritative


class BenefitType(StrEnum):
    RECURRING_CASH = "recurring_cash"
    ONE_TIME_CASH = "one_time_cash"
    FEE_REIMBURSEMENT = "fee_reimbursement"
    IN_KIND = "in_kind"
    INSURANCE_COVER = "insurance_cover"
    HOUSING_ASSISTANCE = "housing_assistance"


class BenefitFrequency(StrEnum):
    ANNUAL = "annual"
    MONTHLY = "monthly"
    ONE_TIME = "one_time"


class GroupMode(StrEnum):
    ALL = "all"
    ANY = "any"


@dataclass(frozen=True, slots=True)
class Source:
    title: str
    url: str
    publisher: str
    accessed_on: date
    section: str | None = None


@dataclass(frozen=True, slots=True)
class Benefit:
    type: BenefitType
    description: str
    amount_inr: float | None = None  # None when the official amount is variable or not published
    frequency: BenefitFrequency | None = None
    is_cash: bool = True  # False for insurance cover / in-kind / housing construction support


@dataclass(frozen=True, slots=True)
class Condition:
    id: str
    description: str
    field: str
    operator: str  # see operators.py
    value: Any = None
    min_trust: Provenance = Provenance.SELF_DECLARED
    resolves_with: tuple[str, ...] = ()
    failure_message: str | None = None
    source_ref: str | None = None  # e.g. "Guidelines para 4.2"


@dataclass(frozen=True, slots=True)
class ConditionGroup:
    id: str
    description: str
    mode: GroupMode
    items: tuple[Condition | ConditionGroup, ...]
    failure_message: str | None = None
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class Relationship:
    scheme_id: str  # target scheme
    relation: str  # COMPLEMENTED_BY | TOPS_UP | PREREQUISITE_FOR | MUTUALLY_EXCLUSIVE_WITH
    reason: str
    source_url: str | None = None


@dataclass(frozen=True, slots=True)
class SchemeRule:
    scheme_id: str
    version: str
    name: str
    level: SchemeLevel
    department: str
    effective_from: date
    verification_status: VerificationStatus
    benefit: Benefit
    conditions: tuple[Condition | ConditionGroup, ...]
    sources: tuple[Source, ...]
    state: str | None = None
    effective_to: date | None = None
    required_documents: tuple[str, ...] = ()
    relationships: tuple[Relationship, ...] = ()
    limitations: tuple[str, ...] = ()
    application_steps: tuple[str, ...] = ()
    rule_hash: str = ""

    def is_effective(self, on: date) -> bool:
        return self.effective_from <= on and (self.effective_to is None or on <= self.effective_to)
