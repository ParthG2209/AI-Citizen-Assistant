"""Run the deterministic rule engine and persist / present its results.

The rule engine alone decides eligibility; this module only stores and groups its output.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from ai_modules.rule_engine.fields import FieldValue
from ai_modules.rule_engine.result import DISCLAIMER, ConditionResult, EligibilityStatus, SchemeEvaluation
from app.errors import ApiError, not_found
from app.models.citizen import ProfileField
from app.models.evaluation import ConditionResultRow, EligibilityEvaluation
from app.models.scheme import Scheme, SchemeRuleVersion
from app.schemas.evaluation import (
    ConditionResultOut,
    EvaluationDetailOut,
    EvaluationRunOut,
    EvaluationSummary,
    ProfileValueIn,
    SchemeEvaluationOut,
)
from app.services import audit_service, profile_service, scheme_service
from app.services.scheme_service import to_jsonable
from app.utils.logger import get_logger
from app.utils.provenance import field_values_from_rows

logger = get_logger(__name__)


# ---------------------------------------------------------------------------- presentation helpers


def _condition_out(result: ConditionResult) -> ConditionResultOut:
    return ConditionResultOut(
        condition_id=result.condition_id,
        description=result.description,
        field=result.field,
        operator=result.operator,
        expected=to_jsonable(result.expected),
        actual=to_jsonable(result.actual),
        provenance=result.provenance,
        outcome=str(result.outcome),
        missing_reason=str(result.missing_reason) if result.missing_reason else None,
        message=result.message,
        resolves_with=list(result.resolves_with),
        children=[_condition_out(child) for child in result.children],
    )


def _evaluation_out(evaluation: SchemeEvaluation, evaluation_id: uuid.UUID | None) -> SchemeEvaluationOut:
    return SchemeEvaluationOut(
        id=evaluation_id,
        scheme_id=evaluation.scheme_id,
        scheme_name=evaluation.scheme_name,
        rule_version=evaluation.rule_version,
        rule_hash=evaluation.rule_hash,
        verification_status=evaluation.verification_status,
        status=str(evaluation.status),
        input_hash=evaluation.input_hash,
        as_of=evaluation.as_of,
        notes=list(evaluation.notes),
        conditions=[_condition_out(c) for c in evaluation.conditions],
    )


def _group(
    items: Iterable[SchemeEvaluationOut],
    *,
    run_id: uuid.UUID | None,
    citizen_id: uuid.UUID | None,
    as_of: date,
    evaluated_at: datetime,
) -> EvaluationRunOut:
    groups: dict[str, list[SchemeEvaluationOut]] = {status.value: [] for status in EligibilityStatus}
    for item in items:
        groups[item.status].append(item)
    return EvaluationRunOut(
        run_id=run_id,
        citizen_id=citizen_id,
        as_of=as_of,
        evaluated_at=evaluated_at,
        summary=EvaluationSummary(**{status: len(group) for status, group in groups.items()}),
        eligible=groups[EligibilityStatus.ELIGIBLE],
        potentially_eligible=groups[EligibilityStatus.POTENTIALLY_ELIGIBLE],
        not_eligible=groups[EligibilityStatus.NOT_ELIGIBLE],
        disclaimer=DISCLAIMER,
    )


# ---------------------------------------------------------------------------- stateless


def evaluate_stateless(
    engine: Any, profile: Mapping[str, ProfileValueIn], as_of: date | None
) -> EvaluationRunOut:
    """Evaluate a caller-supplied profile; nothing is written to the database."""
    values = profile_service.validate_profile_values(
        {name: item.value for name, item in profile.items()}, loc=("body", "profile"), allow_null=False
    )
    field_values = {
        name: FieldValue(
            value=value, provenance=profile[name].provenance, confidence=profile[name].confidence
        )
        for name, value in values.items()
    }
    as_of = as_of or date.today()
    results = engine.evaluate_all(field_values, as_of=as_of)
    return _group(
        (_evaluation_out(r, None) for r in results),
        run_id=None,
        citizen_id=None,
        as_of=as_of,
        evaluated_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------- persisted


def _condition_rows(evaluation_id: uuid.UUID, results: Iterable[ConditionResult]) -> list[ConditionResultRow]:
    """Flatten nested condition results in pre-order; ``parent_id`` keeps the tree."""
    rows: list[ConditionResultRow] = []

    def visit(result: ConditionResult, parent_id: uuid.UUID | None) -> None:
        row = ConditionResultRow(
            id=uuid.uuid4(),
            evaluation_id=evaluation_id,
            parent_id=parent_id,
            position=len(rows),
            condition_id=result.condition_id,
            description=result.description,
            outcome=str(result.outcome),
            field=result.field,
            operator=result.operator,
            expected=to_jsonable(result.expected),
            actual=to_jsonable(result.actual),
            provenance=result.provenance,
            missing_reason=str(result.missing_reason) if result.missing_reason else None,
            message=result.message,
            resolves_with=list(result.resolves_with),
        )
        rows.append(row)
        for child in result.children:
            visit(child, row.id)

    for result in results:
        visit(result, None)
    return rows


def evaluate_citizen(db: Session, engine: Any, citizen_id: uuid.UUID, as_of: date | None) -> EvaluationRunOut:
    """Evaluate every effective scheme for a stored citizen and persist the run atomically."""
    profile_service.get_citizen(db, citizen_id)
    rows = db.scalars(select(ProfileField).where(ProfileField.citizen_id == citizen_id))
    profile = field_values_from_rows(rows)
    as_of = as_of or date.today()
    results: list[SchemeEvaluation] = engine.evaluate_all(profile, as_of=as_of)

    run_id = uuid.uuid4()
    evaluated_at = datetime.now(UTC)
    outputs: list[SchemeEvaluationOut] = []
    for result in results:
        rule = scheme_service.rule_by_hash(engine, result.scheme_id, result.rule_hash)
        try:
            version = scheme_service.ensure_rule_version(db, rule)
        except scheme_service.RuleVersionConflict as exc:
            raise ApiError(409, "rule_version_conflict", str(exc)) from exc
        evaluation = EligibilityEvaluation(
            id=uuid.uuid4(),
            run_id=run_id,
            citizen_id=citizen_id,
            scheme_id=result.scheme_id,
            rule_version_id=version.id,
            status=str(result.status),
            input_hash=result.input_hash,
            as_of=result.as_of,
            notes=list(result.notes),
            evaluated_at=evaluated_at,
        )
        db.add(evaluation)
        db.flush()
        db.add_all(_condition_rows(evaluation.id, result.conditions))
        outputs.append(_evaluation_out(result, evaluation.id))

    run = _group(outputs, run_id=run_id, citizen_id=citizen_id, as_of=as_of, evaluated_at=evaluated_at)
    audit_service.record(
        db, "citizen", citizen_id, "evaluation_run", {"run_id": str(run_id), **run.summary.model_dump()}
    )
    db.commit()
    logger.info(
        "evaluation_run_saved",
        extra={"citizen_id": str(citizen_id), "run_id": str(run_id), "schemes": len(outputs)},
    )
    return run


def _stored_condition_tree(rows: list[ConditionResultRow]) -> list[ConditionResultOut]:
    nodes: dict[uuid.UUID, ConditionResultOut] = {}
    roots: list[ConditionResultOut] = []
    for row in sorted(rows, key=lambda r: r.position):
        node = ConditionResultOut(
            condition_id=row.condition_id,
            description=row.description,
            field=row.field,
            operator=row.operator,
            expected=row.expected,
            actual=row.actual,
            provenance=row.provenance,
            outcome=row.outcome,
            missing_reason=row.missing_reason,
            message=row.message,
            resolves_with=list(row.resolves_with),
        )
        nodes[row.id] = node
        if row.parent_id is None:
            roots.append(node)
        else:
            nodes[row.parent_id].children.append(node)
    return roots


def _stored_out(
    evaluation: EligibilityEvaluation, scheme: Scheme, version: SchemeRuleVersion
) -> SchemeEvaluationOut:
    return SchemeEvaluationOut(
        id=evaluation.id,
        scheme_id=evaluation.scheme_id,
        scheme_name=scheme.name,
        rule_version=version.version,
        rule_hash=version.rule_hash,
        verification_status=version.verification_status,
        status=evaluation.status,
        input_hash=evaluation.input_hash,
        as_of=evaluation.as_of,
        notes=list(evaluation.notes),
        conditions=_stored_condition_tree(evaluation.conditions),
    )


def _stored_query() -> Select[tuple[EligibilityEvaluation, Scheme, SchemeRuleVersion]]:
    return (
        select(EligibilityEvaluation, Scheme, SchemeRuleVersion)
        .join(Scheme, Scheme.scheme_id == EligibilityEvaluation.scheme_id)
        .join(SchemeRuleVersion, SchemeRuleVersion.id == EligibilityEvaluation.rule_version_id)
    )


def latest_run(db: Session, citizen_id: uuid.UUID) -> EvaluationRunOut:
    profile_service.get_citizen(db, citizen_id)
    latest = db.execute(
        select(EligibilityEvaluation.run_id, EligibilityEvaluation.as_of, EligibilityEvaluation.evaluated_at)
        .where(EligibilityEvaluation.citizen_id == citizen_id)
        .order_by(EligibilityEvaluation.evaluated_at.desc())
        .limit(1)
    ).first()
    if latest is None:
        raise not_found("evaluation", f"latest for citizen {citizen_id}")
    run_id, as_of, evaluated_at = latest
    rows = db.execute(
        _stored_query().where(EligibilityEvaluation.run_id == run_id).order_by(Scheme.scheme_id)
    )
    return _group(
        (_stored_out(evaluation, scheme, version) for evaluation, scheme, version in rows),
        run_id=run_id,
        citizen_id=citizen_id,
        as_of=as_of,
        evaluated_at=evaluated_at,
    )


def get_evaluation(db: Session, evaluation_id: uuid.UUID) -> EvaluationDetailOut:
    row = db.execute(_stored_query().where(EligibilityEvaluation.id == evaluation_id)).first()
    if row is None:
        raise not_found("evaluation", evaluation_id)
    evaluation, scheme, version = row
    return EvaluationDetailOut(
        **_stored_out(evaluation, scheme, version).model_dump(),
        run_id=evaluation.run_id,
        citizen_id=evaluation.citizen_id,
        evaluated_at=evaluation.evaluated_at,
        disclaimer=DISCLAIMER,
    )
