"""Coverage job (pandas engine) against the hand-computed fixture in conftest.py."""

from __future__ import annotations

import math

import pytest

from ai_modules.analytics.coverage_job import (
    InputSchemaError,
    coverage_pct,
    prepare_inputs,
    resolve_engine,
    run_coverage,
)

from .conftest import SCHEMES, expected_rejections


def _rows(df, key: str) -> dict[str, dict]:
    return {row[key]: row for row in df.to_dict("records")}


def _metrics(row: dict) -> tuple:
    cov = None if row["coverage_pct"] is None or math.isnan(row["coverage_pct"]) else row["coverage_pct"]
    return (
        row["assessed"],
        row["eligible"],
        row["enrolled"],
        row["gap"],
        row["potentially_eligible"],
        cov,
        row["coverage_flag"],
    )


@pytest.mark.parametrize(
    ("enrolled", "eligible", "expected"),
    [
        (1, 3, 33.3),
        (2, 3, 66.7),
        (1, 8, 12.5),
        (1, 16, 6.3),
        (0, 5, 0.0),
        (5, 5, 100.0),
        (0, 0, None),
        (3, 0, None),
    ],
)
def test_coverage_pct_half_up_and_zero_denominator(enrolled, eligible, expected):
    assert coverage_pct(enrolled, eligible) == expected


def test_aggregation_matches_hand_computed_values(clean_dir, districts):
    result = run_coverage(prepare_inputs(clean_dir, districts, SCHEMES), engine="pandas")

    villages = _rows(result["villages"], "village_id")
    assert _metrics(villages["v1"]) == (5, 3, 1, 2, 1, 33.3, None)
    assert _metrics(villages["v2"]) == (3, 3, 2, 1, 0, 66.7, None)
    assert _metrics(villages["v3"]) == (0, 0, 0, 0, 0, None, "no_eligible_population")
    # Zero enrolled out of one eligible is a real 0.0 %, not "no data".
    assert _metrics(villages["v4"]) == (2, 1, 0, 1, 0, 0.0, None)
    assert villages["v1"]["households"] == 3 and villages["v1"]["population"] == 10

    blocks = _rows(result["blocks"], "block_id")
    assert _metrics(blocks["bhopal_b01"]) == (8, 6, 3, 3, 1, 50.0, None)
    assert _metrics(blocks["bhopal_b02"]) == (0, 0, 0, 0, 0, None, "no_eligible_population")
    assert _metrics(blocks["sehore_b01"]) == (2, 1, 0, 1, 0, 0.0, None)
    assert [blocks[b]["villages"] for b in ("bhopal_b01", "bhopal_b02", "sehore_b01")] == [2, 1, 1]

    district_rows = _rows(result["districts"], "district_id")
    assert set(district_rows) == {"bhopal", "sehore"}
    assert _metrics(district_rows["bhopal"]) == (8, 6, 3, 3, 1, 50.0, None)
    assert _metrics(district_rows["sehore"]) == (2, 1, 0, 1, 0, 0.0, None)
    assert (district_rows["bhopal"]["blocks"], district_rows["bhopal"]["villages"]) == (2, 3)
    assert district_rows["bhopal"]["district_name"] == "Bhopal"
    assert district_rows["bhopal"]["lat"] == districts["bhopal"].lat

    schemes = _rows(result["schemes"], "scheme_id")
    assert _metrics(schemes["pm_kisan"]) == (5, 4, 2, 2, 0, 50.0, None)
    assert _metrics(schemes["pm_jay"]) == (5, 3, 1, 2, 1, 33.3, None)
    assert schemes["pm_kisan"]["scheme_name"] == "PM-KISAN"

    district_schemes = {
        (r["district_id"], r["scheme_id"]): r for r in result["district_schemes"].to_dict("records")
    }
    assert len(district_schemes) == 4
    assert _metrics(district_schemes[("bhopal", "pm_jay")]) == (4, 3, 1, 2, 1, 33.3, None)
    assert _metrics(district_schemes[("sehore", "pm_jay")]) == (1, 0, 0, 0, 0, None, "no_eligible_population")

    missing = {
        (r["level"], r["area_id"], r["document_type"]): r["citizens"]
        for r in result["missing_documents"].to_dict("records")
    }
    assert {k[2]: v for k, v in missing.items() if k[:2] == ("district", "bhopal")} == {
        "ration_card": 2,  # c1 (r2) and c2 (r4, potentially eligible)
        "samagra_domicile": 2,  # c2 (r3) and c5 (r8, value padded with spaces)
        "land_record": 1,  # c2 (r3)
    }
    assert {k[2]: v for k, v in missing.items() if k[:2] == ("district", "sehore")} == {"land_record": 1}
    assert {k[2]: v for k, v in missing.items() if k[:2] == ("scheme", "pm_jay")} == {
        "ration_card": 2,
        "samagra_domicile": 1,
    }
    assert result["rejected"].empty
    assert result.input_rows == {"blocks": 3, "villages": 4, "scheme_status": 10}
    assert result.is_synthetic is True and result.dataset_label == "TEST-FIXTURE"


def test_invalid_rows_are_rejected_with_reasons_and_do_not_leak(clean_dir, invalid_dir, districts):
    clean = run_coverage(prepare_inputs(clean_dir, districts, SCHEMES), engine="pandas")
    dirty = run_coverage(prepare_inputs(invalid_dir, districts, SCHEMES), engine="pandas")

    rejected = [tuple(r) for r in dirty["rejected"].itertuples(index=False)]
    assert rejected == expected_rejections()
    for table in ("villages", "blocks", "districts", "schemes", "district_schemes", "missing_documents"):
        assert dirty[table].equals(clean[table]), table


def test_missing_column_or_manifest_aborts(clean_dir, districts):
    status = clean_dir / "scheme_status.csv"
    status.write_text(status.read_text("utf-8").replace("eligibility_status", "status", 1), "utf-8")
    with pytest.raises(InputSchemaError, match="eligibility_status"):
        prepare_inputs(clean_dir, districts, SCHEMES)
    (clean_dir / "manifest.json").unlink()
    with pytest.raises(InputSchemaError, match="manifest"):
        prepare_inputs(clean_dir, districts, SCHEMES)


def test_manifest_must_declare_synthetic_flag(clean_dir, districts):
    (clean_dir / "manifest.json").write_text('{"dataset_label": "X"}', "utf-8")
    with pytest.raises(InputSchemaError, match="is_synthetic"):
        prepare_inputs(clean_dir, districts, SCHEMES)


def test_resolve_engine(monkeypatch):
    monkeypatch.delenv("ANALYTICS_ENGINE", raising=False)
    assert resolve_engine(None) == "pandas"
    monkeypatch.setenv("ANALYTICS_ENGINE", "spark")
    assert resolve_engine(None) == "spark"
    assert resolve_engine("PANDAS") == "pandas"
    with pytest.raises(ValueError, match="Unknown analytics engine"):
        resolve_engine("dask")
