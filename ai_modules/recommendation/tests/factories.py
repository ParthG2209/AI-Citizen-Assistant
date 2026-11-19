"""SYNTHETIC rule / evaluation builders for recommendation unit tests. Scheme ids, amounts and
conditions are invented fixtures, not real scheme data."""

from __future__ import annotations

from datetime import date

from ai_modules.knowledge_graph.models import SchemeEdge
from ai_modules.rule_engine.definitions import (
    Benefit,
    BenefitFrequency,
    BenefitType,
    Condition,
    ConditionGroup,
    SchemeLevel,
    SchemeRule,
    Source,
    VerificationStatus,
)
from ai_modules.rule_engine.result import (
    ConditionResult,
    EligibilityStatus,
    MissingReason,
    Outcome,
    SchemeEvaluation,
)

AS_OF = date(2026, 11, 18)
SOURCE = Source("Synthetic guideline", "https://example.org/apply", "Test fixture", date(2026, 1, 1))

ELIGIBLE = EligibilityStatus.ELIGIBLE
POTENTIAL = EligibilityStatus.POTENTIALLY_ELIGIBLE
NOT_ELIGIBLE = EligibilityStatus.NOT_ELIGIBLE


def make_rule(
    scheme_id: str,
    benefit_type: BenefitType = BenefitType.RECURRING_CASH,
    amount: float | None = 1000.0,
    frequency: BenefitFrequency | None = BenefitFrequency.ANNUAL,
    conditions: tuple[Condition | ConditionGroup, ...] = (),
    verification: VerificationStatus = VerificationStatus.VERIFIED,
) -> SchemeRule:
    return SchemeRule(
        scheme_id=scheme_id,
        version="1.0.0",
        name=f"Synthetic {scheme_id}",
        level=SchemeLevel.STATE,
        state="Madhya Pradesh",
        department="Test",
        effective_from=date(2025, 1, 1),
        verification_status=verification,
        benefit=Benefit(
            benefit_type,
            f"{scheme_id} benefit",
            amount,
            frequency,
            is_cash=benefit_type
            in (BenefitType.RECURRING_CASH, BenefitType.ONE_TIME_CASH, BenefitType.FEE_REIMBURSEMENT),
        ),
        conditions=conditions,
        sources=(SOURCE,),
        application_steps=("Step 1: synthetic",),
        rule_hash=f"hash-{scheme_id}",
    )


def result(
    condition_id: str,
    field: str,
    outcome: Outcome,
    reason: MissingReason | None = None,
    resolves: tuple[str, ...] = (),
    *,
    actual: object = None,
    provenance: str | None = None,
    operator: str = "eq",
    children: tuple[ConditionResult, ...] = (),
) -> ConditionResult:
    return ConditionResult(
        condition_id=condition_id,
        description=f"{condition_id} description",
        field=field,
        operator=operator,
        expected=None,
        actual=actual,
        provenance=provenance,
        outcome=outcome,
        missing_reason=reason,
        message=f"{condition_id} {outcome.value}",
        resolves_with=resolves,
        children=children,
    )


def passed(condition_id: str, field: str = "age") -> ConditionResult:
    return result(condition_id, field, Outcome.PASS, actual=30, provenance="self_declared")


def failed(condition_id: str, field: str = "age") -> ConditionResult:
    return result(condition_id, field, Outcome.FAIL, actual=10, provenance="self_declared")


def missing(
    condition_id: str,
    field: str,
    resolves: tuple[str, ...] = (),
    reason: MissingReason = MissingReason.NO_VALUE,
    **kwargs: object,
) -> ConditionResult:
    return result(condition_id, field, Outcome.MISSING, reason, resolves, **kwargs)


def evaluation(scheme_id: str, status: EligibilityStatus, *conditions: ConditionResult) -> SchemeEvaluation:
    return SchemeEvaluation(
        scheme_id=scheme_id,
        scheme_name=f"Synthetic {scheme_id}",
        rule_version="1.0.0",
        rule_hash=f"hash-{scheme_id}",
        verification_status="verified",
        status=status,
        conditions=conditions,
        input_hash="input",
        as_of=AS_OF,
    )


def edge(start: str, relation: str, end: str) -> SchemeEdge:
    return SchemeEdge(start, end, relation, f"{start} {relation} {end}", "https://example.org/rel")
