"""Grounded explanations of rule-engine decisions and scheme Q&A (RAG over official documents).

The rule engine alone decides eligibility. The RAG service only explains a decision it is given;
every response is checked to carry exactly the engine's status. Question and answer text are never
logged (lengths only).
"""

from __future__ import annotations

import importlib.util
import threading
import uuid
from collections.abc import Mapping
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_modules.rule_engine.fields import FieldValue
from ai_modules.rule_engine.result import DISCLAIMER
from app.config import get_settings
from app.errors import ApiError
from app.models.evaluation import EligibilityEvaluation
from app.schemas.evaluation import ProfileValueIn
from app.schemas.explanation import ExplanationOut
from app.services import eligibility_service, profile_service, scheme_service
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from ai_modules.rag import Explanation, ExplanationService
    from ai_modules.rule_engine import RuleEngine, SchemeEvaluation, SchemeRule

logger = get_logger(__name__)

_RAG_PACKAGES = ("faiss", "sentence_transformers")
_HINT = "Install ai_modules/requirements.txt and run: python scripts/build_vector_index.py"
_service: ExplanationService | None = None
_lock = threading.Lock()


def rag_status() -> dict[str, Any]:
    """Cheap readiness probe: dependencies importable and index manifest present (no model loading)."""
    missing = [name for name in _RAG_PACKAGES if importlib.util.find_spec(name) is None]
    if missing:
        return {
            "status": "unavailable",
            "index": "unknown",
            "reason": f"missing packages: {', '.join(missing)}",
        }
    from ai_modules.rag.vector_store import IndexNotFoundError, VectorStore

    try:
        manifest = VectorStore.read_manifest(get_settings().rag_index_dir)
    except IndexNotFoundError:
        return {"status": "unavailable", "index": "absent", "reason": "vector index not built"}
    except Exception as exc:  # unreadable / incompatible manifest
        return {"status": "unavailable", "index": "invalid", "reason": f"{type(exc).__name__}: {exc}"}
    return {"status": "ok", "index": "present", "chunks": manifest.chunk_count, "model": manifest.model_name}


def get_service() -> ExplanationService:
    """Process-wide ExplanationService, created on first use (503 ``rag_unavailable`` if not possible)."""
    global _service
    if _service is not None:
        return _service
    status = rag_status()
    if status["status"] != "ok":
        raise ApiError(503, "rag_unavailable", "Explanations are unavailable", {**status, "hint": _HINT})
    with _lock:
        if _service is None:
            from ai_modules.rag import ExplanationService
            from ai_modules.rag.llm_router import LLMRouter

            settings = get_settings()
            router = LLMRouter(
                ollama_base_url=settings.ollama_base_url,
                ollama_model=settings.ollama_model,
                gemini_api_key=settings.gemini_api_key.get_secret_value()
                if settings.gemini_api_key
                else None,
            )
            _service = ExplanationService(index_dir=settings.rag_index_dir, router=router)
    return _service


def _out(
    explanation: Explanation,
    rule: SchemeRule,
    *,
    evaluation_row: EligibilityEvaluation | None = None,
    evaluated_now: bool | None = None,
) -> ExplanationOut:
    logger.info(
        "explanation_generated",
        extra={
            "scheme_id": rule.scheme_id,
            "provider": explanation.provider,
            "grounded": explanation.grounded,
            "refused": explanation.refused,
            "citations": len(explanation.citations),
            "answer_length": len(explanation.answer),
            "latency_ms": explanation.latency_ms,
        },
    )
    return ExplanationOut(
        **explanation.to_dict(),
        rule_version=rule.version,
        rule_hash=rule.rule_hash,
        evaluation_id=evaluation_row.id if evaluation_row else None,
        run_id=evaluation_row.run_id if evaluation_row else None,
        evaluated_at=evaluation_row.evaluated_at if evaluation_row else None,
        evaluated_now=evaluated_now,
        disclaimer=DISCLAIMER,
    )


