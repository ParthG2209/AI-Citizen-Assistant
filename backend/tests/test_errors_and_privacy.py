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
        # No `with`: a second lifespan would close the shared app's knowledge graph on exit.
        response = TestClient(app, raise_server_exceptions=False).get("/__test_boom")
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
        content=b" " * (limit + 64 * 1024 + 1),
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


def _responses(schema: dict, method: str, path: str) -> dict:
    return schema["paths"][f"/api/v1{path}"][method]["responses"]


def test_openapi_declares_the_errors_routes_actually_return(app):
    schema = app.openapi()
    error_ref = "#/components/schemas/ErrorResponse"
    assert not {"HTTPValidationError", "ValidationError"} & set(schema["components"]["schemas"])
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            for status, response in operation["responses"].items():
                if int(status) >= 400 and path != "/api/v1/ready":
                    ref = response["content"]["application/json"]["schema"]["$ref"]
                    assert ref == error_ref, (method, path, status)
                if "{citizen_id}" in path or "{document_id}" in path or "{evaluation_id}" in path:
                    assert "422" in operation["responses"], (method, path)  # invalid UUID

    assert set(_responses(schema, "get", "/profile-fields")) == {"200"}
    assert "non_synthetic_not_allowed" in _responses(schema, "post", "/citizens")["403"]["description"]
    latest = _responses(schema, "get", "/citizens/{citizen_id}/evaluations/latest")
    assert "rule_engine_unavailable" in latest["503"]["description"]
    assert "evaluation_not_found" in latest["404"]["description"]
    for path in ("/eligibility/evaluate", "/explain"):
        assert "provenance_not_allowed" in _responses(schema, "post", path)["422"]["description"]
    upload = _responses(schema, "post", "/citizens/{citizen_id}/documents")
    assert {"200", "201", "404", "413", "415", "422", "503"} <= set(upload)
    for method, path in (
        ("get", "/citizens/{citizen_id}/documents"),
        ("get", "/documents/{document_id}"),
        ("delete", "/documents/{document_id}"),
    ):
        assert not {"413", "415", "503"} & set(_responses(schema, method, path)), (method, path)
    assert "422" not in _responses(schema, "get", "/schemes/{scheme_id}")  # str path: cannot fail
