"""FastAPI application factory.

Run from the repository root:
    python -m uvicorn app.main:app --app-dir backend --port 8000
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import get_settings
from app.errors import error_response, register_error_handlers
from app.services.rules_state import (
    close_knowledge_graph,
    connect_knowledge_graph,
    ensure_rules_synced,
    load_rule_engine,
)
from app.utils.logger import configure_logging, get_logger, request_id_var

logger = get_logger("app.access")

OPENAPI_TAGS = [
    {"name": "health", "description": "Liveness and readiness probes."},
    {
        "name": "citizens",
        "description": "Citizen records, typed profile fields with provenance, enrollments.",
    },
    {
        "name": "documents",
        "description": "Certificate upload with OCR. Files are processed in memory and never stored.",
    },
    {
        "name": "schemes",
        "description": "Scheme rules derived from official sources, with verification status.",
    },
    {
        "name": "eligibility",
        "description": "Deterministic rule-engine evaluations; indicative, not official approvals.",
    },
    {
        "name": "recommendations",
        "description": "Benefit gaps, ranked next steps and complementary schemes from stored evaluations.",
    },
    {
        "name": "explanations",
        "description": "Answers grounded in official scheme documents with citations; the LLM never decides.",
    },
    {
        "name": "analytics",
        "description": "Scheme coverage by district, block and village. SYNTHETIC demo data, not official.",
    },
]

_BODY_METHODS = {"POST", "PUT", "PATCH"}
# Early reject on Content-Length; uploads are additionally capped while the file is read.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    load_rule_engine(app)
    ensure_rules_synced(app)
    connect_knowledge_graph(app)
    try:
        yield
    finally:
        close_knowledge_graph(app)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="AI Citizen Assistant API",
        version="0.1.0",
        description="Welfare-scheme eligibility for Madhya Pradesh and central schemes. Eligibility is "
        "decided only by a deterministic rule engine; every profile value carries its provenance.",
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    register_error_handlers(app)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(request_id[:64])
        started = time.perf_counter()
        try:
            content_length = request.headers.get("content-length")
            if (
                request.method in _BODY_METHODS
                and content_length is not None
                and content_length.isdigit()
                and int(content_length) > settings.max_upload_bytes + MULTIPART_OVERHEAD_BYTES
            ):
                response: Response = error_response(
                    413,
                    "payload_too_large",
                    f"Request body exceeds the {settings.max_upload_mb} MB limit",
                    {"max_bytes": settings.max_upload_bytes},
                )
            else:
                response = await call_next(request)
            response.headers["X-Request-ID"] = request_id_var.get() or request_id
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
            return response
        finally:
            request_id_var.reset(token)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    app.include_router(api_router)
    return app


app = create_app()
