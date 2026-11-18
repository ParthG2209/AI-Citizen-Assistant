"""Benefit-gap recommendations for a stored citizen.

Behaviour of ``GET /citizens/{id}/recommendations``:
* Recommendations are always built from a persisted, auditable run that matches the current stored
  profile and the rules in effect today. The latest run is reused when its profile fingerprint and
  rule-set hash still match; otherwise (or with ``refresh``, or when no run exists) a new run is
  evaluated and persisted first. Older runs stay in the history.
* ``stale_reasons`` says why the previous run could not be reused; ``evaluations`` lists the exact
  evaluation id and rule version used per scheme.
* Enrollment status is read live (it is not an eligibility input): schemes marked ``enrolled`` or
  ``applied`` are passed to the recommender as already claimed, so they are never suggested again.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.schemas.recommendation import CitizenRecommendationsOut, EvaluationUsedOut
from app.services import eligibility_service, profile_service, scheme_service
from app.services.scheme_service import to_jsonable
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from ai_modules.knowledge_graph import KnowledgeGraph
    from ai_modules.rule_engine import RuleEngine, SchemeEvaluation, SchemeRule

logger = get_logger(__name__)

CLAIMED_ENROLLMENT_STATUSES = ("enrolled", "applied")


def _rules_for_run(engine: RuleEngine, evaluations: list[SchemeEvaluation]) -> dict[str, SchemeRule]:
    """Current rules, overridden by the exact versions used in the run."""
    rules = {rule.scheme_id: rule for rule in engine.schemes()}
    for evaluation in evaluations:
        # A fresh run's rule-set hash matches the loaded rules, so every version is loaded.
        rules[evaluation.scheme_id] = scheme_service.rule_by_hash(
            engine, evaluation.scheme_id, evaluation.rule_hash
        )
    return rules


def citizen_recommendations(
    db: Session,
    engine: RuleEngine,
    graph: KnowledgeGraph,
    citizen_id: uuid.UUID,
    refresh: bool = False,
) -> CitizenRecommendationsOut:
    profile_service.get_citizen(db, citizen_id)
    run = eligibility_service.current_run(db, engine, citizen_id, refresh)
    stored = eligibility_service.stored_run_evaluations(db, run.ref.run_id)
    evaluations = [item.evaluation for item in stored]
    rules = _rules_for_run(engine, evaluations)

    enrolled = sorted(
        e.scheme_id
        for e in profile_service.list_enrollments(db, citizen_id)
        if e.status in CLAIMED_ENROLLMENT_STATUSES
    )
    complements = graph.complementary_schemes(sorted({e.scheme_id for e in evaluations} | set(enrolled)))

    from ai_modules.recommendation import build_recommendations

    report = build_recommendations(evaluations, enrolled, complements, rules)
    logger.info(
        "recommendations_built",
        extra={
            "citizen_id": str(citizen_id),
            "run_id": str(run.ref.run_id),
            "evaluated_now": run.evaluated_now,
            "stale_reasons": run.stale_reasons,
            "graph_source": graph.source,
        },
    )
    assert run.ref.profile_fingerprint is not None and run.ref.rule_set_hash is not None  # fresh run
    return CitizenRecommendationsOut(
        **to_jsonable(report.to_dict()),
        citizen_id=citizen_id,
        run_id=run.ref.run_id,
        as_of=run.ref.as_of,
        evaluated_at=run.ref.evaluated_at,
        evaluated_now=run.evaluated_now,
        stale_reasons=run.stale_reasons,
        profile_fingerprint=run.ref.profile_fingerprint,
        rule_set_hash=run.ref.rule_set_hash,
        evaluations=[
            EvaluationUsedOut(
                scheme_id=item.evaluation.scheme_id,
                evaluation_id=item.evaluation_id,
                rule_version=item.evaluation.rule_version,
                rule_hash=item.evaluation.rule_hash,
                status=item.evaluation.status.value,
            )
            for item in stored
        ],
        profile_changed_since_evaluation=False,
        graph_source=graph.source,
    )
