"""Provenance trust boundaries, synthetic-only demo safety, startup guard and numeric input validation."""

from __future__ import annotations

import json
import uuid

import pytest
from app.config import Settings, UnsafeConfigurationError, check_deployment_safety, get_settings
from app.dependencies import get_explainer
from app.models import AuditLog, ProfileField
from sqlalchemy import select

from ai_modules.rag import Explanation
from ai_modules.rule_engine import FieldValue, Provenance

pytestmark = pytest.mark.integration


def _error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code, error
    return error


class _Echo:
    def explain(self, evaluation, rule, question=None, language="en") -> Explanation:
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
    app.dependency_overrides[get_explainer] = _Echo
    yield
    app.dependency_overrides.pop(get_explainer, None)


# ------------------------------------------------------------------------------- stateless provenance


@pytest.mark.parametrize("endpoint", ["/api/v1/eligibility/evaluate", "/api/v1/explain"])
def test_stateless_rejects_client_claimed_verified(client, explainer, endpoint):
    body = {
        "scheme_id": "pm_kisan",
        "profile": {
            "age": {"value": 46},
            "annual_family_income": {"value": 90000, "provenance": "verified"},
        },
    }
    if endpoint.endswith("evaluate"):
        body.pop("scheme_id")
    error = _error(client.post(endpoint, json=body), 422, "provenance_not_allowed")
    assert error["details"] == [
        {
            "loc": ["body", "profile", "annual_family_income", "provenance"],
            "msg": "Provenance 'verified' cannot be supplied by the caller; allowed: self_declared, "
            "document_extracted",
            "type": "provenance_not_allowed",
        }
    ]


def test_stateless_document_extracted_is_marked_client_asserted(client, app):
    body = {
        "profile": {
            "age": {"value": 19},
            "annual_family_income": {"value": 150000, "provenance": "document_extracted", "confidence": 0.95},
            "social_category": {"value": "obc", "provenance": "document_extracted", "confidence": 0.9},
        }
    }
    run = client.post("/api/v1/eligibility/evaluate", json=body).json()
    assert run["simulation"] is True
    assert run["client_asserted_provenance"] == ["annual_family_income", "social_category"]
    assert "client-asserted" in run["provenance_note"]
    assert run["run_id"] is None and run["stale"] is False
    # The what-if still runs with the asserted provenance (so document-gated conditions can be explored).
    values = {
        "age": FieldValue(19, Provenance.SELF_DECLARED),
        "annual_family_income": FieldValue(150000, Provenance.DOCUMENT_EXTRACTED, 0.95),
        "social_category": FieldValue("obc", Provenance.DOCUMENT_EXTRACTED, 0.9),
    }
    expected = {r.scheme_id: r.status.value for r in app.state.rule_engine.evaluate_all(values)}
    groups = ("eligible", "potentially_eligible", "not_eligible")
    got = {i["scheme_id"]: i["status"] for g in groups for i in run[g]}
    assert got == expected

    plain = client.post("/api/v1/eligibility/evaluate", json={"profile": {"age": {"value": 19}}}).json()
    assert plain["simulation"] is True and plain["client_asserted_provenance"] == []


def test_stateless_explain_is_marked_simulation(client, explainer):
    body = {
        "scheme_id": "pm_kisan",
        "profile": {
            "annual_family_income": {"value": 90000, "provenance": "document_extracted", "confidence": 1}
        },
    }
    out = client.post("/api/v1/explain", json=body).json()
    assert out["simulation"] is True and out["client_asserted_provenance"] == ["annual_family_income"]
    assert out["evaluation_id"] is None and out["provenance_note"]


def test_persisted_runs_are_not_simulations(client, citizen_id):
    run = client.post(f"/api/v1/citizens/{citizen_id}/evaluations").json()
    assert run["simulation"] is False and run["client_asserted_provenance"] == []
    assert run["provenance_note"] is None


# ------------------------------------------------------------------------- stored provenance via the API


