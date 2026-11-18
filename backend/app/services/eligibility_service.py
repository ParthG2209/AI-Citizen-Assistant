"""Run the deterministic rule engine and persist / present its results.

The rule engine alone decides eligibility; this module only stores and groups its output.

Freshness: every persisted run stores a ``profile_fingerprint`` (sha256 of every stored profile value
with its provenance and OCR confidence -- everything the engine reads; no timestamps) and a
``rule_set_hash`` (sha256 of the effective rule versions + the engine's confidence threshold). A run
is stale when either differs from the current state (``STALE_PROFILE_CHANGED`` /
``STALE_RULE_VERSION_CHANGED``). Enrollments are not part of the fingerprint: they never change
eligibility and are read live by the recommender.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from ai_modules.rule_engine import verification_warning
from ai_modules.rule_engine.definitions import VerificationStatus
from ai_modules.rule_engine.fields import FieldValue, Provenance, coerce_value
from ai_modules.rule_engine.loader import canonical_json, sha256_hex
from ai_modules.rule_engine.result import (
    DISCLAIMER,
    ConditionResult,
    EligibilityStatus,
    MissingReason,
    Outcome,
    SchemeEvaluation,
)
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

STALE_PROFILE_CHANGED = "profile_changed"
STALE_RULE_VERSION_CHANGED = "rule_version_changed"

# Stateless endpoints: callers may never claim ``verified``; ``document_extracted`` is accepted for
# what-if checks but the response is marked as a simulation with client-asserted provenance.
CLIENT_PROVENANCE_ALLOWED = (Provenance.SELF_DECLARED, Provenance.DOCUMENT_EXTRACTED)
SIMULATION_NOTE = (
    "Simulation: the profile and its provenance were supplied by the caller and nothing was stored. "
    "Values marked document_extracted are client-asserted (no uploaded document backs them); the "
    "result must not be treated as document-backed or verified."
)


# ---------------------------------------------------------------------------- fingerprints


def profile_fingerprint(profile: Mapping[str, FieldValue]) -> str:
    """Deterministic, timestamp-free hash of everything the engine reads from a profile."""
    payload: dict[str, Any] = {}
    for name, item in profile.items():
        try:
            value = coerce_value(name, item.value)
        except ValueError:
            value = item.value
        payload[name] = {
            "value": value,
            "provenance": Provenance(item.provenance).value,
            "confidence": item.confidence,
        }
    return sha256_hex(canonical_json(payload))


def rule_set_hash(engine: Any, as_of: date | None = None) -> str:
    """Identity of the rules the engine applies on ``as_of`` (default today)."""
    rules = sorted([r.scheme_id, r.version, r.rule_hash] for r in engine.schemes(as_of or date.today()))
    return sha256_hex(canonical_json({"confidence_threshold": engine.confidence_threshold, "rules": rules}))


def _stored_profile(db: Session, citizen_id: uuid.UUID) -> dict[str, FieldValue]:
    rows = db.scalars(select(ProfileField).where(ProfileField.citizen_id == citizen_id))
    return field_values_from_rows(rows)


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
        verification_warning=evaluation.verification_warning,
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
    **extra: Any,
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
        **extra,
    )


# ---------------------------------------------------------------------------- stateless


@dataclass(frozen=True, slots=True)
class ClientProfile:
    """A validated caller-supplied profile (stateless endpoints)."""

    values: dict[str, FieldValue]
    asserted_document_fields: list[str]  # document_extracted claimed by the caller, no document behind it


def client_profile(profile: Mapping[str, ProfileValueIn]) -> ClientProfile:
    """Validate a stateless profile: values (422 ``validation_error``) and provenance.

    ``verified`` is rejected with 422 ``provenance_not_allowed``: nothing in this API can verify a value.
    """
    forbidden = [
        {
            "loc": ["body", "profile", name, "provenance"],
            "msg": f"Provenance '{item.provenance.value}' cannot be supplied by the caller; allowed: "
            + ", ".join(p.value for p in CLIENT_PROVENANCE_ALLOWED),
            "type": "provenance_not_allowed",
        }
        for name, item in profile.items()
        if item.provenance not in CLIENT_PROVENANCE_ALLOWED
    ]
    if forbidden:
        raise ApiError(422, "provenance_not_allowed", "Caller-supplied provenance not allowed", forbidden)
    values = profile_service.validate_profile_values(
        {name: item.value for name, item in profile.items()}, loc=("body", "profile"), allow_null=False
    )
    field_values = {
        name: FieldValue(value, profile[name].provenance, confidence=profile[name].confidence)
        for name, value in values.items()
    }
    asserted = sorted(
        name for name, fv in field_values.items() if fv.provenance is Provenance.DOCUMENT_EXTRACTED
    )
    return ClientProfile(values=field_values, asserted_document_fields=asserted)


def evaluate_stateless(
    engine: Any, profile: Mapping[str, ProfileValueIn], as_of: date | None
) -> EvaluationRunOut:
    """Evaluate a caller-supplied profile; nothing is written to the database."""
    client = client_profile(profile)
    as_of = as_of or date.today()
    results = engine.evaluate_all(client.values, as_of=as_of)
    return _group(
        (_evaluation_out(r, None) for r in results),
        run_id=None,
        citizen_id=None,
        as_of=as_of,
        evaluated_at=datetime.now(UTC),
        profile_fingerprint=profile_fingerprint(client.values),
        rule_set_hash=rule_set_hash(engine, as_of),
        simulation=True,
        client_asserted_provenance=client.asserted_document_fields,
        provenance_note=SIMULATION_NOTE,
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
    profile = _stored_profile(db, citizen_id)
    as_of = as_of or date.today()
    results: list[SchemeEvaluation] = engine.evaluate_all(profile, as_of=as_of)
    fingerprint = profile_fingerprint(profile)
    rules_hash = rule_set_hash(engine, as_of)

    run_id = uuid.uuid4()
    evaluated_at = db.scalar(select(func.now()))  # DB clock
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
            profile_fingerprint=fingerprint,
            rule_set_hash=rules_hash,
        )
        db.add(evaluation)
        db.flush()
        db.add_all(_condition_rows(evaluation.id, result.conditions))
        outputs.append(_evaluation_out(result, evaluation.id))

    run = _group(
        outputs,
        run_id=run_id,
        citizen_id=citizen_id,
        as_of=as_of,
        evaluated_at=evaluated_at,
        profile_fingerprint=fingerprint,
        rule_set_hash=rules_hash,
    )
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


def _stored_verification_warning(version: SchemeRuleVersion) -> str | None:
    """The engine's warning for the stored rule snapshot (it only reads status and limitations)."""
    snapshot = SimpleNamespace(
        verification_status=VerificationStatus(version.verification_status),
        limitations=tuple(version.definition.get("limitations", ())),
    )
    return verification_warning(cast(Any, snapshot))


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
        verification_warning=_stored_verification_warning(version),
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


