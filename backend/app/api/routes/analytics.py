"""District analytics over the SYNTHETIC demo dataset (eligible vs enrolled, under-served areas)."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.schemas.analytics import (
    BlockListOut,
    DistrictDetailOut,
    DistrictListOut,
    MetadataOut,
    SchemeAdoptionListOut,
    SummaryOut,
    VillagePageOut,
)
from app.schemas.common import VALIDATION_ERROR, error_responses
from app.services import analytics_service

NOT_GENERATED = (503, "analytics_not_generated: run scripts/run_district_analytics.py")
router = APIRouter(prefix="/analytics", tags=["analytics"], responses=error_responses(NOT_GENERATED))


@router.get("/summary", response_model=SummaryOut, summary="State-level totals")
def get_summary() -> SummaryOut:
    return analytics_service.summary()


@router.get("/metadata", response_model=MetadataOut, summary="Dataset provenance, thresholds and definitions")
def get_metadata() -> MetadataOut:
    return analytics_service.metadata()


@router.get("/districts", response_model=DistrictListOut, summary="Coverage per district")
def list_districts() -> DistrictListOut:
    return analytics_service.districts()


@router.get(
    "/districts/{district_id}",
    response_model=DistrictDetailOut,
    summary="One district with its blocks and per-scheme coverage",
    responses=error_responses((404, "district_not_found")),
)
def get_district(district_id: str) -> DistrictDetailOut:
    return analytics_service.district_detail(district_id)


@router.get(
    "/blocks",
    response_model=BlockListOut,
    summary="Coverage per block",
    responses=error_responses((404, "district_not_found"), VALIDATION_ERROR),
)
def list_blocks(
    district_id: str | None = Query(default=None),
    underserved: bool | None = Query(default=None),
) -> BlockListOut:
    return analytics_service.blocks(district_id, underserved)


@router.get(
    "/villages",
    response_model=VillagePageOut,
    summary="Coverage per village (paginated)",
    responses=error_responses((404, "district_not_found"), (404, "block_not_found"), VALIDATION_ERROR),
)
def list_villages(
    district_id: str | None = Query(default=None),
    block_id: str | None = Query(default=None),
    underserved: bool | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> VillagePageOut:
    return analytics_service.villages(district_id, block_id, underserved, limit, offset)


@router.get("/schemes", response_model=SchemeAdoptionListOut, summary="Eligible vs enrolled per scheme")
def list_schemes() -> SchemeAdoptionListOut:
    return analytics_service.schemes()
