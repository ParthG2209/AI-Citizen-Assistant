"""Scheme coverage job: validate the census-style inputs and aggregate eligible vs enrolled.

Two interchangeable engines produce identical results (asserted by the tests):

* ``pandas`` - in-process "lite" engine for low-RAM laptops (default);
* ``spark``  - PySpark local mode (needs a JDK, see ``spark_session.py``).

Inputs (``input_dir``, schema in data/raw/census/README.md): ``manifest.json``, ``blocks.csv``,
``villages.csv``, ``scheme_status.csv`` (one row per citizen x scheme assessment).

Metric definitions (per area and per scheme), counted over accepted ``scheme_status`` rows:

* ``assessed``  - rows (citizen x scheme assessments);
* ``eligible``  - rows with ``eligibility_status == "eligible"``;
* ``enrolled``  - eligible rows with ``enrolled == true``;
* ``gap``       - ``eligible - enrolled``;
* ``potentially_eligible`` - rows with status ``potentially_eligible`` (pending documents; not in coverage);
* ``coverage_pct`` - ``enrolled / eligible * 100`` rounded half-up to 1 decimal; ``null`` with
  ``coverage_flag = "no_eligible_population"`` when ``eligible == 0`` (never a division error, never 0%).

Missing documents are counted as distinct citizens per (area, document type) over rows that are eligible or
potentially eligible but not enrolled.

Invalid rows are not silently dropped: every rejected row is reported in ``CoverageResult.rejected`` with
``(table, record_key, reason)``; the first failing check (in the order listed in ``*_CHECKS`` docs) wins.
"""

from __future__ import annotations

import csv
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from ai_modules.analytics.reference import KNOWN_DOCUMENT_TYPES, District, SchemeInfo
from ai_modules.analytics.synthetic import (
    BLOCK_COLUMNS,
    DOCUMENT_SEPARATOR,
    STATUS_COLUMNS,
    VILLAGE_COLUMNS,
    read_manifest,
)

if TYPE_CHECKING:
    import pandas as pd
    from pyspark.sql import DataFrame as SparkDataFrame
    from pyspark.sql import SparkSession

ENGINES = ("pandas", "spark")
DEFAULT_ENGINE_ENV = "ANALYTICS_ENGINE"
STATUSES = ("eligible", "potentially_eligible", "not_eligible")
OPEN_STATUSES = ("eligible", "potentially_eligible")  # statuses whose missing documents are counted
INT_PATTERN = r"^-?[0-9]{1,15}$"
NO_ELIGIBLE_FLAG = "no_eligible_population"

METRIC_COLUMNS = (
    "assessed",
    "eligible",
    "enrolled",
    "gap",
    "potentially_eligible",
    "coverage_pct",
    "coverage_flag",
)
COUNT_COLUMNS = ("assessed", "eligible", "enrolled", "gap", "potentially_eligible")
OUTPUT_COLUMNS: dict[str, tuple[str, ...]] = {
    "villages": (
        "village_id",
        "village_name",
        "block_id",
        "district_id",
        "households",
        "population",
        *METRIC_COLUMNS,
    ),
    "blocks": ("block_id", "block_name", "district_id", "villages", *METRIC_COLUMNS),
    "districts": ("district_id", "district_name", "lat", "lng", "blocks", "villages", *METRIC_COLUMNS),
    "schemes": ("scheme_id", "scheme_name", *METRIC_COLUMNS),
    "district_schemes": ("district_id", "scheme_id", *METRIC_COLUMNS),
    "missing_documents": ("level", "area_id", "document_type", "citizens"),
    "rejected": ("table", "record_key", "reason"),
}
SORT_KEYS: dict[str, tuple[str, ...]] = {
    "villages": ("village_id",),
    "blocks": ("block_id",),
    "districts": ("district_id",),
    "schemes": ("scheme_id",),
    "district_schemes": ("district_id", "scheme_id"),
    "missing_documents": ("level", "area_id", "document_type"),
    "rejected": ("table", "record_key", "reason"),
}
INT_COLUMNS = frozenset({*COUNT_COLUMNS, "households", "population", "villages", "blocks", "citizens"})
FLOAT_COLUMNS = frozenset({"coverage_pct", "lat", "lng"})
# Area level -> id column in the joined status rows (used for missing-document counts).
LEVEL_KEYS = {"village": "village_id", "block": "block_id", "district": "district_id", "scheme": "scheme_id"}


