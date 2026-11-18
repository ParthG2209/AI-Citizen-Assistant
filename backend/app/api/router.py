"""Aggregate ``/api/v1`` router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import eligibility, health, profiles, schemes

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(profiles.router)
api_router.include_router(schemes.router)
api_router.include_router(eligibility.router)
