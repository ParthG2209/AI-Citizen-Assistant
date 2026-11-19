"""Export validation: files, JSON fields, is_synthetic flags everywhere, loader round-trip."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from ai_modules.analytics import OutputsNotFoundError, load_outputs
from ai_modules.analytics.outputs import JSON_FILES, TABLE_MIRRORS
from ai_modules.analytics.pipeline import run_pipeline
from ai_modules.analytics.underserved_detection import UnderservedCriteria

from .conftest import LABEL, SCHEMES

CRITERIA = UnderservedCriteria(max_coverage_pct=60.0, min_gap_district=3, min_gap_block=3, min_gap_village=2)
DISTRICT_FIELDS = {
    "id",
    "name",
    "position",
    "assessed",
    "eligible",
    "enrolled",
    "gap",
    "potentially_eligible",
    "coverage_pct",
    "coverage_flag",
    "underserved",
    "top_missing_documents",
    "underserved_blocks",
    "blocks",
    "villages",
    "is_synthetic",
}
SCHEME_FIELDS = {
    "scheme_id",
    "name",
    "assessed",
    "eligible",
    "enrolled",
    "gap",
    "potentially_eligible",
    "coverage_pct",
    "coverage_flag",
    "top_missing_documents",
    "is_synthetic",
}
SUMMARY_FIELDS = {
    "dataset_label",
    "is_synthetic",
    "generated_at",
    "engine",
    "districts",
    "blocks",
    "villages",
    "schemes",
    "assessed",
    "eligible",
    "enrolled",
    "gap",
    "potentially_eligible",
    "coverage_pct",
    "avg_district_coverage_pct",
    "underserved",
    "rejected_rows",
}


@pytest.fixture()
def run(invalid_dir, tmp_path):
    return run_pipeline(invalid_dir, SCHEMES, engine="pandas", output_dir=tmp_path / "out", criteria=CRITERIA)


def test_all_files_written(run):
    out = run.output_dir
    for name in JSON_FILES:
        assert (out / f"{name}.json").is_file(), name
    for stem in TABLE_MIRRORS:
        assert (out / "parquet" / f"{stem}.parquet").is_file(), stem
        assert (out / "csv" / f"{stem}.csv").is_file(), stem
    assert (out / "rejected_rows.csv").is_file()
    html = (out / "report.html").read_text("utf-8")
    assert "SYNTHETIC DEMONSTRATION DATA" in html and "plotly" in html.lower()


def test_json_shapes_and_values(run):
    out = run.output_dir
    summary = json.loads((out / "summary.json").read_text("utf-8"))
    assert set(summary) == SUMMARY_FIELDS
    assert summary["is_synthetic"] is True and summary["dataset_label"] == LABEL
    assert (summary["eligible"], summary["enrolled"], summary["gap"], summary["coverage_pct"]) == (
        7,
        3,
        4,
        42.9,
    )
    assert summary["avg_district_coverage_pct"] == 25.0  # mean of bhopal 50.0 and sehore 0.0
    assert summary["underserved"] == {"districts": 1, "blocks": 1, "villages": 1}
    assert summary["rejected_rows"] == len(run.result["rejected"]) > 0

    districts = json.loads((out / "districts.json").read_text("utf-8"))
    assert districts["count"] == 2 and districts["is_synthetic"] is True
    bhopal = next(d for d in districts["items"] if d["id"] == "bhopal")
    assert set(bhopal) == DISTRICT_FIELDS
    assert bhopal["position"] == [pytest.approx(23.478), pytest.approx(77.3961)]
    assert bhopal["coverage_pct"] == 50.0 and bhopal["underserved"] is True
    assert bhopal["underserved_blocks"] == [
        {"id": "bhopal_b01", "name": "Block 01", "coverage_pct": 50.0, "gap": 3}
    ]
    assert bhopal["top_missing_documents"][0]["document_type"] == "ration_card"

    blocks = json.loads((out / "blocks.json").read_text("utf-8"))["items"]
    empty_block = next(b for b in blocks if b["id"] == "bhopal_b02")
    assert empty_block["coverage_pct"] is None and empty_block["coverage_flag"] == "no_eligible_population"
    villages = json.loads((out / "villages.json").read_text("utf-8"))["items"]
    assert {v["id"] for v in villages} == {"v1", "v2", "v3", "v4"}

    schemes = json.loads((out / "scheme_adoption.json").read_text("utf-8"))["items"]
    assert all(set(s) == SCHEME_FIELDS for s in schemes)
    assert {s["scheme_id"]: s["coverage_pct"] for s in schemes} == {"pm_kisan": 50.0, "pm_jay": 33.3}

    metadata = json.loads((out / "metadata.json").read_text("utf-8"))
    assert metadata["is_synthetic"] is True and metadata["engine"] == "pandas"
    assert metadata["thresholds"]["min_gap_village"] == 2
    assert metadata["rejected_by_reason"]["blocks:duplicate_id"] == 2


def test_is_synthetic_on_every_record(run):
    out = run.output_dir
    for name in ("districts", "blocks", "villages", "scheme_adoption", "district_schemes"):
        doc = json.loads((out / f"{name}.json").read_text("utf-8"))
        assert doc["is_synthetic"] is True and doc["dataset_label"] == LABEL
        assert doc["items"] and all(item["is_synthetic"] is True for item in doc["items"]), name
    for stem in TABLE_MIRRORS:
        for frame in (
            pd.read_parquet(out / "parquet" / f"{stem}.parquet"),
            pd.read_csv(out / "csv" / f"{stem}.csv"),
        ):
            assert frame["is_synthetic"].astype(bool).all(), stem
            assert set(frame["dataset_label"]) == {LABEL}


def test_rejected_rows_report(run):
    rejected = pd.read_csv(run.output_dir / "rejected_rows.csv")
    assert list(rejected.columns) == ["table", "record_key", "reason", "dataset_label"]
    assert ("villages", "vneg", "negative_households") in set(
        rejected[["table", "record_key", "reason"]].itertuples(index=False, name=None)
    )


def test_load_outputs_round_trip(run, monkeypatch):
    loaded = load_outputs(run.output_dir)
    assert loaded.summary == run.documents["summary"]
    assert loaded.districts["items"][0]["is_synthetic"] is True
    monkeypatch.setenv("ANALYTICS_OUTPUT_DIR", str(run.output_dir))
    assert load_outputs().metadata["dataset_label"] == LABEL


def test_load_outputs_missing(tmp_path):
    with pytest.raises(OutputsNotFoundError, match="run_district_analytics"):
        load_outputs(tmp_path)
