from __future__ import annotations

import io
import logging

import pytest
from app.utils.logger import JsonFormatter
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message", "details"}
    assert body["error"]["code"] == code
    return body["error"]


def test_unknown_route_404(client):
    _assert_error(client.get("/api/v1/nope"), 404, "not_found")


def test_method_not_allowed(client):
    _assert_error(client.put("/api/v1/health"), 405, "method_not_allowed")


def test_invalid_uuid_is_422(client):
    error = _assert_error(client.get("/api/v1/citizens/not-a-uuid"), 422, "validation_error")
    assert error["details"][0]["loc"] == ["path", "citizen_id"]


def test_malformed_json_is_422(client, citizen_id):
    response = client.patch(
        f"/api/v1/citizens/{citizen_id}/profile",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    _assert_error(response, 422, "validation_error")


def test_validation_errors_do_not_echo_input(client):
    response = client.post("/api/v1/citizens", json={"label": "x" * 200, "is_synthetic": True})
    error = _assert_error(response, 422, "validation_error")
    assert all(set(d) == {"loc", "msg", "type"} for d in error["details"])
    assert "x" * 200 not in response.text


def test_unexpected_fields_rejected(client):
    _assert_error(
        client.post("/api/v1/citizens", json={"aadhaar_number": "123412341234"}), 422, "validation_error"
    )


def test_unhandled_error_is_500_without_stack_trace(app):
    def boom() -> None:
        raise RuntimeError("secret internal detail")

    app.add_api_route("/__test_boom", boom)
    try:
        with TestClient(app, raise_server_exceptions=False) as local:
            response = local.get("/__test_boom")
    finally:
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/__test_boom"]
    error = _assert_error(response, 500, "internal_error")
    assert "secret internal detail" not in response.text
    assert "Traceback" not in response.text
    assert error["details"] is None


def test_oversized_body_is_413(client, citizen_id):
    from app.config import get_settings

    limit = get_settings().max_upload_bytes
    response = client.patch(
        f"/api/v1/citizens/{citizen_id}/profile",
        content=b" " * (limit + 1),
        headers={"Content-Type": "application/json"},
    )
    _assert_error(response, 413, "payload_too_large")


@pytest.mark.parametrize("value", ["1234 5678 9012", "123456789012", "Ram 1234-5678-9012", "123456789"])
def test_identity_numbers_rejected_in_text_fields(client, citizen_id, value):
    for field in ("full_name", "district"):
        response = client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {field: value}})
        error = _assert_error(response, 422, "validation_error")
        assert error["details"][0]["type"] == "sensitive_identifier"
        assert value not in response.text


@pytest.mark.parametrize("field", ["aadhaar", "aadhaar_number", "samagra_id", "samagra_id_number"])
def test_identity_fields_not_in_catalog(client, citizen_id, field):
    response = client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {field: "x"}})
    _assert_error(response, 422, "validation_error")


def test_logs_never_contain_profile_values(client, citizen_id):
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    previous_level = root.level
    root.setLevel(logging.DEBUG)
    secret_name = "Zxqvyn Synthetictestname"
    secret_district = "Qwertypur"
    try:
        client.patch(
            f"/api/v1/citizens/{citizen_id}/profile",
            json={
                "fields": {
                    "full_name": secret_name,
                    "district": secret_district,
                    "annual_family_income": 987654,
                }
            },
        )
        client.post(f"/api/v1/citizens/{citizen_id}/evaluations")
        client.post(
            "/api/v1/eligibility/evaluate",
            json={
                "profile": {"district": {"value": secret_district}, "annual_family_income": {"value": 987654}}
            },
        )
        client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"age": "secret-bad-age"}})
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
    output = stream.getvalue()
    assert "profile_updated" in output  # the event itself is logged ...
    assert "full_name" in output  # ... with field names ...
    for secret in (secret_name, secret_district, "987654", "987,654", "9,87,654", "secret-bad-age"):
        assert secret not in output  # ... but never values