class InputSchemaError(ValueError):
    """An input file or the manifest is missing or lacks required columns (the whole run is rejected)."""


@dataclass(frozen=True, slots=True)
class CoverageInputs:
    input_dir: Path
    dataset_label: str
    is_synthetic: bool
    districts: Mapping[str, District]
    schemes: Mapping[str, SchemeInfo]

    @property
    def blocks_path(self) -> Path:
        return self.input_dir / "blocks.csv"

    @property
    def villages_path(self) -> Path:
        return self.input_dir / "villages.csv"

    @property
    def status_path(self) -> Path:
        return self.input_dir / "scheme_status.csv"


@dataclass(slots=True)
class CoverageResult:
    engine: str
    dataset_label: str
    is_synthetic: bool
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    input_rows: dict[str, int] = field(default_factory=dict)

    def __getitem__(self, name: str) -> pd.DataFrame:
        return self.tables[name]


def resolve_engine(engine: str | None) -> str:
    """CLI value > ``ANALYTICS_ENGINE`` env var > ``pandas``."""
    chosen = (engine or os.environ.get(DEFAULT_ENGINE_ENV) or "pandas").strip().lower()
    if chosen not in ENGINES:
        raise ValueError(f"Unknown analytics engine '{chosen}'; choose one of {ENGINES}")
    return chosen


def prepare_inputs(
    input_dir: Path, districts: Mapping[str, District], schemes: Mapping[str, SchemeInfo]
) -> CoverageInputs:
    """Check the manifest and the CSV headers; raise ``InputSchemaError`` if unusable."""
    if not schemes or not districts:
        raise InputSchemaError("The scheme catalog and the district reference must not be empty")
    input_dir = Path(input_dir)
    try:
        manifest = read_manifest(input_dir)
    except FileNotFoundError as exc:
        raise InputSchemaError(f"Missing manifest.json in {input_dir}") from exc
    label, synthetic = manifest.get("dataset_label"), manifest.get("is_synthetic")
    if not isinstance(label, str) or not label or not isinstance(synthetic, bool):
        raise InputSchemaError("manifest.json needs a non-empty 'dataset_label' and a boolean 'is_synthetic'")
    inputs = CoverageInputs(input_dir, label, synthetic, districts, schemes)
    for path, columns in (
        (inputs.blocks_path, BLOCK_COLUMNS),
        (inputs.villages_path, VILLAGE_COLUMNS),
        (inputs.status_path, STATUS_COLUMNS),
    ):
        _check_header(path, columns)
    return inputs


def _check_header(path: Path, columns: tuple[str, ...]) -> None:
    if not path.is_file():
        raise InputSchemaError(f"Missing input file {path}")
    with path.open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh), [])
    missing = [c for c in columns if c not in {h.strip() for h in header}]
    if missing:
        raise InputSchemaError(f"{path.name} is missing required columns {missing}")


def run_coverage(
    inputs: CoverageInputs, engine: str = "pandas", spark: SparkSession | None = None
) -> CoverageResult:
    """Run validation + aggregation with the chosen engine. ``spark`` is created on demand if omitted."""
    engine = resolve_engine(engine)
    if engine == "pandas":
        tables, rows = _run_pandas(inputs)
    else:
        if spark is None:
            from ai_modules.analytics.spark_session import get_spark

            spark = get_spark()
        tables, rows = _run_spark(inputs, spark)
    finalized = {name: _finalize(name, df) for name, df in tables.items()}
    return CoverageResult(engine, inputs.dataset_label, inputs.is_synthetic, finalized, rows)


