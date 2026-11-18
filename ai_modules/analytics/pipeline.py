"""End-to-end district analytics: inputs -> coverage job -> under-served detection -> outputs + report."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ai_modules.analytics import synthetic
from ai_modules.analytics.coverage_job import CoverageResult, prepare_inputs, run_coverage
from ai_modules.analytics.outputs import default_output_dir, write_outputs
from ai_modules.analytics.reference import DEFAULT_GEOJSON, District, SchemeInfo, load_districts
from ai_modules.analytics.underserved_detection import Detection, UnderservedCriteria, detect_underserved

if TYPE_CHECKING:
    from pyspark.sql import SparkSession


@dataclass(slots=True)
class PipelineRun:
    result: CoverageResult
    detection: Detection
    documents: dict[str, Any]
    output_dir: Path
    report_path: Path | None
    timings: dict[str, float] = field(default_factory=dict)


def ensure_synthetic_dataset(
    input_dir: Path,
    districts: Mapping[str, District],
    schemes: Mapping[str, SchemeInfo],
    config: synthetic.SyntheticConfig,
    regenerate: bool = False,
) -> str | None:
    """(Re)generate the synthetic inputs when needed; returns the reason, or ``None`` if reused as-is."""
    reason = "--regenerate requested" if regenerate else None
    if reason is None:
        try:
            manifest = synthetic.read_manifest(input_dir)
        except FileNotFoundError:
            reason = "no dataset found"
        else:
            if manifest.get("scheme_ids") != sorted(schemes):
                reason = "rule catalog scheme ids changed"
            elif manifest.get("config", {}).get("seed") != config.seed:
                reason = f"seed changed to {config.seed}"
    if reason:
        synthetic.write_dataset(synthetic.generate(districts, schemes, config), input_dir)
    return reason


def run_pipeline(
    input_dir: Path,
    schemes: Mapping[str, SchemeInfo],
    *,
    engine: str = "pandas",
    output_dir: Path | None = None,
    criteria: UnderservedCriteria | None = None,
    geojson_path: Path = DEFAULT_GEOJSON,
    spark: SparkSession | None = None,
    report: bool = True,
) -> PipelineRun:
    timings: dict[str, float] = {}
    output_dir = Path(output_dir) if output_dir is not None else default_output_dir()

    start = time.perf_counter()
    inputs = prepare_inputs(Path(input_dir), load_districts(geojson_path), schemes)
    result = run_coverage(inputs, engine=engine, spark=spark)
    timings["coverage_job_s"] = time.perf_counter() - start

    start = time.perf_counter()
    detection = detect_underserved(result, criteria)
    timings["underserved_detection_s"] = time.perf_counter() - start

    start = time.perf_counter()
    documents = write_outputs(result, detection, output_dir)
    timings["write_outputs_s"] = time.perf_counter() - start

    report_path = None
    if report:
        from ai_modules.analytics.plotly_reports import write_report

        start = time.perf_counter()
        report_path = write_report(documents, output_dir / "report.html", geojson_path)
        timings["report_s"] = time.perf_counter() - start
    return PipelineRun(result, detection, documents, output_dir, report_path, timings)
