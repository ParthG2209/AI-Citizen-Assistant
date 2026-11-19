from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_health_is_live(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_request_id_is_echoed(client):
    response = client.get("/api/v1/health", headers={"X-Request-ID": "test-req-123"})
    assert response.headers["X-Request-ID"] == "test-req-123"


def test_ready_reports_database_rules_and_optional_services(client):
    response = client.get("/api/v1/ready")
    body = response.json()
    assert response.status_code == 200, body
    assert body["status"] == "ready"
    checks = body["checks"]
    assert checks["database"]["status"] == "ok"
    assert checks["rule_engine"]["status"] == "ok"
    assert checks["rule_engine"]["schemes"] >= 1
    # Optional services are reported but never decide readiness.
    assert checks["neo4j"]["status"] in {"reachable", "unreachable"}
    assert checks["ollama"]["status"] in {"reachable", "unreachable"}


def test_openapi_documents_routes(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    for path in (
        "/api/v1/citizens",
        "/api/v1/citizens/{citizen_id}/profile",
        "/api/v1/schemes",
        "/api/v1/citizens/{citizen_id}/evaluations",
        "/api/v1/eligibility/evaluate",
    ):
        assert path in paths
    assert {tag["name"] for tag in schema["tags"]} >= {"health", "citizens", "schemes", "eligibility"}


def test_cors_allows_configured_origin(client):
    response = client.options(
        "/api/v1/citizens",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

    simple = client.get("/api/v1/health", headers={"Origin": "http://localhost:5173"})
    assert simple.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_rejects_unknown_origin(client):
    response = client.get("/api/v1/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers
