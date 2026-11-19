"""Shared fixtures: a tiny hand-computed dataset, an invalid-rows dataset and a local Spark session."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from ai_modules.analytics.reference import SchemeInfo, load_districts
from ai_modules.analytics.synthetic import BLOCK_COLUMNS, STATUS_COLUMNS, VILLAGE_COLUMNS

LABEL = "TEST-FIXTURE"
SCHEMES = {
    "pm_jay": SchemeInfo("pm_jay", "Ayushman Bharat PM-JAY", ("ration_card",)),
    "pm_kisan": SchemeInfo("pm_kisan", "PM-KISAN", ("land_record", "samagra_domicile")),
}

BLOCKS = [
    ("bhopal_b01", "Block 01", "bhopal"),
    ("bhopal_b02", "Block 02", "bhopal"),
    ("sehore_b01", "Block 01", "sehore"),
]
VILLAGES = [
    ("v1", "Village 1", "bhopal_b01", "bhopal", "3", "10"),
    ("v2", "Village 2", "bhopal_b01", "bhopal", "2", "7"),
    ("v3", "Village 3", "bhopal_b02", "bhopal", "0", "0"),  # no assessments -> zero denominator
    ("v4", "Village 4", "sehore_b01", "sehore", "1", "2"),
]
# record_id, citizen_id, household_id, village_id, scheme_id, eligibility_status, enrolled, missing_documents
STATUS = [
    ("r1", "c1", "h1", "v1", "pm_kisan", "eligible", "true", ""),
    ("r2", "c1", "h1", "v1", "pm_jay", "eligible", "false", "ration_card"),
    ("r3", "c2", "h2", "v1", "pm_kisan", "eligible", "false", "land_record|samagra_domicile"),
    ("r4", "c2", "h2", "v1", "pm_jay", "potentially_eligible", "false", "ration_card"),
    ("r5", "c3", "h3", "v1", "pm_kisan", "not_eligible", "false", ""),
    ("r6", "c4", "h4", "v2", "pm_kisan", "eligible", "true", ""),
    ("r7", "c4", "h4", "v2", "pm_jay", "eligible", "TRUE", ""),
    ("r8", "c5", "h5", "v2", "pm_jay", "eligible", "false", " samagra_domicile "),
    ("r9", "c6", "h6", "v4", "pm_kisan", "eligible", "false", "land_record"),
    ("r10", "c6", "h6", "v4", "pm_jay", "not_eligible", "false", ""),
]

# Invalid rows appended to the clean fixture; expected (table, record_key, reason) for each.
BAD_BLOCKS = [
    (("dup_b", "Dup A", "bhopal"), ("blocks", "dup_b", "duplicate_id")),
    (("dup_b", "Dup B", "bhopal"), ("blocks", "dup_b", "duplicate_id")),
    (("atlantis_b01", "Lost", "atlantis"), ("blocks", "atlantis_b01", "unknown_district")),
    (("noname_b01", "", "bhopal"), ("blocks", "noname_b01", "missing_block_name")),
]
BAD_VILLAGES = [
    (("vneg", "Neg", "bhopal_b01", "bhopal", "-3", "5"), ("villages", "vneg", "negative_households")),
    (("vabc", "Abc", "bhopal_b01", "bhopal", "abc", "5"), ("villages", "vabc", "invalid_integer_households")),
    (("vpop", "Pop", "bhopal_b01", "bhopal", "2", "4.5"), ("villages", "vpop", "invalid_integer_population")),
    (("vorphan", "Orphan", "dup_b", "bhopal", "2", "5"), ("villages", "vorphan", "unknown_block")),
    (("vmis", "Mismatch", "bhopal_b01", "sehore", "2", "5"), ("villages", "vmis", "district_mismatch")),
    (("vnull", "Null", "bhopal_b01", "bhopal", "", "5"), ("villages", "vnull", "missing_households")),
]
BAD_STATUS = [
    (("b1", "x1", "hx", "v1", "pm_unknown", "eligible", "false", ""), "unknown_scheme"),
    (("b2", "x2", "hx", "v1", "pm_kisan", "maybe", "false", ""), "invalid_eligibility_status"),
    (("b3", "x3", "hx", "v1", "pm_kisan", "not_eligible", "true", ""), "enrolled_not_eligible"),
    (("b4", "x4", "hx", "v1", "pm_kisan", "eligible", "false", "passport"), "unknown_document_type"),
    (("b5", "x5", "hx", "vneg", "pm_kisan", "eligible", "false", ""), "unknown_village"),
    (("b6", "x6", "hx", "", "pm_kisan", "eligible", "false", ""), "missing_village_id"),
    (("b7", "x7", "hx", "v1", "pm_kisan", "eligible", "yes", ""), "invalid_boolean_enrolled"),
    (("b8", "x8", "hx", "v1", "pm_kisan", "eligible", "false", ""), "duplicate_id"),
    (("b8", "x9", "hx", "v1", "pm_jay", "eligible", "false", ""), "duplicate_id"),
    (("b9", "x10", "hx", "v1", "pm_kisan", "eligible", "false", ""), "duplicate_citizen_scheme"),
    (("b10", "x10", "hx", "v1", "pm_kisan", "eligible", "true", ""), "duplicate_citizen_scheme"),
    (("", "x11", "hx", "v1", "pm_kisan", "eligible", "false", ""), "missing_record_id"),
]
# Rows whose provenance columns are wrong (record, is_synthetic, dataset_label, reason).
BAD_PROVENANCE = [
    (
        ("p1", "y1", "hy", "v1", "pm_kisan", "eligible", "false", ""),
        "false",
        LABEL,
        "synthetic_flag_mismatch",
    ),
    (
        ("p2", "y2", "hy", "v1", "pm_kisan", "eligible", "false", ""),
        "true",
        "OTHER",
        "dataset_label_mismatch",
    ),
    (
        ("p3", "y3", "hy", "v1", "pm_kisan", "eligible", "false", ""),
        "maybe",
        LABEL,
        "invalid_boolean_is_synthetic",
    ),
]


def _write(path: Path, columns: tuple[str, ...], rows: list[tuple]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(columns)
        writer.writerows(rows)


def write_fixture(directory: Path, *, invalid: bool = False) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    tail = ("true", LABEL)
    blocks = [(*b, *tail) for b in BLOCKS]
    villages = [(*v, *tail) for v in VILLAGES]
    status = [(*s, *tail) for s in STATUS]
    if invalid:
        blocks += [(*row, *tail) for row, _ in BAD_BLOCKS]
        villages += [(*row, *tail) for row, _ in BAD_VILLAGES]
        status += [(*row, *tail) for row, _ in BAD_STATUS]
        status += [(*row, flag, label) for row, flag, label, _ in BAD_PROVENANCE]
    _write(directory / "blocks.csv", BLOCK_COLUMNS, blocks)
    _write(directory / "villages.csv", VILLAGE_COLUMNS, villages)
    _write(directory / "scheme_status.csv", STATUS_COLUMNS, status)
    (directory / "manifest.json").write_text(
        json.dumps({"dataset_label": LABEL, "is_synthetic": True}), "utf-8"
    )
    return directory


def expected_rejections() -> list[tuple[str, str | None, str]]:
    rows = [expected for _, expected in BAD_BLOCKS + BAD_VILLAGES]
    rows += [("scheme_status", row[0] or None, reason) for row, reason in BAD_STATUS]
    rows += [("scheme_status", row[0], reason) for row, _, _, reason in BAD_PROVENANCE]
    return sorted(rows, key=lambda r: (r[0], r[1] or "", r[2]))


@pytest.fixture(scope="session")
def districts():
    return load_districts()


@pytest.fixture()
def clean_dir(tmp_path: Path) -> Path:
    return write_fixture(tmp_path / "clean")


@pytest.fixture()
def invalid_dir(tmp_path: Path) -> Path:
    return write_fixture(tmp_path / "invalid", invalid=True)


@pytest.fixture(scope="session")
def spark():
    pytest.importorskip("pyspark")
    from ai_modules.analytics.spark_session import JavaNotFoundError, get_spark

    try:
        session = get_spark()
    except JavaNotFoundError as exc:
        pytest.skip(f"Spark tests need a JDK: {exc}")
    yield session
    session.stop()
