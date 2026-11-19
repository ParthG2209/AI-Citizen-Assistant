"""Spark engine: identical results to the pandas engine (needs a JDK; skipped without one)."""

from __future__ import annotations

import pandas as pd
import pytest

from ai_modules.analytics import synthetic
from ai_modules.analytics.coverage_job import OUTPUT_COLUMNS, prepare_inputs, run_coverage
from ai_modules.analytics.reference import SchemeInfo
from ai_modules.analytics.spark_session import JavaNotFoundError, SparkSettings, find_java

from .conftest import SCHEMES

pytestmark = pytest.mark.slow


def _assert_same(pandas_result, spark_result):
    assert spark_result.engine == "spark"
    assert pandas_result.input_rows == spark_result.input_rows
    for name in OUTPUT_COLUMNS:
        pd.testing.assert_frame_equal(pandas_result[name], spark_result[name], check_exact=True, obj=name)


@pytest.mark.parametrize("fixture_name", ["clean_dir", "invalid_dir"])
def test_spark_matches_pandas_on_fixtures(request, fixture_name, districts, spark):
    inputs = prepare_inputs(request.getfixturevalue(fixture_name), districts, SCHEMES)
    _assert_same(run_coverage(inputs, "pandas"), run_coverage(inputs, "spark", spark=spark))


def test_spark_matches_pandas_on_synthetic_dataset(tmp_path, districts, spark):
    schemes = {
        "pm_kisan": SCHEMES["pm_kisan"],
        "pm_jay": SCHEMES["pm_jay"],
        "mmvy": SchemeInfo("mmvy", "MMVY", ("class12_marksheet", "income_certificate")),
    }
    config = synthetic.SyntheticConfig(seed=11, district_ids=("indore", "rewa", "sagar"))
    directory = synthetic.write_dataset(synthetic.generate(districts, schemes, config), tmp_path / "ds")
    inputs = prepare_inputs(directory, districts, schemes)
    pandas_result = run_coverage(inputs, "pandas")
    assert pandas_result.input_rows["scheme_status"] > 1000
    _assert_same(pandas_result, run_coverage(inputs, "spark", spark=spark))


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("SPARK_MASTER", "local[1]")
    monkeypatch.setenv("SPARK_DRIVER_MEMORY", "2g")
    monkeypatch.setenv("SPARK_SHUFFLE_PARTITIONS", "2")
    assert SparkSettings.from_env() == SparkSettings("local[1]", "2g", 2)


def test_missing_java_gives_clear_error(monkeypatch, tmp_path):
    monkeypatch.setenv("JAVA_HOME", str(tmp_path / "no-jdk"))
    with pytest.raises(JavaNotFoundError, match="JAVA_HOME"):
        find_java()
    monkeypatch.delenv("JAVA_HOME")
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(JavaNotFoundError, match="--engine pandas"):
        find_java()
