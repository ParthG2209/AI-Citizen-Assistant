"""District analytics responses (typed mirror of data/processed/analytics/*.json).

All analytics data is SYNTHETIC; every response carries ``is_synthetic`` and ``dataset_label``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class DatasetInfo(BaseModel):
    dataset_label: str
    is_synthetic: bool
    generated_at: str
    engine: str


class MissingDocumentOut(BaseModel):
    document_type: str
    label: str
    citizens: int = Field(description="Eligible or potentially eligible, not enrolled citizens lacking it.")


class AreaRefOut(BaseModel):
    id: str
    name: str
    coverage_pct: float | None
    gap: int


class CoverageMetrics(BaseModel):
    assessed: int
    eligible: int
    enrolled: int
    gap: int
    potentially_eligible: int
    coverage_pct: float | None = Field(description="enrolled / eligible * 100; null when eligible = 0.")
    coverage_flag: str | None
    is_synthetic: bool


class DistrictOut(CoverageMetrics):
    id: str
    name: str
    position: list[float] = Field(description="[latitude, longitude] of the district centroid.")
    underserved: bool
    top_missing_documents: list[MissingDocumentOut]
    underserved_blocks: list[AreaRefOut]
    blocks: int
    villages: int


class BlockOut(CoverageMetrics):
    id: str
    name: str
    district_id: str
    underserved: bool
    top_missing_documents: list[MissingDocumentOut]
    underserved_villages: list[AreaRefOut]
    villages: int


class VillageOut(CoverageMetrics):
    id: str
    name: str
    block_id: str
    district_id: str
    households: int
    population: int
    underserved: bool
    top_missing_documents: list[MissingDocumentOut]


class SchemeAdoptionOut(CoverageMetrics):
    scheme_id: str
    name: str
    top_missing_documents: list[MissingDocumentOut]


class DistrictSchemeOut(CoverageMetrics):
    district_id: str
    scheme_id: str
    scheme_name: str


class UnderservedCounts(BaseModel):
    districts: int
    blocks: int
    villages: int


class SummaryOut(DatasetInfo):
    districts: int
    blocks: int
    villages: int
    schemes: int
    assessed: int
    eligible: int
    enrolled: int
    gap: int
    potentially_eligible: int
    coverage_pct: float | None
    avg_district_coverage_pct: float | None
    underserved: UnderservedCounts
    rejected_rows: int


class DistrictListOut(DatasetInfo):
    count: int
    items: list[DistrictOut]


class BlockListOut(DatasetInfo):
    count: int
    items: list[BlockOut]


class VillagePageOut(DatasetInfo):
    total: int = Field(description="Villages matching the filters (before pagination).")
    limit: int
    offset: int
    items: list[VillageOut]


class SchemeAdoptionListOut(DatasetInfo):
    count: int
    items: list[SchemeAdoptionOut]


class DistrictDetailOut(DatasetInfo):
    district: DistrictOut
    blocks: list[BlockOut]
    schemes: list[DistrictSchemeOut]


class MetadataOut(DatasetInfo):
    disclaimer: str
    thresholds: dict[str, float]
    underserved_rule: str
    metric_definitions: dict[str, str]
    input_rows: dict[str, int]
    rejected_rows: int
    rejected_by_reason: dict[str, int]
    geo_attribution: str
    files: list[str]