@dataclass(frozen=True, slots=True)
class RunRef:
    """Run-level facts of a persisted evaluation run (shared by all its per-scheme rows)."""

    run_id: uuid.UUID
    as_of: date
    evaluated_at: datetime
    profile_fingerprint: str | None  # None for runs stored before fingerprints existed
    rule_set_hash: str | None


def latest_run_ref(db: Session, citizen_id: uuid.UUID) -> RunRef | None:
    """The citizen's most recent run, or None."""
    latest = db.execute(
        select(
            EligibilityEvaluation.run_id,
            EligibilityEvaluation.as_of,
            EligibilityEvaluation.evaluated_at,
            EligibilityEvaluation.profile_fingerprint,
            EligibilityEvaluation.rule_set_hash,
        )
        .where(EligibilityEvaluation.citizen_id == citizen_id)
        .order_by(EligibilityEvaluation.evaluated_at.desc())
        .limit(1)
    ).first()
    return None if latest is None else RunRef(*latest)


def stale_reasons(db: Session, engine: Any, citizen_id: uuid.UUID, ref: RunRef) -> list[str]:
    """Why ``ref`` no longer reflects the stored profile / the rules in effect today (empty: fresh).

    Runs without stored fingerprints (pre-migration) are treated as stale.
    """
    reasons: list[str] = []
    if ref.profile_fingerprint != profile_fingerprint(_stored_profile(db, citizen_id)):
        reasons.append(STALE_PROFILE_CHANGED)
    if ref.rule_set_hash != rule_set_hash(engine):
        reasons.append(STALE_RULE_VERSION_CHANGED)
    return reasons


@dataclass(frozen=True, slots=True)
class CurrentRun:
    ref: RunRef
    evaluated_now: bool  # a new run was evaluated and persisted by this call
    stale_reasons: list[str]  # why the previous run could not be reused (empty if none existed / refresh)


