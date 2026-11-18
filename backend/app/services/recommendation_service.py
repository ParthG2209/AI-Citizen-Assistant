"""Benefit-gap recommendations for a stored citizen.

Behaviour of ``GET /citizens/{id}/recommendations``:
* The citizen's latest persisted evaluation run is used as-is (eligibility is never recomputed here
  silently), so the recommendations always match a stored, auditable run.
* A new run is evaluated and persisted first when the citizen has no run yet, when ``refresh`` is
  requested, or when the run used a rule version that is no longer loaded.
* ``profile_changed_since_evaluation`` tells the client that the profile was edited after the run.
* Enrollment status is read live: schemes marked ``enrolled`` or ``applied`` are passed to the
  recommender as already claimed, so they are never suggested again.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from app.schemas.recommendation import CitizenRecommendationsOut
from app.services import eligibility_service, profile_service, scheme_service
from app.services.scheme_service import to_jsonable
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from ai_modules.knowledge_graph import KnowledgeGraph
    from ai_modules.rule_engine import RuleEngine, SchemeEvaluation, SchemeRule

logger = get_logger(__name__)

CLAIMED_ENROLLMENT_STATUSES = ("enrolled", "applied")


def _rules_for_run(engine: RuleEngine, evaluations: list[SchemeEvaluation]) -> dict[str, SchemeRule] | None:
    """Current rules, overridden by the exact versions used in the run; None if any is no longer loaded."""
    rules = {rule.scheme_id: rule for rule in engine.schemes()}
    for evaluation in evaluations:
        try:
            rules[evaluation.scheme_id] = scheme_service.rule_by_hash(
                engine, evaluation.scheme_id, evaluation.rule_hash
            )
        except LookupError:
            return None
    return rules


def citizen_recommendations(
    db: Session,
    engine: RuleEngine,
    graph: KnowledgeGraph,
    citizen_id: uuid.UUID,
    refresh: bool = False,
) -> CitizenRecommendationsOut:
    citizen = profile_service.get_citizen(db, citizen_id)
    latest = None if refresh else eligibility_service.latest_run_ref(db, citizen_id)
    evaluations = eligibility_service.stored_run_evaluations(db, latest[0]) if latest else []
    rules = _rules_for_run(engine, evaluations) if latest else None

    evaluated_now = rules is None
    if evaluated_now:
        eligibility_service.evaluate_citizen(db, engine, citizen_id, None)
        latest = eligibility_service.latest_run_ref(db, citizen_id)
        assert latest is not None
        evaluations = eligibility_service.stored_run_evaluations(db, latest[0])
        rules = _rules_for_run(engine, evaluations)
        assert rules is not None  # just evaluated with the loaded rules
    run_id, as_of, evaluated_at = latest

    enrolled = sorted(
        e.scheme_id
        for e in profile_service.list_enrollments(db, citizen_id)
        if e.status in CLAIMED_ENROLLMENT_STATUSES
    )
    complements = graph.complementary_schemes(sorted({e.scheme_id for e in evaluations} | set(enrolled)))

    from ai_modules.recommendation import build_recommendations

    report = build_recommendations(evaluations, enrolled, complements, rules)
    db.refresh(citizen)
    logger.info(
        "recommendations_built",
        extra={
            "citizen_id": str(citizen_id),
            "run_id": str(run_id),
            "evaluated_now": evaluated_now,
            "graph_source": graph.source,
        },
    )
    return CitizenRecommendationsOut(
        **to_jsonable(report.to_dict()),
        citizen_id=citizen_id,
        run_id=run_id,
        as_of=as_of,
        evaluated_at=evaluated_at,
        evaluated_now=evaluated_now,
        profile_changed_since_evaluation=citizen.updated_at > evaluated_at,
        graph_source=graph.source,
    )