def _finalize(name: str, df: pd.DataFrame) -> pd.DataFrame:
    """Canonical column order, dtypes and row order, so both engines give identical tables."""
    df = df.loc[:, list(OUTPUT_COLUMNS[name])].copy()
    for column in df.columns:
        if column in INT_COLUMNS:
            df[column] = df[column].astype("int64")
        elif column in FLOAT_COLUMNS:
            df[column] = df[column].astype("float64")
        else:
            df[column] = df[column].astype(object).where(df[column].notna(), None)
    df = df.sort_values(list(SORT_KEYS[name]), na_position="first", kind="mergesort")
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------------------------- pandas


def _run_pandas(inputs: CoverageInputs) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    import pandas as pd

    blocks = _read_csv_pandas(inputs.blocks_path, BLOCK_COLUMNS)
    villages = _read_csv_pandas(inputs.villages_path, VILLAGE_COLUMNS)
    status = _read_csv_pandas(inputs.status_path, STATUS_COLUMNS)
    rows = {"blocks": len(blocks), "villages": len(villages), "scheme_status": len(status)}

    # blocks
    blocks["reason"] = _first_reason(
        [
            *_common_checks_pandas(blocks, BLOCK_COLUMNS, "block_id", inputs),
            (~blocks["district_id"].isin(list(inputs.districts)), "unknown_district"),
        ]
    )
    blocks_ok = blocks[blocks["reason"].isna()]

    # villages
    households_valid = villages["households"].str.fullmatch(INT_PATTERN, na=False)
    population_valid = villages["population"].str.fullmatch(INT_PATTERN, na=False)
    villages["households_n"] = pd.to_numeric(villages["households"].where(households_valid), errors="coerce")
    villages["population_n"] = pd.to_numeric(villages["population"].where(population_valid), errors="coerce")
    block_district = villages["block_id"].map(blocks_ok.set_index("block_id")["district_id"])
    villages["reason"] = _first_reason(
        [
            *_common_checks_pandas(villages, VILLAGE_COLUMNS, "village_id", inputs),
            (~households_valid, "invalid_integer_households"),
            (~population_valid, "invalid_integer_population"),
            (villages["households_n"] < 0, "negative_households"),
            (villages["population_n"] < 0, "negative_population"),
            (block_district.isna(), "unknown_block"),
            (block_district != villages["district_id"], "district_mismatch"),
        ]
    )
    villages_ok = villages[villages["reason"].isna()].copy()
    villages_ok["households"] = villages_ok["households_n"].astype("int64")
    villages_ok["population"] = villages_ok["population_n"].astype("int64")

    # scheme_status
    enrolled = status["enrolled"].str.lower()
    status["docs"] = status["missing_documents"].map(_split_documents)
    citizen_scheme_dup = (
        status.duplicated(["citizen_id", "scheme_id"], keep=False) & status["citizen_id"].notna()
    )
    status["reason"] = _first_reason(
        [
            *_common_checks_pandas(status, STATUS_COLUMNS, "record_id", inputs, extra_booleans=("enrolled",)),
            (~status["scheme_id"].isin(list(inputs.schemes)), "unknown_scheme"),
            (~status["eligibility_status"].isin(STATUSES), "invalid_eligibility_status"),
            (~status["village_id"].isin(villages_ok["village_id"]), "unknown_village"),
            ((enrolled == "true") & (status["eligibility_status"] != "eligible"), "enrolled_not_eligible"),
            (
                status["docs"].map(lambda docs: any(d not in KNOWN_DOCUMENT_TYPES for d in docs)),
                "unknown_document_type",
            ),
            (citizen_scheme_dup, "duplicate_citizen_scheme"),
        ]
    )
    s = status[status["reason"].isna()].merge(
        villages_ok[["village_id", "block_id", "district_id"]], on="village_id", how="inner"
    )
    s["is_eligible"] = s["eligibility_status"] == "eligible"
    s["is_enrolled"] = s["is_eligible"] & (s["enrolled"].str.lower() == "true")
    s["is_potential"] = s["eligibility_status"] == "potentially_eligible"

    rejected = pd.concat(
        [
            _rejected_pandas(blocks, "blocks", "block_id"),
            _rejected_pandas(villages, "villages", "village_id"),
            _rejected_pandas(status, "scheme_status", "record_id"),
        ],
        ignore_index=True,
    )

    # area tables
    block_villages = villages_ok.groupby("block_id").size().rename("villages")
    district_blocks = blocks_ok.groupby("district_id").size().rename("blocks")
    district_villages = villages_ok.groupby("district_id").size().rename("villages")
    blocks_out = blocks_ok[["block_id", "block_name", "district_id"]].join(block_villages, on="block_id")
    district_ids = sorted(blocks_ok["district_id"].unique())
    districts_out = pd.DataFrame(
        {
            "district_id": district_ids,
            "district_name": [inputs.districts[d].name for d in district_ids],
            "lat": [inputs.districts[d].lat for d in district_ids],
            "lng": [inputs.districts[d].lng for d in district_ids],
        }
    )
    districts_out = districts_out.join(district_blocks, on="district_id").join(
        district_villages, on="district_id"
    )
    schemes_out = pd.DataFrame(
        {"scheme_id": list(inputs.schemes), "scheme_name": [info.name for info in inputs.schemes.values()]}
    )
    district_schemes_out = districts_out[["district_id"]].merge(schemes_out[["scheme_id"]], how="cross")

    tables = {
        "villages": _with_metrics_pandas(villages_ok, s, ["village_id"]),
        "blocks": _with_metrics_pandas(blocks_out, s, ["block_id"]),
        "districts": _with_metrics_pandas(districts_out, s, ["district_id"]),
        "schemes": _with_metrics_pandas(schemes_out, s, ["scheme_id"]),
        "district_schemes": _with_metrics_pandas(district_schemes_out, s, ["district_id", "scheme_id"]),
        "missing_documents": _missing_documents_pandas(s),
        "rejected": rejected,
    }
    for name in ("blocks", "districts"):
        tables[name] = tables[name].fillna({"villages": 0, "blocks": 0})
    return tables, rows


