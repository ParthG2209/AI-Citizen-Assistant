"""Scheme views over the loaded rule engine and syncing of rule versions into PostgreSQL."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable
from datetime import date
from enum import Enum
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from ai_modules.rule_engine.definitions import Condition, ConditionGroup, SchemeRule
from app.errors import ApiError, not_found
from app.models.scheme import Scheme, SchemeRuleVersion

if TYPE_CHECKING:
    from ai_modules.rule_engine import RuleEngine
from app.schemas.scheme import (
    BenefitOut,
    ConditionGroupOut,
    ConditionOut,
    RelationshipOut,
    SchemeDetail,
    SchemeSummary,
    SourceOut,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)


class RuleVersionConflict(Exception):
    """A rule's content changed but its ``version`` did not; stored history would become ambiguous."""


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses / enums / dates / tuples into plain JSON-compatible values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [to_jsonable(v) for v in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    return json.loads(json.dumps(value, default=str))


def _condition_out(item: Condition | ConditionGroup) -> ConditionOut | ConditionGroupOut:
    if isinstance(item, ConditionGroup):
        return ConditionGroupOut(
            id=item.id,
            description=item.description,
            mode=item.mode.value,
            items=[_condition_out(child) for child in item.items],
            failure_message=item.failure_message,
            source_ref=item.source_ref,
        )
    return ConditionOut(
        id=item.id,
        description=item.description,
        field=item.field,
        operator=item.operator,
        value=to_jsonable(item.value),
        min_trust=item.min_trust.value,
        resolves_with=list(item.resolves_with),
        failure_message=item.failure_message,
        source_ref=item.source_ref,
    )


def _summary_fields(rule: SchemeRule) -> dict[str, Any]:
    return {
        "scheme_id": rule.scheme_id,
        "name": rule.name,
        "level": rule.level.value,
        "state": rule.state,
        "department": rule.department,
        "version": rule.version,
        "effective_from": rule.effective_from,
        "effective_to": rule.effective_to,
        "verification_status": rule.verification_status.value,
        "benefit": BenefitOut.model_validate(to_jsonable(rule.benefit)),
        "required_documents": list(rule.required_documents),
    }


def scheme_summary(rule: SchemeRule) -> SchemeSummary:
    return SchemeSummary(**_summary_fields(rule))


def scheme_detail(rule: SchemeRule) -> SchemeDetail:
    return SchemeDetail(
        **_summary_fields(rule),
        rule_hash=rule.rule_hash,
        conditions=[_condition_out(item) for item in rule.conditions],
        sources=[SourceOut.model_validate(to_jsonable(s)) for s in rule.sources],
        relationships=[RelationshipOut.model_validate(to_jsonable(r)) for r in rule.relationships],
        limitations=list(rule.limitations),
        application_steps=list(rule.application_steps),
    )


def get_rule(engine: RuleEngine, scheme_id: str) -> SchemeRule:
    """The rule version of ``scheme_id`` effective today (404 if unknown or not currently effective)."""
    for rule in engine.schemes():
        if rule.scheme_id == scheme_id:
            return rule
    raise not_found("scheme", scheme_id)


def all_rule_versions(engine: RuleEngine) -> list[SchemeRule]:
    """Every loaded rule version (effective or not), oldest first within each scheme."""
    return [rule for scheme_id in engine.scheme_ids() for rule in engine.versions(scheme_id)]


def rule_by_hash(engine: RuleEngine, scheme_id: str, rule_hash: str) -> SchemeRule:
    """The exact rule version that produced an evaluation."""
    for rule in engine.versions(scheme_id):
        if rule.rule_hash == rule_hash:
            return rule
    raise LookupError(f"No loaded rule of '{scheme_id}' has hash {rule_hash}")


def ensure_rule_version(db: Session, rule: SchemeRule) -> SchemeRuleVersion:
    """Upsert the scheme row and return the stored snapshot for this exact rule (keyed by rule_hash)."""
    scheme = db.get(Scheme, rule.scheme_id)
    if scheme is None:
        scheme = Scheme(scheme_id=rule.scheme_id)
        db.add(scheme)
    scheme.name = rule.name
    scheme.level = rule.level.value
    scheme.state = rule.state
    scheme.department = rule.department
    scheme.benefit = to_jsonable(rule.benefit)
    scheme.active = True

    stored = db.scalar(
        select(SchemeRuleVersion).where(
            SchemeRuleVersion.scheme_id == rule.scheme_id, SchemeRuleVersion.version == rule.version
        )
    )
    if stored is not None:
        if stored.rule_hash != rule.rule_hash:
            raise RuleVersionConflict(
                f"Rule '{rule.scheme_id}' version {rule.version} changed (stored hash "
                f"{stored.rule_hash[:12]}, loaded hash {rule.rule_hash[:12]}); bump the rule version"
            )
        return stored
    db.flush()  # the scheme row must exist before the version row references it
    stored = SchemeRuleVersion(
        scheme_id=rule.scheme_id,
        version=rule.version,
        effective_from=rule.effective_from,
        effective_to=rule.effective_to,
        rule_hash=rule.rule_hash,
        definition=to_jsonable(rule),
        sources=to_jsonable(rule.sources),
        verification_status=rule.verification_status.value,
    )
    db.add(stored)
    db.flush()
    return stored


def _version_count(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(SchemeRuleVersion)) or 0


def sync_rules(db: Session, rules: Iterable[SchemeRule]) -> dict[str, int]:
    """Idempotently store every loaded rule; schemes missing from the rule set become inactive."""
    rules = list(rules)
    before = _version_count(db)
    for rule in rules:
        ensure_rule_version(db, rule)
    loaded_ids = sorted({rule.scheme_id for rule in rules})
    deactivated = db.execute(
        update(Scheme)
        .where(Scheme.scheme_id.not_in(loaded_ids), Scheme.active.is_(True))
        .values(active=False)
    ).rowcount
    db.commit()
    result = {
        "schemes": len(loaded_ids),
        "rule_versions": len(rules),
        "new_rule_versions": _version_count(db) - before,
        "deactivated": deactivated,
    }
    logger.info("rules_synced", extra=result)
    return result


def require_engine(engine: Any) -> Any:
    if engine is None:
        raise ApiError(503, "rule_engine_unavailable", "The rule engine is not loaded; see GET /api/v1/ready")
    return engine