@pytest.mark.parametrize(
    "value",
    [
        {"value": 19, "provenance": "verified"},
        {"value": 19, "provenance": "document_extracted", "confidence": 0.99},
    ],
)
def test_patch_cannot_smuggle_provenance(client, citizen_id, db, value):
    response = client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"age": value}})
    _error(response, 422, "validation_error")
    response = client.patch(
        f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"district": {"provenance": "verified"}}}
    )
    _error(response, 422, "validation_error")
    assert _stored_fields(db, citizen_id) == []


def test_patch_values_are_always_self_declared(client, citizen_id):
    url = f"/api/v1/citizens/{citizen_id}/profile"
    fields = client.patch(url, json={"fields": {"age": 19, "district": "Sehore"}}).json()["fields"]
    assert {f["provenance"] for f in fields.values()} == {"self_declared"}
    again = client.patch(url, json={"fields": {"age": "19"}}).json()["fields"]  # same value: unchanged
    assert again["age"]["provenance"] == "self_declared" and again["age"]["confidence"] is None


def test_enrollment_cannot_set_provenance(client, citizen_id):
    scheme_id = client.get("/api/v1/schemes").json()[0]["scheme_id"]
    url = f"/api/v1/citizens/{citizen_id}/enrollments/{scheme_id}"
    _error(client.put(url, json={"status": "enrolled", "provenance": "verified"}), 422, "validation_error")
    assert client.put(url, json={"status": "enrolled"}).json()["provenance"] == "self_declared"


# ------------------------------------------------------------------------------ synthetic-only demo


def test_non_synthetic_citizens_rejected_by_default(client, monkeypatch):
    error = _error(
        client.post("/api/v1/citizens", json={"label": "real person", "is_synthetic": False}),
        403,
        "non_synthetic_not_allowed",
    )
    assert "ALLOW_NON_SYNTHETIC" in error["message"]
    monkeypatch.setattr(get_settings(), "allow_non_synthetic", True)
    created = client.post("/api/v1/citizens", json={"is_synthetic": False})
    assert created.status_code == 201 and created.json()["is_synthetic"] is False


def test_label_with_identity_number_rejected(client):
    response = client.post("/api/v1/citizens", json={"label": "card 1234 5678 9012"})
    error = _error(response, 422, "validation_error")
    assert error["details"][0]["type"] == "sensitive_identifier"
    assert "1234" not in response.text


def _settings(**values) -> Settings:
    return Settings(_env_file=None, **values)


@pytest.mark.parametrize("env", ["development", "test", "demo", "Development"])
def test_startup_guard_allows_safe_environments(env):
    check_deployment_safety(_settings(app_env=env))


@pytest.mark.parametrize("env", ["production", "staging", "prod"])
def test_startup_guard_requires_acknowledgement(env):
    with pytest.raises(UnsafeConfigurationError, match="ALLOW_UNAUTHENTICATED=true"):
        check_deployment_safety(_settings(app_env=env))
    check_deployment_safety(_settings(app_env=env, allow_unauthenticated=True))


@pytest.mark.parametrize("origins", ["*", "http://localhost:5173, *"])
def test_startup_guard_rejects_wildcard_cors(origins):
    for values in ({}, {"app_env": "production", "allow_unauthenticated": True}):
        with pytest.raises(UnsafeConfigurationError, match="CORS_ORIGINS"):
            check_deployment_safety(_settings(cors_origins=origins, **values))


def test_create_app_refuses_to_start_when_unsafe(monkeypatch):
    from app.main import create_app

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("ALLOW_UNAUTHENTICATED", raising=False)
    get_settings.cache_clear()
    try:
        with pytest.raises(UnsafeConfigurationError):
            create_app()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()


# ----------------------------------------------------------------------------- numeric input validation


def _raw_json(client, method: str, url: str, payload: str):
    """Send JSON text as-is (NaN / Infinity / 1e400 are not producible with ``json=``-safe encoders)."""
    return client.request(method, url, content=payload.encode(), headers={"Content-Type": "application/json"})


def _evaluate_raw(client, payload: str):
    return _raw_json(client, "POST", "/api/v1/eligibility/evaluate", payload)


def _stored_fields(db, citizen_id: str) -> list:
    return db.scalars(select(ProfileField).where(ProfileField.citizen_id == uuid.UUID(citizen_id))).all()


NUMERIC_CASES = [
    ("age", "NaN", "must be a finite number"),
    ("annual_family_income", "NaN", "must be a finite number"),
    ("annual_family_income", "Infinity", "must be a finite number"),
    ("annual_family_income", "-Infinity", "must be a finite number"),
    ("annual_family_income", "1e400", "must be a finite number"),
    ("annual_family_income", '"nan"', "must be a number"),
    ("annual_family_income", '"inf"', "must be a number"),
    ("annual_family_income", "-1", "must be >= 0"),
    ("annual_family_income", '"12abc"', "must be a number"),
    ("annual_family_income", '"1_000"', "must be a number"),
    ("annual_family_income", "true", "must be a number"),
    ("annual_family_income", "1e30", "must be <= 1000000000000"),
    ("age", "18.5", "must be an integer"),
    ("age", '"18.5"', "must be an integer"),
    ("age", "true", "must be an integer"),
    ("age", "-1", "must be >= 0"),
    ("class12_percentage", "NaN", "must be a finite number"),
    ("district", "123", "must be a string"),
    ("domicile_state", "true", "must be a string"),
    ("owns_pucca_house", "2", "must be a boolean"),
]


@pytest.mark.parametrize(("field", "raw", "fragment"), NUMERIC_CASES)
def test_patch_profile_rejects_invalid_numbers(client, citizen_id, db, field, raw, fragment):
    response = _raw_json(
        client, "PATCH", f"/api/v1/citizens/{citizen_id}/profile", f'{{"fields": {{"{field}": {raw}}}}}'
    )
    error = _error(response, 422, "validation_error")
    assert error["details"][0]["loc"] == ["body", "fields", field]
    assert fragment in error["details"][0]["msg"]
    assert _stored_fields(db, citizen_id) == []


@pytest.mark.parametrize(("field", "raw", "fragment"), NUMERIC_CASES)
def test_stateless_evaluate_rejects_invalid_numbers(client, field, raw, fragment):
    payload = f'{{"profile": {{"{field}": {{"value": {raw}}}}}}}'
    error = _error(_evaluate_raw(client, payload), 422, "validation_error")
    assert error["details"][0]["loc"] == ["body", "profile", field]
    assert fragment in error["details"][0]["msg"]


@pytest.mark.parametrize("confidence", ["NaN", "Infinity", "1.5", "-0.1", '"high"', "true"])
def test_stateless_rejects_invalid_confidence(client, confidence):
    payload = (
        '{"profile": {"annual_family_income": {"value": 1000, "provenance": "document_extracted", '
        f'"confidence": {confidence}}}}}}}'
    )
    error = _error(_evaluate_raw(client, payload), 422, "validation_error")
    assert error["details"][0]["loc"][:4] == ["body", "profile", "annual_family_income", "confidence"]


def test_integral_floats_are_accepted_and_normalised(client, citizen_id):
    fields = client.patch(
        f"/api/v1/citizens/{citizen_id}/profile",
        json={"fields": {"age": 18.0, "class12_passing_year": "2025.0", "annual_family_income": "180000"}},
    ).json()["fields"]
    assert fields["age"]["value"] == 18 and isinstance(fields["age"]["value"], int)
    assert fields["class12_passing_year"]["value"] == 2025
    assert fields["annual_family_income"]["value"] == 180000


def test_audit_rows_never_contain_values(client, citizen_id, db):
    secret = "Qwzxv Synthetictestperson"
    client.patch(
        f"/api/v1/citizens/{citizen_id}/profile",
        json={"fields": {"full_name": secret, "annual_family_income": 987654}},
    )
    client.post(f"/api/v1/citizens/{citizen_id}/evaluations")
    client.delete(f"/api/v1/citizens/{citizen_id}")
    rows = db.scalars(select(AuditLog).where(AuditLog.entity_id == citizen_id)).all()
    assert {r.action for r in rows} >= {"create", "profile_update", "evaluation_run", "delete"}
    dumped = json.dumps([r.details for r in rows])
    assert secret not in dumped and "987654" not in dumped