def _read_csv_pandas(path: Path, columns: tuple[str, ...]) -> pd.DataFrame:
    import pandas as pd

    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""], encoding="utf-8")
    df.columns = [c.strip() for c in df.columns]
    df = df.loc[:, list(columns)]
    for column in columns:
        df[column] = df[column].str.strip(" ")
        df[column] = df[column].where(df[column] != "")
    return df.reset_index(drop=True)


def _common_checks_pandas(
    df: pd.DataFrame,
    columns: tuple[str, ...],
    id_column: str,
    inputs: CoverageInputs,
    extra_booleans: tuple[str, ...] = (),
) -> list[tuple[pd.Series, str]]:
    nullable = {"missing_documents"}
    checks = [(df[c].isna(), f"missing_{c}") for c in columns if c not in nullable]
    for column in ("is_synthetic", *extra_booleans):
        checks.append((~df[column].str.lower().isin(["true", "false"]), f"invalid_boolean_{column}"))
    checks += [
        ((df["is_synthetic"].str.lower() == "true") != inputs.is_synthetic, "synthetic_flag_mismatch"),
        (df["dataset_label"] != inputs.dataset_label, "dataset_label_mismatch"),
        (df[id_column].duplicated(keep=False) & df[id_column].notna(), "duplicate_id"),
    ]
    return checks


def _first_reason(checks: list[tuple[pd.Series, str]]) -> pd.Series:
    import pandas as pd

    reason = pd.Series(None, index=checks[0][0].index, dtype=object)
    for mask, label in checks:
        reason = reason.where(reason.notna() | ~mask.fillna(False).astype(bool), label)
    return reason


def _split_documents(value: object) -> list[str]:
    if not isinstance(value, str):
        return []
    return [token.strip(" ") for token in value.split(DOCUMENT_SEPARATOR) if token.strip(" ")]


def _rejected_pandas(df: pd.DataFrame, table: str, key: str) -> pd.DataFrame:
    bad = df[df["reason"].notna()]
    return bad.assign(table=table, record_key=bad[key])[["table", "record_key", "reason"]]


