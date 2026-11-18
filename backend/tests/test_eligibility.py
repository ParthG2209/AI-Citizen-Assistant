from __future__ import annotations

import uuid
from datetime import date

import pytest
from app.config import get_settings
from app.models import ConditionResultRow, EligibilityEvaluation, Scheme, SchemeRuleVersion
from sqlalchemy import func, select

from ai_modules.rule_engine import FieldValue, Provenance, RuleEngine
from ai_modules.rule_engine.result import DISCLAIMER

pytestmark = pytest.mark.integration


def _engine() -> RuleEngine:
    """An independent load of the same rules the API uses (RULES_DIR or the engine default)."""
    return RuleEngine(rules_dir=get_settings().rules_dir)


PROFILE = {
    "age": 19,
    "gender": "female",
    "domicile_state": "Madhya Pradesh",
    "district": "Sehore",
    "residence_type": "rural",
    "annual_family_income": 150000,
    "social_category": "obc",
    "occupation": "student",
    "enrolled_in_higher_education": True,
    "class12_percentage": 82.4,
    "class12_board": "mpbse",
    "family_owns_cultivable_land": False,
}


def _expected_statuses(profile: dict, as_of: date | None = None) -> dict[str, str]:
    """Independent run of the rule engine: the API must report exactly the engine's decisions."""
    values = {name: FieldValue(value, Provenance.SELF_DECLARED) for name, value in profile.items()}
    return {r.scheme_id: r.status.value for r in _engine().evaluate_all(values, as_of=as_of)}


def _statuses(run: dict) -> dict[str, str]:
    out = {}
    for group in ("eligible", "potentially_eligible", "not_eligible"):
        for item in run[group]:
            assert item["status"] == group
            out[item["scheme_id"]] = item["status"]
    return out


def _count_tree(conditions: list[dict]) -> int:
    return sum(1 + _count_tree(c["children"]) for c in conditions)


def test_list_and_get_schemes(client):
    schemes = client.get("/api/v1/schemes").json()
    engine_ids = sorted(r.scheme_id for r in _engine().schemes())
    assert sorted(s["scheme_id"] for s in schemes) == engine_ids
    first = schemes[0]
    assert first["verification_status"] in {"verified", "partially_verified", "unverified"}
    assert "type" in first["benefit"]

    detail = client.get(f"/api/v1/schemes/{first['scheme_id']}").json()
    assert detail["conditions"] and detail["sources"]
    assert all(source["url"].startswith("http") for source in detail["sources"])
    for key in ("required_documents", "limitations", "application_steps", "relationships", "rule_hash"):
        assert key in detail


def test_unknown_scheme_is_404(client):
    response = client.get("/api/v1/schemes/not_a_scheme")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "scheme_not_found"


def test_rule_versions_synced_at_startup(db):
    engine = _engine()
    for scheme_id in engine.scheme_ids():
        for rule in engine.versions(scheme_id):
            stored = db.scalars(
                select(SchemeRuleVersion).where(
                    SchemeRuleVersion.scheme_id == scheme_id, SchemeRuleVersion.version == rule.version
                )
            ).one()
            assert stored.rule_hash == rule.rule_hash
            assert stored.definition["scheme_id"] == scheme_id
        assert db.get(Scheme, scheme_id).active is True


def test_evaluation_persists_runs_and_condition_rows(client, citizen_id, db):
    client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": PROFILE})
    response = client.post(f"/api/v1/citizens/{citizen_id}/evaluations", json={})
    assert response.status_code == 201, response.text
    run = response.json()

    assert run["disclaimer"] == DISCLAIMER
    assert _statuses(run) == _expected_statuses(PROFILE)
    assert sum(run["summary"].values()) == len(_statuses(run))

    items = run["eligible"] + run["potentially_eligible"] + run["not_eligible"]
    evaluations = db.scalars(
        select(EligibilityEvaluation).where(EligibilityEvaluation.run_id == uuid.UUID(run["run_id"]))
    ).all()
    assert {str(e.id) for e in evaluations} == {item["id"] for item in items}
    for item in items:
        stored = db.scalar(
            select(func.count())
            .select_from(ConditionResultRow)
            .where(ConditionResultRow.evaluation_id == uuid.UUID(item["id"]))
        )
        assert stored == _count_tree(item["conditions"]) > 0
        for condition in item["conditions"]:
            assert condition["outcome"] in {"pass", "fail", "missing"}
            assert condition["message"]


