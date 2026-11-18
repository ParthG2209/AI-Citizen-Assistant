"""Aggregate ``/api/v1`` router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.routes import analytics, eligibility, health, profiles, recommendations, schemes

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(profiles.router)
api_router.include_router(schemes.router)
api_router.include_router(eligibility.router)
api_router.include_router(recommendations.router)
api_router.include_router(analytics.router)