def coverage_pct(enrolled: int, eligible: int) -> float | None:
    """``enrolled / eligible * 100``, half-up to 1 decimal, integer arithmetic; ``None`` if eligible == 0.

    The vectorised pandas/Spark code below uses the same integer formula so all engines agree exactly.
    """
    if eligible == 0:
        return None
    return ((enrolled * 2000 + eligible) // (2 * eligible)) / 10


def _with_metrics_pandas(areas: pd.DataFrame, s: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    metrics = s.groupby(keys).agg(
        assessed=("record_id", "size"),
        eligible=("is_eligible", "sum"),
        enrolled=("is_enrolled", "sum"),
        potentially_eligible=("is_potential", "sum"),
    )
    out = areas.merge(metrics, left_on=keys, right_index=True, how="left")
    for column in ("assessed", "eligible", "enrolled", "potentially_eligible"):
        out[column] = out[column].fillna(0).astype("int64")
    out["gap"] = out["eligible"] - out["enrolled"]
    has_eligible = out["eligible"] > 0
    safe_denominator = (2 * out["eligible"]).where(has_eligible, 1)
    out["coverage_pct"] = ((out["enrolled"] * 2000 + out["eligible"]) // safe_denominator / 10).where(
        has_eligible
    )
    out["coverage_flag"] = None
    out.loc[~has_eligible, "coverage_flag"] = NO_ELIGIBLE_FLAG
    return out


def _missing_documents_pandas(s: pd.DataFrame) -> pd.DataFrame:
    import pandas as pd

    open_rows = s[s["eligibility_status"].isin(OPEN_STATUSES) & ~s["is_enrolled"]]
    exploded = open_rows.explode("docs").dropna(subset=["docs"])
    frames = []
    for level, key in LEVEL_KEYS.items():
        counts = exploded.groupby([key, "docs"])["citizen_id"].nunique().reset_index()
        counts.columns = ["area_id", "document_type", "citizens"]
        frames.append(counts.assign(level=level))
    return pd.concat(frames, ignore_index=True)


# ---------------------------------------------------------------------------------------------- spark


def _run_spark(inputs: CoverageInputs, spark: SparkSession) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    blocks = _read_csv_spark(spark, inputs.blocks_path, BLOCK_COLUMNS).cache()
    villages = _read_csv_spark(spark, inputs.villages_path, VILLAGE_COLUMNS).cache()
    status = _read_csv_spark(spark, inputs.status_path, STATUS_COLUMNS).cache()
    rows = {"blocks": blocks.count(), "villages": villages.count(), "scheme_status": status.count()}

    # Reference tables are built from literals in the JVM (no Python worker processes needed).
    district_ref = _literal_frame(
        spark,
        [
            {"district_id": d.district_id, "district_name": d.name, "lat": float(d.lat), "lng": float(d.lng)}
            for d in inputs.districts.values()
        ],
    )
    scheme_ref = _literal_frame(
        spark, [{"scheme_id": info.scheme_id, "scheme_name": info.name} for info in inputs.schemes.values()]
    )

    # blocks
    blocks = blocks.join(
        district_ref.select("district_id", F.lit(True).alias("_known")), "district_id", "left"
    )
    blocks = blocks.withColumn(
        "reason",
        _first_reason_spark(
            [
                *_common_checks_spark(BLOCK_COLUMNS, "block_id", inputs),
                (F.col("_known").isNull(), "unknown_district"),
            ]
        ),
    ).cache()
    blocks_ok = blocks.filter(F.col("reason").isNull())

    # villages
    accepted_blocks = blocks_ok.select("block_id", F.col("district_id").alias("_block_district"))
    villages = villages.join(accepted_blocks, "block_id", "left")
    households_n = F.expr("try_cast(households as bigint)")
    population_n = F.expr("try_cast(population as bigint)")
    villages = villages.withColumn(
        "reason",
        _first_reason_spark(
            [
                *_common_checks_spark(VILLAGE_COLUMNS, "village_id", inputs),
                (~F.col("households").rlike(INT_PATTERN), "invalid_integer_households"),
                (~F.col("population").rlike(INT_PATTERN), "invalid_integer_population"),
                (households_n < 0, "negative_households"),
                (population_n < 0, "negative_population"),
                (F.col("_block_district").isNull(), "unknown_block"),
                (F.col("_block_district") != F.col("district_id"), "district_mismatch"),
            ]
        ),
    ).cache()
    villages_ok = villages.filter(F.col("reason").isNull()).select(
        "village_id",
        "village_name",
        "block_id",
        "district_id",
        households_n.alias("households"),
        population_n.alias("population"),
    )

    # scheme_status
    known_docs = F.array(*[F.lit(d) for d in sorted(KNOWN_DOCUMENT_TYPES)])
    docs = F.filter(
        F.transform(F.split(F.coalesce(F.col("missing_documents"), F.lit("")), r"\|"), lambda x: F.trim(x)),
        lambda x: x != "",
    )
    citizen_scheme = Window.partitionBy("citizen_id", "scheme_id")
    status = (
        status.withColumn("docs", docs)
        .withColumn("_citizen_scheme_n", F.count(F.lit(1)).over(citizen_scheme))
        .join(scheme_ref.select("scheme_id", F.lit(True).alias("_known_scheme")), "scheme_id", "left")
        .join(villages_ok.select("village_id", "block_id", "district_id"), "village_id", "left")
    )
    status = status.withColumn(
        "reason",
        _first_reason_spark(
            [
                *_common_checks_spark(STATUS_COLUMNS, "record_id", inputs, extra_booleans=("enrolled",)),
                (F.col("_known_scheme").isNull(), "unknown_scheme"),
                (~F.col("eligibility_status").isin(*STATUSES), "invalid_eligibility_status"),
                (F.col("block_id").isNull(), "unknown_village"),
                (
                    (F.lower("enrolled") == "true") & (F.col("eligibility_status") != "eligible"),
                    "enrolled_not_eligible",
                ),
                (F.size(F.array_except("docs", known_docs)) > 0, "unknown_document_type"),
                (
                    (F.col("_citizen_scheme_n") > 1) & F.col("citizen_id").isNotNull(),
                    "duplicate_citizen_scheme",
                ),
            ]
        ),
    ).cache()
    s = (
        status.filter(F.col("reason").isNull())
        .withColumn("is_eligible", F.col("eligibility_status") == "eligible")
        .withColumn("is_enrolled", F.col("is_eligible") & (F.lower("enrolled") == "true"))
        .withColumn("is_potential", F.col("eligibility_status") == "potentially_eligible")
        .cache()
    )

    rejected = (
        blocks.filter(F.col("reason").isNotNull())
        .select(F.lit("blocks").alias("table"), F.col("block_id").alias("record_key"), "reason")
        .unionByName(
            villages.filter(F.col("reason").isNotNull()).select(
                F.lit("villages").alias("table"), F.col("village_id").alias("record_key"), "reason"
            )
        )
        .unionByName(
            status.filter(F.col("reason").isNotNull()).select(
                F.lit("scheme_status").alias("table"), F.col("record_id").alias("record_key"), "reason"
            )
        )
    )

    # area tables
    block_villages = villages_ok.groupBy("block_id").agg(F.count(F.lit(1)).alias("villages"))
    district_blocks = blocks_ok.groupBy("district_id").agg(F.count(F.lit(1)).alias("blocks"))
    district_villages = villages_ok.groupBy("district_id").agg(F.count(F.lit(1)).alias("villages"))
    blocks_out = (
        blocks_ok.select("block_id", "block_name", "district_id")
        .join(block_villages, "block_id", "left")
        .fillna(0, subset=["villages"])
    )
    districts_out = (
        district_ref.join(district_blocks, "district_id", "inner")
        .join(district_villages, "district_id", "left")
        .fillna(0, subset=["villages"])
    )
    district_schemes_out = districts_out.select("district_id").crossJoin(scheme_ref.select("scheme_id"))

    open_rows = s.filter(F.col("eligibility_status").isin(*OPEN_STATUSES) & ~F.col("is_enrolled"))
    exploded = open_rows.select(*LEVEL_KEYS.values(), "citizen_id", F.explode("docs").alias("document_type"))
    missing = None
    for level, key in LEVEL_KEYS.items():
        counts = exploded.groupBy(F.col(key).alias("area_id"), "document_type").agg(
            F.countDistinct("citizen_id").alias("citizens")
        )
        counts = counts.select(F.lit(level).alias("level"), "area_id", "document_type", "citizens")
        missing = counts if missing is None else missing.unionByName(counts)

    spark_tables: dict[str, SparkDataFrame] = {
        "villages": _with_metrics_spark(villages_ok, s, ["village_id"]),
        "blocks": _with_metrics_spark(blocks_out, s, ["block_id"]),
        "districts": _with_metrics_spark(districts_out, s, ["district_id"]),
        "schemes": _with_metrics_spark(scheme_ref, s, ["scheme_id"]),
        "district_schemes": _with_metrics_spark(district_schemes_out, s, ["district_id", "scheme_id"]),
        "missing_documents": missing,
        "rejected": rejected,
    }
    tables = {name: df.toPandas() for name, df in spark_tables.items()}
    for df in (blocks, villages, status, s):
        df.unpersist()
    return tables, rows


def _literal_frame(spark: SparkSession, rows: list[dict[str, str | float]]) -> SparkDataFrame:
    """Small DataFrame from Python literals via ``inline(array(struct(...)))``, built inside the JVM."""
    from pyspark.sql import functions as F

    structs = [F.struct(*[F.lit(value).alias(key) for key, value in row.items()]) for row in rows]
    return spark.range(1).select(F.inline(F.array(*structs)))


def _read_csv_spark(spark: SparkSession, path: Path, columns: tuple[str, ...]) -> SparkDataFrame:
    from pyspark.sql import functions as F

    df = spark.read.csv(Path(path).resolve().as_posix(), header=True, inferSchema=False, encoding="UTF-8")
    df = df.toDF(*[c.strip() for c in df.columns])
    return df.select(*[F.when(F.trim(F.col(c)) != "", F.trim(F.col(c))).alias(c) for c in columns])


def _common_checks_spark(
    columns: tuple[str, ...], id_column: str, inputs: CoverageInputs, extra_booleans: tuple[str, ...] = ()
) -> list:
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    nullable = {"missing_documents"}
    checks = [(F.col(c).isNull(), f"missing_{c}") for c in columns if c not in nullable]
    for column in ("is_synthetic", *extra_booleans):
        checks.append((~F.lower(column).isin("true", "false"), f"invalid_boolean_{column}"))
    checks += [
        ((F.lower("is_synthetic") == "true") != F.lit(inputs.is_synthetic), "synthetic_flag_mismatch"),
        (F.col("dataset_label") != F.lit(inputs.dataset_label), "dataset_label_mismatch"),
        (
            (F.count(F.lit(1)).over(Window.partitionBy(id_column)) > 1) & F.col(id_column).isNotNull(),
            "duplicate_id",
        ),
    ]
    return checks


def _first_reason_spark(checks: list):
    from pyspark.sql import functions as F

    return F.coalesce(*[F.when(condition, F.lit(label)) for condition, label in checks])


def _with_metrics_spark(areas: SparkDataFrame, s: SparkDataFrame, keys: list[str]) -> SparkDataFrame:
    from pyspark.sql import functions as F

    metrics = s.groupBy(*keys).agg(
        F.count(F.lit(1)).alias("assessed"),
        F.sum(F.col("is_eligible").cast("long")).alias("eligible"),
        F.sum(F.col("is_enrolled").cast("long")).alias("enrolled"),
        F.sum(F.col("is_potential").cast("long")).alias("potentially_eligible"),
    )
    out = areas.join(metrics, keys, "left").fillna(
        0, subset=["assessed", "eligible", "enrolled", "potentially_eligible"]
    )
    has_eligible = F.col("eligible") > 0
    return (
        out.withColumn("gap", F.col("eligible") - F.col("enrolled"))
        .withColumn(
            "coverage_pct",
            F.when(has_eligible, F.expr("(enrolled * 2000 + eligible) div (2 * eligible)") / F.lit(10.0)),
        )
        .withColumn("coverage_flag", F.when(~has_eligible, F.lit(NO_ELIGIBLE_FLAG)))
    )
