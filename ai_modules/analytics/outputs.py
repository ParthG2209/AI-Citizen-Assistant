"""Write and load the analytics outputs in ``data/processed/analytics/``.

JSON files share one envelope::

    {"dataset_label": str, "is_synthetic": bool, "generated_at": ISO-8601 UTC, "engine": "pandas"|"spark",
     "count": int, "items": [ {..., "is_synthetic": bool}, ... ]}

(``summary.json`` and ``metadata.json`` are flat objects instead.) Parquet and CSV mirrors of the tables are
written to ``parquet/`` and ``csv/`` with ``is_synthetic`` and ``dataset_label`` columns on every row.

``load_outputs`` only uses the standard library so the backend can call it without pandas.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai_modules.analytics.coverage_job import coverage_pct
from ai_modules.analytics.reference import REPO_ROOT

if TYPE_CHECKING:
    import pandas as pd

    from ai_modules.analytics.coverage_job import CoverageResult
    from ai_modules.analytics.underserved_detection import Detection

DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "processed" / "analytics"
OUTPUT_DIR_ENV = "ANALYTICS_OUTPUT_DIR"
JSON_FILES = ("summary", "districts", "blocks", "villages", "scheme_adoption", "district_schemes", "metadata")
TABLE_MIRRORS = {  # mirror file stem -> coverage/detection table
    "districts": "districts",
    "blocks": "blocks",
    "villages": "villages",
    "scheme_adoption": "schemes",
    "district_schemes": "district_schemes",
    "missing_documents": "missing_documents",
}
METRIC_FIELDS = (
    "assessed",
    "eligible",
    "enrolled",
    "gap",
    "potentially_eligible",
    "coverage_pct",
    "coverage_flag",
)
METRIC_DEFINITIONS = {
    "assessed": "citizen x scheme assessments (rows) in the area",
    "eligible": "assessments with eligibility_status = eligible",
    "enrolled": "eligible assessments with enrolled = true",
    "gap": "eligible - enrolled",
    "potentially_eligible": "assessments pending documents (not part of coverage)",
    "coverage_pct": "enrolled / eligible * 100, half-up to 1 decimal; null when eligible = 0",
    "coverage_flag": "'no_eligible_population' when eligible = 0, else null",
    "top_missing_documents.citizens": (
        "distinct eligible or potentially eligible, not enrolled citizens lacking the document"
    ),
}
GEO_ATTRIBUTION = (
    "District boundaries/centroids derived from DataMeet India Census-2011 districts (CC BY 2.5 IN); "
    "see data/geo/README.md"
)


class OutputsNotFoundError(FileNotFoundError):
    """The analytics outputs have not been generated yet."""


@dataclass(frozen=True, slots=True)
class AnalyticsOutputs:
    summary: dict[str, Any]
    districts: dict[str, Any]
    blocks: dict[str, Any]
    villages: dict[str, Any]
    scheme_adoption: dict[str, Any]
    district_schemes: dict[str, Any]
    metadata: dict[str, Any]


def default_output_dir() -> Path:
    return Path(os.environ.get(OUTPUT_DIR_ENV) or DEFAULT_OUTPUT_DIR)


def load_outputs(output_dir: Path | str | None = None) -> AnalyticsOutputs:
    """Read the JSON outputs. Raises ``OutputsNotFoundError`` if any file is missing."""
    directory = Path(output_dir) if output_dir is not None else default_output_dir()
    missing = [name for name in JSON_FILES if not (directory / f"{name}.json").is_file()]
    if missing:
        raise OutputsNotFoundError(
            f"Analytics outputs missing in {directory}: {missing}. "
            "Run: python scripts/run_district_analytics.py"
        )
    loaded = {}
    for name in JSON_FILES:
        with (directory / f"{name}.json").open(encoding="utf-8") as fh:
            loaded[name] = json.load(fh)
    return AnalyticsOutputs(**loaded)


def _value(value: Any) -> Any:
    """numpy/pandas scalars -> plain JSON values; NaN -> None."""
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _metrics(row: pd.Series) -> dict[str, Any]:
    return {name: _value(row[name]) for name in METRIC_FIELDS}


def _brief(rows: pd.DataFrame, id_column: str, name_column: str) -> list[dict[str, Any]]:
    """Under-served child areas, largest gap first (ties: lowest coverage, then id)."""
    rows = rows.sort_values(
        ["gap", "coverage_pct", id_column], ascending=[False, True, True], kind="mergesort"
    )
    return [
        {
            "id": r[id_column],
            "name": r[name_column],
            "coverage_pct": _value(r["coverage_pct"]),
            "gap": _value(r["gap"]),
        }
        for _, r in rows.iterrows()
    ]


def build_documents(result: CoverageResult, detection: Detection, generated_at: str) -> dict[str, Any]:
    """Build the JSON documents (without writing them)."""
    synthetic = result.is_synthetic
    districts, blocks, villages = (detection.tables[n] for n in ("districts", "blocks", "villages"))
    top = detection.top_missing_documents

    def envelope(items: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "dataset_label": result.dataset_label,
            "is_synthetic": synthetic,
            "generated_at": generated_at,
            "engine": result.engine,
            "count": len(items),
            "items": items,
        }

    underserved_blocks = blocks[blocks["underserved"]]
    underserved_villages = villages[villages["underserved"]]
    district_items = [
        {
            "id": r["district_id"],
            "name": r["district_name"],
            "position": [_value(r["lat"]), _value(r["lng"])],
            **_metrics(r),
            "underserved": bool(r["underserved"]),
            "top_missing_documents": top["district"].get(r["district_id"], []),
            "underserved_blocks": _brief(
                underserved_blocks[underserved_blocks["district_id"] == r["district_id"]],
                "block_id",
                "block_name",
            ),
            "blocks": _value(r["blocks"]),
            "villages": _value(r["villages"]),
            "is_synthetic": synthetic,
        }
        for _, r in districts.iterrows()
    ]
    block_items = [
        {
            "id": r["block_id"],
            "name": r["block_name"],
            "district_id": r["district_id"],
            **_metrics(r),
            "underserved": bool(r["underserved"]),
            "top_missing_documents": top["block"].get(r["block_id"], []),
            "underserved_villages": _brief(
                underserved_villages[underserved_villages["block_id"] == r["block_id"]],
                "village_id",
                "village_name",
            ),
            "villages": _value(r["villages"]),
            "is_synthetic": synthetic,
        }
        for _, r in blocks.iterrows()
    ]
    village_items = [
        {
            "id": r["village_id"],
            "name": r["village_name"],
            "block_id": r["block_id"],
            "district_id": r["district_id"],
            "households": _value(r["households"]),
            "population": _value(r["population"]),
            **_metrics(r),
            "underserved": bool(r["underserved"]),
            "top_missing_documents": top["village"].get(r["village_id"], []),
            "is_synthetic": synthetic,
        }
        for _, r in villages.iterrows()
    ]
    scheme_items = [
        {
            "scheme_id": r["scheme_id"],
            "name": r["scheme_name"],
            **_metrics(r),
            "top_missing_documents": top["scheme"].get(r["scheme_id"], []),
            "is_synthetic": synthetic,
        }
        for _, r in result["schemes"].iterrows()
    ]
    scheme_names = dict(zip(result["schemes"]["scheme_id"], result["schemes"]["scheme_name"], strict=True))
    district_scheme_items = [
        {
            "district_id": r["district_id"],
            "scheme_id": r["scheme_id"],
            "scheme_name": scheme_names[r["scheme_id"]],
            **_metrics(r),
            "is_synthetic": synthetic,
        }
        for _, r in result["district_schemes"].iterrows()
    ]

    eligible = int(districts["eligible"].sum())
    enrolled = int(districts["enrolled"].sum())
    district_coverage = [c for c in districts["coverage_pct"].tolist() if not math.isnan(c)]
    rejected = result["rejected"]
    summary = {
        "dataset_label": result.dataset_label,
        "is_synthetic": synthetic,
        "generated_at": generated_at,
        "engine": result.engine,
        "districts": len(districts),
        "blocks": len(blocks),
        "villages": len(villages),
        "schemes": len(result["schemes"]),
        "assessed": int(districts["assessed"].sum()),
        "eligible": eligible,
        "enrolled": enrolled,
        "gap": eligible - enrolled,
        "potentially_eligible": int(districts["potentially_eligible"].sum()),
        "coverage_pct": coverage_pct(enrolled, eligible),
        "avg_district_coverage_pct": _half_up(sum(district_coverage) / len(district_coverage))
        if district_coverage
        else None,
        "underserved": {
            "districts": int(districts["underserved"].sum()),
            "blocks": int(blocks["underserved"].sum()),
            "villages": int(villages["underserved"].sum()),
        },
        "rejected_rows": len(rejected),
    }
    metadata = {
        "generated_at": generated_at,
        "engine": result.engine,
        "dataset_label": result.dataset_label,
        "is_synthetic": synthetic,
        "disclaimer": (
            "SYNTHETIC demonstration data - not government statistics. "
            "Only district names and centroids are real."
            if synthetic
            else "Computed from the input dataset named in dataset_label."
        ),
        "thresholds": detection.criteria.as_dict(),
        "underserved_rule": (
            "coverage_pct is not null AND coverage_pct < max_coverage_pct AND gap >= min_gap_<level>"
        ),
        "metric_definitions": METRIC_DEFINITIONS,
        "input_rows": result.input_rows,
        "rejected_rows": len(rejected),
        "rejected_by_reason": {
            f"{table}:{reason}": int(count)
            for (table, reason), count in rejected.groupby(["table", "reason"]).size().items()
        },
        "geo_attribution": GEO_ATTRIBUTION,
        "files": [f"{name}.json" for name in JSON_FILES]
        + ["rejected_rows.csv", "report.html"]
        + [f"parquet/{stem}.parquet" for stem in TABLE_MIRRORS]
        + [f"csv/{stem}.csv" for stem in TABLE_MIRRORS],
    }
    return {
        "summary": summary,
        "districts": envelope(district_items),
        "blocks": envelope(block_items),
        "villages": envelope(village_items),
        "scheme_adoption": envelope(scheme_items),
        "district_schemes": envelope(district_scheme_items),
        "metadata": metadata,
    }


def _half_up(value: float) -> float:
    return math.floor(value * 10 + 0.5) / 10


def write_outputs(
    result: CoverageResult,
    detection: Detection,
    output_dir: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Write JSON, Parquet and CSV outputs; returns the JSON documents. ``metadata.json`` is written last."""
    directory = Path(output_dir) if output_dir is not None else default_output_dir()
    generated_at = generated_at or datetime.now(UTC).replace(microsecond=0).isoformat()
    documents = build_documents(result, detection, generated_at)
    (directory / "parquet").mkdir(parents=True, exist_ok=True)
    (directory / "csv").mkdir(parents=True, exist_ok=True)

    for stem, table_name in TABLE_MIRRORS.items():
        table = detection.tables.get(table_name, result.tables.get(table_name))
        table = table.assign(is_synthetic=result.is_synthetic, dataset_label=result.dataset_label)
        table.to_parquet(directory / "parquet" / f"{stem}.parquet", index=False)
        table.to_csv(directory / "csv" / f"{stem}.csv", index=False, lineterminator="\n")
    result["rejected"].assign(dataset_label=result.dataset_label).to_csv(
        directory / "rejected_rows.csv", index=False, lineterminator="\n"
    )
    for name in JSON_FILES:
        with (directory / f"{name}.json").open("w", encoding="utf-8", newline="\n") as fh:
            json.dump(documents[name], fh, ensure_ascii=False, indent=1)
            fh.write("\n")
    return documents
