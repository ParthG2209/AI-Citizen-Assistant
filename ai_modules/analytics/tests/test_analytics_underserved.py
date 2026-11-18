"""Under-served criteria and top-missing-document ranking."""

from __future__ import annotations

import math

import pandas as pd

from ai_modules.analytics.coverage_job import prepare_inputs, run_coverage
from ai_modules.analytics.underserved_detection import (
    UnderservedCriteria,
    detect_underserved,
    flag_underserved,
    top_missing_documents,
)

from .conftest import SCHEMES


def test_flag_underserved_boundaries():
    table = pd.DataFrame(
        {
            "coverage_pct": [59.9, 60.0, 10.0, math.nan, 0.0, 59.9],
            "gap": [100, 500, 99, 1000, 100, 101],
        }
    )
    flags = flag_underserved(table, max_coverage_pct=60.0, min_gap=100).tolist()
    # below threshold & gap == min -> yes; coverage == threshold -> no; gap too small -> no;
    # unknown coverage -> never; 0 % with enough gap -> yes
    assert flags == [True, False, False, False, True, True]


def test_detect_underserved_on_fixture(clean_dir, districts):
    result = run_coverage(prepare_inputs(clean_dir, districts, SCHEMES), engine="pandas")
    criteria = UnderservedCriteria(
        max_coverage_pct=60.0, min_gap_district=3, min_gap_block=3, min_gap_village=2, top_documents=2
    )
    detection = detect_underserved(result, criteria)

    assert detection.underserved_ids("village") == ["v1"]  # v4 is 0 % but gap 1 < 2; v3 has no eligible
    assert detection.underserved_ids("block") == ["bhopal_b01"]
    assert detection.underserved_ids("district") == ["bhopal"]
    # Ties broken alphabetically, capped at top_documents.
    assert [d["document_type"] for d in detection.top_missing_documents["district"]["bhopal"]] == [
        "ration_card",
        "samagra_domicile",
    ]
    assert detection.top_missing_documents["district"]["bhopal"][0] == {
        "document_type": "ration_card",
        "label": "NFSA ration card / SECC listing proof",
        "citizens": 2,
    }
    assert "v3" not in detection.top_missing_documents["village"]


def test_criteria_from_env(monkeypatch):
    monkeypatch.setenv("UNDERSERVED_MAX_COVERAGE_PCT", "45.5")
    monkeypatch.setenv("UNDERSERVED_MIN_GAP_BLOCK", "7")
    monkeypatch.setenv("ANALYTICS_TOP_DOCUMENTS", "5")
    criteria = UnderservedCriteria.from_env()
    assert criteria.max_coverage_pct == 45.5
    assert criteria.min_gap("block") == 7
    assert criteria.min_gap("district") == UnderservedCriteria().min_gap_district
    assert criteria.top_documents == 5


def test_top_missing_documents_ranking():
    missing = pd.DataFrame(
        {
            "level": ["block"] * 4,
            "area_id": ["b1", "b1", "b1", "b2"],
            "document_type": ["ration_card", "income_certificate", "land_record", "land_record"],
            "citizens": [3, 5, 3, 1],
        }
    )
    top = top_missing_documents(missing, "block", limit=2)
    assert [d["document_type"] for d in top["b1"]] == ["income_certificate", "land_record"]
    assert top["b2"][0]["citizens"] == 1