def current_run(db: Session, engine: Any, citizen_id: uuid.UUID, refresh: bool = False) -> CurrentRun:
    """The latest run if it is still fresh; otherwise evaluate and persist a new one (history kept)."""
    latest = None if refresh else latest_run_ref(db, citizen_id)
    reasons = stale_reasons(db, engine, citizen_id, latest) if latest is not None else []
    if latest is not None and not reasons:
        return CurrentRun(ref=latest, evaluated_now=False, stale_reasons=[])
    evaluate_citizen(db, engine, citizen_id, None)
    ref = latest_run_ref(db, citizen_id)
    assert ref is not None
    if reasons:
        logger.info(
            "stale_run_replaced",
            extra={"citizen_id": str(citizen_id), "run_id": str(ref.run_id), "stale_reasons": reasons},
        )
    return CurrentRun(ref=ref, evaluated_now=True, stale_reasons=reasons)


def latest_run(db: Session, engine: Any, citizen_id: uuid.UUID) -> EvaluationRunOut:
    """The most recent persisted run as stored, with ``stale`` / ``stale_reasons`` (never recomputed)."""
    profile_service.get_citizen(db, citizen_id)
    latest = latest_run_ref(db, citizen_id)
    if latest is None:
        raise not_found("evaluation", f"latest for citizen {citizen_id}")
    reasons = stale_reasons(db, engine, citizen_id, latest)
    rows = db.execute(
        _stored_query().where(EligibilityEvaluation.run_id == latest.run_id).order_by(Scheme.scheme_id)
    )
    return _group(
        (_stored_out(evaluation, scheme, version) for evaluation, scheme, version in rows),
        run_id=latest.run_id,
        citizen_id=citizen_id,
        as_of=latest.as_of,
        evaluated_at=latest.evaluated_at,
        profile_fingerprint=latest.profile_fingerprint,
        rule_set_hash=latest.rule_set_hash,
        stale=bool(reasons),
        stale_reasons=reasons,
    )


def _stored_condition_results(rows: list[ConditionResultRow]) -> tuple[ConditionResult, ...]:
    """Rebuild the engine's ``ConditionResult`` tree from stored rows."""
    children: dict[uuid.UUID | None, list[ConditionResultRow]] = {}
    for row in sorted(rows, key=lambda r: r.position):
        children.setdefault(row.parent_id, []).append(row)

    def build(row: ConditionResultRow) -> ConditionResult:
        return ConditionResult(
            condition_id=row.condition_id,
            description=row.description,
            field=row.field,
            operator=row.operator,
            expected=row.expected,
            actual=row.actual,
            provenance=row.provenance,
            outcome=Outcome(row.outcome),
            missing_reason=MissingReason(row.missing_reason) if row.missing_reason else None,
            message=row.message,
            resolves_with=tuple(row.resolves_with),
            children=tuple(build(child) for child in children.get(row.id, [])),
        )

    return tuple(build(row) for row in children.get(None, []))


@dataclass(frozen=True, slots=True)
class StoredEvaluation:
    evaluation_id: uuid.UUID
    evaluation: SchemeEvaluation


def stored_run_evaluations(db: Session, run_id: uuid.UUID) -> list[StoredEvaluation]:
    """The persisted run as rule-engine ``SchemeEvaluation`` objects with their row ids."""
    rows = db.execute(
        _stored_query().where(EligibilityEvaluation.run_id == run_id).order_by(Scheme.scheme_id)
    )
    return [
        StoredEvaluation(
            evaluation_id=evaluation.id,
            evaluation=SchemeEvaluation(
                scheme_id=evaluation.scheme_id,
                scheme_name=scheme.name,
                rule_version=version.version,
                rule_hash=version.rule_hash,
                verification_status=version.verification_status,
                status=EligibilityStatus(evaluation.status),
                conditions=_stored_condition_results(evaluation.conditions),
                input_hash=evaluation.input_hash,
                as_of=evaluation.as_of,
                notes=tuple(evaluation.notes),
                verification_warning=_stored_verification_warning(version),
            ),
        )
        for evaluation, scheme, version in rows
    ]


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
        profile_fingerprint=evaluation.profile_fingerprint,
        rule_set_hash=evaluation.rule_set_hash,
        disclaimer=DISCLAIMER,
    )