def test_latest_and_single_evaluation_match_post(client, citizen_id):
    client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": PROFILE})
    first = client.post(f"/api/v1/citizens/{citizen_id}/evaluations").json()
    client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"annual_family_income": 900000}})
    second = client.post(f"/api/v1/citizens/{citizen_id}/evaluations").json()
    assert first["run_id"] != second["run_id"]

    latest = client.get(f"/api/v1/citizens/{citizen_id}/evaluations/latest").json()
    assert latest["run_id"] == second["run_id"]
    assert _statuses(latest) == _statuses(second)

    item = (second["eligible"] + second["potentially_eligible"] + second["not_eligible"])[0]
    detail = client.get(f"/api/v1/evaluations/{item['id']}").json()
    assert detail["run_id"] == second["run_id"]
    assert detail["citizen_id"] == citizen_id
    assert detail["conditions"] == item["conditions"]
    assert detail["input_hash"] == item["input_hash"]
    assert detail["rule_hash"] == item["rule_hash"]


def test_evaluation_as_of_is_respected(client, citizen_id):
    client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": PROFILE})
    as_of = date.today()
    run = client.post(f"/api/v1/citizens/{citizen_id}/evaluations", json={"as_of": as_of.isoformat()}).json()
    assert run["as_of"] == as_of.isoformat()
    assert _statuses(run) == _expected_statuses(PROFILE, as_of)


def test_empty_profile_is_never_eligible(client, citizen_id):
    run = client.post(f"/api/v1/citizens/{citizen_id}/evaluations").json()
    assert run["eligible"] == []
    assert _statuses(run) == _expected_statuses({})


def test_latest_without_runs_is_404(client, citizen_id):
    response = client.get(f"/api/v1/citizens/{citizen_id}/evaluations/latest")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "evaluation_not_found"


def test_unknown_evaluation_and_citizen_are_404(client):
    assert client.get(f"/api/v1/evaluations/{uuid.uuid4()}").status_code == 404
    response = client.post(f"/api/v1/citizens/{uuid.uuid4()}/evaluations")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "citizen_not_found"


def test_stateless_evaluation_persists_nothing(client, db):
    before = db.scalar(select(func.count()).select_from(EligibilityEvaluation))
    body = {
        "profile": {name: {"value": value, "provenance": "self_declared"} for name, value in PROFILE.items()}
    }
    response = client.post("/api/v1/eligibility/evaluate", json=body)
    assert response.status_code == 200, response.text
    run = response.json()
    assert run["run_id"] is None and run["citizen_id"] is None
    assert _statuses(run) == _expected_statuses(PROFILE)
    assert all(
        item["id"] is None for item in run["eligible"] + run["potentially_eligible"] + run["not_eligible"]
    )
    assert db.scalar(select(func.count()).select_from(EligibilityEvaluation)) == before


def test_stateless_low_confidence_document_value_matches_engine(client):
    body = {
        "profile": {
            "annual_family_income": {"value": 150000, "provenance": "document_extracted", "confidence": 0.2},
            "age": {"value": 19},
        }
    }
    run = client.post("/api/v1/eligibility/evaluate", json=body).json()
    values = {
        "annual_family_income": FieldValue(150000, Provenance.DOCUMENT_EXTRACTED, 0.2),
        "age": FieldValue(19, Provenance.SELF_DECLARED),
    }
    expected = {r.scheme_id: r.status.value for r in _engine().evaluate_all(values)}
    assert _statuses(run) == expected


@pytest.mark.parametrize(
    ("profile", "loc"),
    [
        ({"age": {"value": "old"}}, ["body", "profile", "age"]),
        ({"aadhaar": {"value": "1"}}, ["body", "profile", "aadhaar"]),
        ({"age": {"value": None}}, ["body", "profile", "age"]),
    ],
)
def test_stateless_validation(client, profile, loc):
    response = client.post("/api/v1/eligibility/evaluate", json={"profile": profile})
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["loc"] == loc


def test_stateless_rejects_bad_provenance_and_confidence(client):
    bad_provenance = {"profile": {"age": {"value": 19, "provenance": "trusted"}}}
    bad_confidence = {
        "profile": {"age": {"value": 19, "provenance": "document_extracted", "confidence": 1.5}}
    }
    for body in (bad_provenance, bad_confidence):
        response = client.post("/api/v1/eligibility/evaluate", json=body)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"
