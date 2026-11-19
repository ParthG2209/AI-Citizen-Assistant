"""Shared response shapes. Successful responses are plain resources; errors use ``ErrorResponse``."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None


class ErrorResponse(BaseModel):
    error: ErrorBody


ErrorSpec = tuple[int, str]  # (HTTP status, error code(s) with an optional hint)

# Errors shared by many routes.
VALIDATION_ERROR: ErrorSpec = (422, "validation_error")
# Any POST / PUT / PATCH: the middleware rejects a Content-Length above MAX_UPLOAD_MB.
PAYLOAD_TOO_LARGE: ErrorSpec = (413, "payload_too_large")
CITIZEN_NOT_FOUND: ErrorSpec = (404, "citizen_not_found")
SCHEME_NOT_FOUND: ErrorSpec = (404, "scheme_not_found")
RULE_ENGINE_UNAVAILABLE: ErrorSpec = (503, "rule_engine_unavailable: see GET /api/v1/ready")
RULE_VERSION_CONFLICT: ErrorSpec = (409, "rule_version_conflict: a rule changed without a version bump")


def error_responses(*errors: ErrorSpec) -> dict[int | str, dict[str, Any]]:
    """OpenAPI ``responses=`` documenting exactly the errors a route can return (shared error schema).

    Several codes with the same status are joined in one description.
    """
    responses: dict[int | str, dict[str, Any]] = {}
    for status, codes in errors:
        entry = responses.setdefault(status, {"model": ErrorResponse, "description": codes})
        if entry["description"] != codes:
            entry["description"] = f"{entry['description']} | {codes}"
    return responses
