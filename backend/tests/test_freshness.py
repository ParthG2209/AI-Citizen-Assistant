"""Evaluation freshness: stale detection, and recommendations / explanations always using the current run.

A run is stale when the stored profile (values, provenance, OCR confidence) or the rules in effect
today differ from what it was evaluated with. Fingerprints are timestamp-free.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import date

import pytest
from app.dependencies import get_explainer
from app.models import EligibilityEvaluation, ProfileField
from app.services.eligibility_service import profile_fingerprint, rule_set_hash
from sqlalchemy import func, select, update

from ai_modules.rag import Explanation
from ai_modules.rule_engine import FieldValue, Provenance, RuleEngine
from ai_modules.rule_engine.loader import compute_rule_hash

pytestmark = pytest.mark.integration

FARMER = {
    "age": 46,
    "gender": "male",
    "domicile_state": "Madhya Pradesh",
    "district": "Vidisha",
    "residence_type": "rural",
    "occupation": "farmer",
    "social_category": "sc",
    "annual_family_income": 90000,
    "family_owns_cultivable_land": True,
    "owns_pucca_house": False,
    "family_member_income_tax_payer": False,
    "family_member_government_employee": False,
    "family_member_pensioner_above_10k": False,
    "listed_in_secc_or_nfsa": True,
}


class EchoExplainer:
    """Echoes the engine decision (no RAG); records the evaluations it was given."""

    def __init__(self) -> None:
        self.calls: list = []

    def explain(self, evaluation, rule, question=None, language="en") -> Explanation:
        self.calls.append((evaluation, rule))
        return Explanation(
            answer="test double",
            decision=evaluation.status.value,
            citations=(),
            grounded=False,
            provider="none",
            model=None,
            uncertainty=None,
            language=language,
            scheme_id=rule.scheme_id,
        )


@pytest.fixture
def explainer(app):
    double = EchoExplainer()
    app.dependency_overrides[get_explainer] = lambda: double
    yield double
    app.dependency_overrides.pop(get_explainer, None)


def _citizen(client, profile=FARMER) -> str:
    citizen_id = client.post("/api/v1/citizens", json={"label": "pytest synthetic"}).json()["id"]
    assert client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": profile}).status_code == 200
    return citizen_id


def _latest(client, citizen_id) -> dict:
    response = client.get(f"/api/v1/citizens/{citizen_id}/evaluations/latest")
    assert response.status_code == 200, response.text
    return response.json()


def _items(run: dict) -> dict[str, dict]:
    return {i["scheme_id"]: i for g in ("eligible", "potentially_eligible", "not_eligible") for i in run[g]}


def _recommend(client, citizen_id) -> dict:
    response = client.get(f"/api/v1/citizens/{citizen_id}/recommendations")
    assert response.status_code == 200, response.text
    return response.json()


def _explain(client, citizen_id, scheme_id="pm_kisan") -> dict:
    response = client.post(f"/api/v1/citizens/{citizen_id}/schemes/{scheme_id}/explanation")
    assert response.status_code == 200, response.text
    return response.json()


def _patch(client, citizen_id, fields: dict) -> None:
    response = client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": fields})
    assert response.status_code == 200, response.text


def _run_count(db, citizen_id) -> int:
    return db.scalar(
        select(func.count(func.distinct(EligibilityEvaluation.run_id))).where(
            EligibilityEvaluation.citizen_id == uuid.UUID(citizen_id)
        )
    )


def _assert_current(client, citizen_id, recs: dict, explanation: dict, scheme_id="pm_kisan") -> dict:
    """Recommendations and explanation both use the latest run, which is fresh."""
    latest = _latest(client, citizen_id)
    assert latest["stale"] is False and latest["stale_reasons"] == []
    items = _items(latest)
    assert recs["run_id"] == latest["run_id"] == explanation["run_id"]
    assert recs["profile_fingerprint"] == latest["profile_fingerprint"] == explanation["profile_fingerprint"]
    assert recs["rule_set_hash"] == latest["rule_set_hash"] == explanation["rule_set_hash"]
    assert {e["scheme_id"]: e["evaluation_id"] for e in recs["evaluations"]} == {
        s: i["id"] for s, i in items.items()
    }
    assert {e["scheme_id"]: e["rule_version"] for e in recs["evaluations"]} == {
        s: i["rule_version"] for s, i in items.items()
    }
    assert explanation["evaluation_id"] == items[scheme_id]["id"]
    assert explanation["rule_version"] == items[scheme_id]["rule_version"]
    assert explanation["decision"] == items[scheme_id]["status"]
    return latest


def test_fingerprint_is_deterministic_and_timestamp_free():
    a = {
        "age": FieldValue(19, Provenance.SELF_DECLARED),
        "annual_family_income": FieldValue(180000, Provenance.DOCUMENT_EXTRACTED, 0.93),
    }
    b = {
        "annual_family_income": FieldValue(180000.0, Provenance.DOCUMENT_EXTRACTED, 0.93),
        "age": FieldValue("19", Provenance.SELF_DECLARED),
    }
    assert profile_fingerprint(a) == profile_fingerprint(b)  # order / int-vs-float / coercion
    for changed in (
        {**a, "age": FieldValue(20, Provenance.SELF_DECLARED)},  # value
        {**a, "age": FieldValue(19, Provenance.DOCUMENT_EXTRACTED, 0.93)},  # provenance
        {**a, "annual_family_income": FieldValue(180000, Provenance.DOCUMENT_EXTRACTED, 0.94)},  # confidence
        {"age": a["age"]},  # removed field
    ):
        assert profile_fingerprint(changed) != profile_fingerprint(a)


def test_rule_set_hash_tracks_versions_and_threshold(app, client):
    engine = app.state.rule_engine
    assert rule_set_hash(engine) == rule_set_hash(RuleEngine(rules=_all_rules(engine)))
    stricter = RuleEngine(rules=_all_rules(engine), confidence_threshold=0.9)
    assert rule_set_hash(stricter) != rule_set_hash(engine)
    assert rule_set_hash(_bumped_engine(engine, "pm_kisan")) != rule_set_hash(engine)


def test_profile_patch_and_income_change_make_runs_stale(client, explainer, db):
    citizen_id = _citizen(client)
    first_recs = _recommend(client, citizen_id)
    first = _assert_current(client, citizen_id, first_recs, _explain(client, citizen_id))
    assert first_recs["evaluated_now"] is True

    _patch(client, citizen_id, {"annual_family_income": 95000})
    stale = _latest(client, citizen_id)
    assert stale["run_id"] == first["run_id"]
    assert stale["stale"] is True and stale["stale_reasons"] == ["profile_changed"]

    recs = _recommend(client, citizen_id)
    assert recs["evaluated_now"] is True and recs["stale_reasons"] == ["profile_changed"]
    explanation = _explain(client, citizen_id)
    assert explanation["evaluated_now"] is False  # recommendations already refreshed the run
    second = _assert_current(client, citizen_id, recs, explanation)
    assert second["run_id"] != first["run_id"]
    assert second["profile_fingerprint"] != first["profile_fingerprint"]
    before_hashes = {s: i["input_hash"] for s, i in _items(first).items()}
    assert {s: i["input_hash"] for s, i in _items(second).items()} != before_hashes

    # Explanation first this time: it refreshes the run, then recommendations reuse it.
    _patch(client, citizen_id, {"family_member_income_tax_payer": True})
    explanation = _explain(client, citizen_id)
    assert explanation["evaluated_now"] is True and explanation["stale_reasons"] == ["profile_changed"]
    assert explanation["decision"] == "not_eligible"
    recs = _recommend(client, citizen_id)
    assert recs["evaluated_now"] is False
    third = _assert_current(client, citizen_id, recs, explanation)

    # History is kept: every earlier run and its evaluation rows still exist.
    assert _run_count(db, citizen_id) == 3
    for run in (first, second):
        old = _items(run)["pm_kisan"]
        assert client.get(f"/api/v1/evaluations/{old['id']}").json()["run_id"] == run["run_id"]
    assert third["run_id"] not in {first["run_id"], second["run_id"]}


def test_same_second_edits_are_detected_by_content_not_time(client, explainer):
    citizen_id = _citizen(client)
    base = _recommend(client, citizen_id)
    # Two edits in immediate succession (typically within the same second / DB timestamp tick).
    _patch(client, citizen_id, {"annual_family_income": 91000})
    after_first = _recommend(client, citizen_id)
    _patch(client, citizen_id, {"annual_family_income": 92000})
    after_second = _recommend(client, citizen_id)
    assert len({base["run_id"], after_first["run_id"], after_second["run_id"]}) == 3
    assert after_second["stale_reasons"] == ["profile_changed"]
    _assert_current(client, citizen_id, after_second, _explain(client, citizen_id))

    # A no-op PATCH (same value) bumps no fingerprint input, so the run stays fresh and is reused.
    _patch(client, citizen_id, {"annual_family_income": 92000})
    assert _latest(client, citizen_id)["stale"] is False
    assert _recommend(client, citizen_id)["run_id"] == after_second["run_id"]


def test_confidence_or_provenance_change_alone_is_stale(client, explainer, db):
    citizen_id = _citizen(client)
    run = _recommend(client, citizen_id)
    db.execute(
        update(ProfileField)
        .where(ProfileField.citizen_id == uuid.UUID(citizen_id), ProfileField.field_name == "age")
        .values(provenance="verified")  # simulates a future verification step (no API sets it)
    )
    db.commit()
    assert _latest(client, citizen_id)["stale_reasons"] == ["profile_changed"]
    assert _recommend(client, citizen_id)["run_id"] != run["run_id"]


def test_enrollment_change_does_not_stale_but_is_read_live(client, explainer):
    citizen_id = _citizen(client)
    before = _recommend(client, citizen_id)
    candidates = [r["scheme_id"] for r in before["unclaimed"] + before["potentially_eligible"]]
    assert candidates
    client.put(f"/api/v1/citizens/{citizen_id}/enrollments/{candidates[0]}", json={"status": "enrolled"})
    assert _latest(client, citizen_id)["stale"] is False
    after = _recommend(client, citizen_id)
    assert after["evaluated_now"] is False and after["run_id"] == before["run_id"]
    assert after["enrolled_scheme_ids"] == [candidates[0]]
    assert candidates[0] not in {r["scheme_id"] for r in after["unclaimed"] + after["potentially_eligible"]}
    _assert_current(client, citizen_id, after, _explain(client, citizen_id))


def _all_rules(engine: RuleEngine) -> list:
    return [rule for scheme_id in engine.scheme_ids() for rule in engine.versions(scheme_id)]


def _bumped_engine(engine: RuleEngine, scheme_id: str) -> RuleEngine:
    """Same rules, but today's version of ``scheme_id`` replaced by a new version with new content."""
    current = engine.get(scheme_id)
    bumped = dataclasses.replace(
        current,
        version=f"{current.version}-fresh-test",
        limitations=(*current.limitations, "Test-only rule revision."),
    )
    bumped = dataclasses.replace(bumped, rule_hash=compute_rule_hash(bumped))
    rules = [bumped if r is current else r for r in _all_rules(engine)]
    return RuleEngine(rules=rules, confidence_threshold=engine.confidence_threshold)


def test_rule_version_change_makes_runs_stale(app, client, explainer, db):
    citizen_id = _citizen(client)
    original_engine = app.state.rule_engine
    before = _assert_current(client, citizen_id, _recommend(client, citizen_id), _explain(client, citizen_id))
    app.state.rule_engine = _bumped_engine(original_engine, "pm_kisan")
    try:
        stale = _latest(client, citizen_id)
        assert stale["run_id"] == before["run_id"]
        assert stale["stale"] is True and stale["stale_reasons"] == ["rule_version_changed"]

        explanation = _explain(client, citizen_id)
        assert explanation["evaluated_now"] is True
        assert explanation["stale_reasons"] == ["rule_version_changed"]
        assert explanation["rule_version"].endswith("-fresh-test")
        recs = _recommend(client, citizen_id)
        assert recs["evaluated_now"] is False
        after = _assert_current(client, citizen_id, recs, explanation)
        assert after["rule_set_hash"] != before["rule_set_hash"]
        assert after["profile_fingerprint"] == before["profile_fingerprint"]
        used = {e["scheme_id"]: e["rule_version"] for e in recs["evaluations"]}
        assert used["pm_kisan"].endswith("-fresh-test")
    finally:
        app.state.rule_engine = original_engine
    # Back on the original rules, the latest run (made with the bumped rule) is stale again.
    assert _latest(client, citizen_id)["stale_reasons"] == ["rule_version_changed"]
    old = _items(before)["pm_kisan"]
    assert client.get(f"/api/v1/evaluations/{old['id']}").json()["rule_version"] == old["rule_version"]
    assert _run_count(db, citizen_id) == 2


def test_runs_without_fingerprints_are_stale(client, explainer, db):
    citizen_id = _citizen(client)
    run = client.post(f"/api/v1/citizens/{citizen_id}/evaluations").json()
    assert run["profile_fingerprint"] and run["rule_set_hash"] and run["stale"] is False
    db.execute(
        update(EligibilityEvaluation)
        .where(EligibilityEvaluation.run_id == uuid.UUID(run["run_id"]))
        .values(profile_fingerprint=None, rule_set_hash=None)  # as stored before the migration
    )
    db.commit()
    latest = _latest(client, citizen_id)
    assert latest["stale_reasons"] == ["profile_changed", "rule_version_changed"]
    assert _recommend(client, citizen_id)["run_id"] != run["run_id"]


def test_explicit_as_of_run_is_fresh_when_same_rules_apply(client, explainer):
    citizen_id = _citizen(client)
    run = client.post(
        f"/api/v1/citizens/{citizen_id}/evaluations", json={"as_of": date.today().isoformat()}
    ).json()
    assert _latest(client, citizen_id)["stale"] is False
    assert _recommend(client, citizen_id)["run_id"] == run["run_id"]