def _explain_checked(
    service: ExplanationService,
    evaluation: SchemeEvaluation,
    rule: SchemeRule,
    question: str | None,
    language: str,
) -> Explanation:
    explanation = service.explain(evaluation, rule, question=question, language=language)
    if explanation.decision != evaluation.status.value:  # invariant: the LLM never changes the decision
        raise RuntimeError(
            f"explanation decision {explanation.decision!r} != engine {evaluation.status.value!r}"
        )
    return explanation


def _stored_scheme_evaluation(
    db: Session, engine: RuleEngine, citizen_id: uuid.UUID, scheme_id: str
) -> tuple[SchemeEvaluation, SchemeRule, EligibilityEvaluation] | None:
    latest = eligibility_service.latest_run_ref(db, citizen_id)
    if latest is None:
        return None
    for evaluation in eligibility_service.stored_run_evaluations(db, latest[0]):
        if evaluation.scheme_id != scheme_id:
            continue
        try:
            rule = scheme_service.rule_by_hash(engine, scheme_id, evaluation.rule_hash)
        except LookupError:  # rule changed since the run -> evaluate again
            return None
        row = db.scalars(
            select(EligibilityEvaluation).where(
                EligibilityEvaluation.run_id == latest[0], EligibilityEvaluation.scheme_id == scheme_id
            )
        ).one()
        return evaluation, rule, row
    return None


def explain_for_citizen(
    db: Session,
    engine: RuleEngine,
    service: ExplanationService,
    citizen_id: uuid.UUID,
    scheme_id: str,
    question: str | None,
    language: str,
) -> ExplanationOut:
    """Explain the citizen's latest persisted evaluation of ``scheme_id`` (evaluating first if needed)."""
    profile_service.get_citizen(db, citizen_id)
    scheme_service.get_rule(engine, scheme_id)  # 404 for unknown / not currently effective schemes
    stored = _stored_scheme_evaluation(db, engine, citizen_id, scheme_id)
    evaluated_now = stored is None
    if stored is None:
        eligibility_service.evaluate_citizen(db, engine, citizen_id, None)
        stored = _stored_scheme_evaluation(db, engine, citizen_id, scheme_id)
        if stored is None:
            raise ApiError(404, "evaluation_not_found", f"Scheme '{scheme_id}' is not effective today")
    evaluation, rule, row = stored
    db.rollback()  # read-only from here on; release the transaction during the slow LLM call
    explanation = _explain_checked(service, evaluation, rule, question, language)
    return _out(explanation, rule, evaluation_row=row, evaluated_now=evaluated_now)


def explain_stateless(
    engine: RuleEngine,
    service: ExplanationService,
    scheme_id: str,
    profile: Mapping[str, ProfileValueIn],
    as_of: date | None,
    question: str | None,
    language: str,
) -> ExplanationOut:
    """Evaluate a supplied profile for one scheme (nothing persisted) and explain the result."""
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
    rule = next((r for r in engine.schemes(as_of) if r.scheme_id == scheme_id), None)
    if rule is None:
        raise ApiError(404, "scheme_not_found", f"Scheme '{scheme_id}' not found or not effective on {as_of}")
    evaluation = engine.evaluate_rule(rule, field_values, as_of)
    explanation = _explain_checked(service, evaluation, rule, question, language)
    return _out(explanation, rule)


def ask(
    engine: RuleEngine, service: ExplanationService, scheme_id: str, question: str, language: str
) -> ExplanationOut:
    """General question about a scheme, answered from its official documents (no personal decision)."""
    rule = scheme_service.get_rule(engine, scheme_id)
    explanation = service.answer_question(
        question, scheme_id=scheme_id, scheme_name=rule.name, language=language
    )
    if explanation.decision is not None:
        raise RuntimeError("general Q&A must not carry an eligibility decision")
    return _out(explanation, rule)
