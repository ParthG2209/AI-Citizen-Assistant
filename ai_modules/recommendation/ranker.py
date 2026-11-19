"""Deterministic ranking of scheme recommendations with human-readable reasons.

Sort key, in order:
1. eligibility status   eligible > potentially eligible > not eligible > not evaluated
2. benefit kind         cash (recurring, one-time, fee reimbursement) > non-cash
3. benefit type         recurring cash > one-time cash > fee reimbursement > housing > insurance > in-kind
4. known amount         larger first; only annual or one-time amounts are compared. Monthly amounts
                        are not annualised (payment months are scheme-specific) and rank like
                        unpublished amounts.
5. effort               fewer missing items first
6. rule evidence        verified > partially verified > unverified
7. scheme_id            alphabetical tie-break, so equal inputs always give the same order
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from ai_modules.rule_engine.definitions import Benefit, BenefitFrequency, BenefitType

STATUS_ORDER = {"eligible": 0, "potentially_eligible": 1, "not_eligible": 2, "not_evaluated": 3}
STATUS_TEXT = {
    "eligible": "eligible: every rule condition passes",
    "potentially_eligible": "potentially eligible: no condition fails, some information is missing",
    "not_eligible": "not eligible: at least one rule condition fails",
    "not_evaluated": "not evaluated by the rule engine for this date",
}
CASH_TYPES = (BenefitType.RECURRING_CASH, BenefitType.ONE_TIME_CASH, BenefitType.FEE_REIMBURSEMENT)
TYPE_ORDER = {
    BenefitType.RECURRING_CASH: 0,
    BenefitType.ONE_TIME_CASH: 1,
    BenefitType.FEE_REIMBURSEMENT: 2,
    BenefitType.HOUSING_ASSISTANCE: 3,
    BenefitType.INSURANCE_COVER: 4,
    BenefitType.IN_KIND: 5,
}
EVIDENCE_ORDER = {"verified": 0, "partially_verified": 1, "unverified": 2}


@dataclass(frozen=True, slots=True)
class RankInput:
    scheme_id: str
    status: str  # EligibilityStatus value or "not_evaluated"
    benefit: Benefit
    effort: int  # number of missing items still to resolve
    verification_status: str


@dataclass(frozen=True, slots=True)
class Ranked:
    item: RankInput
    rank: int
    reasons: tuple[str, ...]


def comparable_amount(benefit: Benefit) -> float | None:
    """Published amount usable for comparison, or None (unpublished or monthly)."""
    if benefit.amount_inr is None or benefit.frequency is BenefitFrequency.MONTHLY:
        return None
    return benefit.amount_inr


def _key(item: RankInput) -> tuple[int, int, int, int, float, int, int, str]:
    amount = comparable_amount(item.benefit)
    return (
        STATUS_ORDER.get(item.status, len(STATUS_ORDER)),
        0 if item.benefit.type in CASH_TYPES else 1,
        TYPE_ORDER[item.benefit.type],
        0 if amount is not None else 1,
        -(amount or 0.0),
        item.effort,
        EVIDENCE_ORDER.get(item.verification_status, len(EVIDENCE_ORDER)),
        item.scheme_id,
    )


def _amount_text(benefit: Benefit) -> str:
    if benefit.amount_inr is None:
        return "amount not published in the official source"
    if benefit.frequency is BenefitFrequency.MONTHLY:
        return f"INR {benefit.amount_inr:,.0f} per month (not annualised; ranked as not comparable)"
    period = {BenefitFrequency.ANNUAL: " per year", BenefitFrequency.ONE_TIME: " one time"}.get(
        benefit.frequency, "" if benefit.frequency is None else f" {benefit.frequency}"
    )
    return f"INR {benefit.amount_inr:,.0f}{period}"


def reasons(item: RankInput) -> tuple[str, ...]:
    kind = "cash" if item.benefit.type in CASH_TYPES else "non-cash"
    effort = "nothing missing" if item.effort == 0 else f"{item.effort} missing item(s) to resolve"
    return (
        f"Status: {STATUS_TEXT.get(item.status, item.status)}",
        f"Benefit: {item.benefit.type.value.replace('_', ' ')} ({kind}; cash benefits rank above non-cash)",
        f"Amount: {_amount_text(item.benefit)}",
        f"Effort: {effort}",
        f"Rule evidence: {item.verification_status.replace('_', ' ')}",
    )


def rank(items: Sequence[RankInput]) -> list[Ranked]:
    """Return items sorted by the documented key, numbered from 1."""
    ordered = sorted(items, key=_key)
    return [Ranked(item, position, reasons(item)) for position, item in enumerate(ordered, start=1)]
