"""Read-only access to the district analytics outputs (synthetic dataset).

Outputs are produced offline by ``scripts/run_district_analytics.py``; this service only reads the
JSON files. Loaded outputs are cached per directory and reloaded when any file's mtime changes
(i.e. after the pipeline is re-run).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.config import get_settings
from app.errors import ApiError, not_found
from app.schemas.analytics import (
    BlockListOut,
    DistrictDetailOut,
    DistrictListOut,
    MetadataOut,
    SchemeAdoptionListOut,
    SummaryOut,
    VillagePageOut,
)

if TYPE_CHECKING:
    from ai_modules.analytics import AnalyticsOutputs

JSON_FILES = (
    "summary",
    "districts",
    "blocks",
    "villages",
    "scheme_adoption",
    "district_schemes",
    "metadata",
)
_cache: dict[Path, tuple[tuple[int, ...], AnalyticsOutputs]] = {}
_lock = threading.Lock()


def _not_generated(directory: Path) -> ApiError:
    return ApiError(
        503,
        "analytics_not_generated",
        "District analytics outputs have not been generated yet",
        {
            "hint": "Run: python scripts/run_district_analytics.py --engine pandas",
            "output_dir": str(directory),
        },
    )


def load(directory: Path | None = None) -> AnalyticsOutputs:
    from ai_modules.analytics import OutputsNotFoundError, load_outputs

    directory = Path(directory or get_settings().analytics_output_dir)
    try:
        key = tuple((directory / f"{name}.json").stat().st_mtime_ns for name in JSON_FILES)
    except OSError as exc:
        raise _not_generated(directory) from exc
    with _lock:
        cached = _cache.get(directory)
        if cached is not None and cached[0] == key:
            return cached[1]
        try:
            outputs = load_outputs(directory)
        except OutputsNotFoundError as exc:
            raise _not_generated(directory) from exc
        _cache[directory] = (key, outputs)
        return outputs


def _dataset(document: dict[str, Any]) -> dict[str, Any]:
    return {k: document[k] for k in ("dataset_label", "is_synthetic", "generated_at", "engine")}


def summary() -> SummaryOut:
    return SummaryOut.model_validate(load().summary)


def metadata() -> MetadataOut:
    return MetadataOut.model_validate(load().metadata)


def districts() -> DistrictListOut:
    return DistrictListOut.model_validate(load().districts)


def schemes() -> SchemeAdoptionListOut:
    return SchemeAdoptionListOut.model_validate(load().scheme_adoption)


def _district_ids(outputs: AnalyticsOutputs) -> set[str]:
    return {d["id"] for d in outputs.districts["items"]}


def district_detail(district_id: str) -> DistrictDetailOut:
    outputs = load()
    district = next((d for d in outputs.districts["items"] if d["id"] == district_id), None)
    if district is None:
        raise not_found("district", district_id)
    return DistrictDetailOut(
        **_dataset(outputs.districts),
        district=district,
        blocks=[b for b in outputs.blocks["items"] if b["district_id"] == district_id],
        schemes=[s for s in outputs.district_schemes["items"] if s["district_id"] == district_id],
    )


def blocks(district_id: str | None, underserved: bool | None) -> BlockListOut:
    outputs = load()
    if district_id is not None and district_id not in _district_ids(outputs):
        raise not_found("district", district_id)
    items = [
        b
        for b in outputs.blocks["items"]
        if (district_id is None or b["district_id"] == district_id)
        and (underserved is None or b["underserved"] is underserved)
    ]
    return BlockListOut(**_dataset(outputs.blocks), count=len(items), items=items)


def villages(
    district_id: str | None, block_id: str | None, underserved: bool | None, limit: int, offset: int
) -> VillagePageOut:
    outputs = load()
    if district_id is not None and district_id not in _district_ids(outputs):
        raise not_found("district", district_id)
    if block_id is not None and block_id not in {b["id"] for b in outputs.blocks["items"]}:
        raise not_found("block", block_id)
    items = [
        v
        for v in outputs.villages["items"]
        if (district_id is None or v["district_id"] == district_id)
        and (block_id is None or v["block_id"] == block_id)
        and (underserved is None or v["underserved"] is underserved)
    ]
    return VillagePageOut(
        **_dataset(outputs.villages),
        total=len(items),
        limit=limit,
        offset=offset,
        items=items[offset : offset + limit],
    )
