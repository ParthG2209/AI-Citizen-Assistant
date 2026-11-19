"""Synthetic generator: determinism, labelling and referential integrity."""

from __future__ import annotations

import csv
import dataclasses

import pytest

from ai_modules.analytics import synthetic
from ai_modules.analytics.pipeline import ensure_synthetic_dataset
from ai_modules.analytics.reference import SchemeInfo

SCHEMES = {
    scheme_id: SchemeInfo(scheme_id, scheme_id.upper(), docs)
    for scheme_id, docs in {
        "pm_kisan": ("land_record", "samagra_domicile"),
        "pm_jay": ("ration_card",),
        "gaon_ki_beti": ("class12_marksheet", "samagra_domicile"),
        "post_matric_scholarship_obc": ("caste_certificate", "income_certificate", "college_admission"),
        "future_scheme": ("income_certificate",),  # no SCHEME_DRAWS entry -> DEFAULT_DRAW
    }.items()
}
SMALL = synthetic.SyntheticConfig(
    seed=7,
    district_ids=("bhopal", "sehore"),
    blocks_per_district=(2, 3),
    villages_per_block=(2, 3),
    households_per_village=(5, 8),
)


def _files(directory):
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


def test_same_seed_gives_identical_files(tmp_path, districts):
    a = synthetic.write_dataset(synthetic.generate(districts, SCHEMES, SMALL), tmp_path / "a")
    b = synthetic.write_dataset(synthetic.generate(districts, SCHEMES, SMALL), tmp_path / "b")
    assert _files(a) == _files(b)
    assert set(_files(a)) == {"blocks.csv", "villages.csv", "scheme_status.csv", "manifest.json"}


def test_different_seed_changes_data(districts):
    other = dataclasses.replace(SMALL, seed=8)
    assert (
        synthetic.generate(districts, SCHEMES, SMALL).scheme_status
        != synthetic.generate(districts, SCHEMES, other).scheme_status
    )


def test_every_record_is_labelled_and_consistent(tmp_path, districts):
    data = synthetic.generate(districts, SCHEMES, SMALL)
    directory = synthetic.write_dataset(data, tmp_path / "ds")
    assert data.manifest["is_synthetic"] is True
    assert data.manifest["dataset_label"] == synthetic.DATASET_LABEL
    assert data.manifest["scheme_ids"] == sorted(SCHEMES)

    tables = {}
    for name in ("blocks.csv", "villages.csv", "scheme_status.csv"):
        with (directory / name).open(encoding="utf-8", newline="") as fh:
            tables[name] = list(csv.DictReader(fh))
        assert tables[name], name
        assert {row["is_synthetic"] for row in tables[name]} == {"true"}
        assert {row["dataset_label"] for row in tables[name]} == {synthetic.DATASET_LABEL}

    block_ids = {b["block_id"] for b in tables["blocks.csv"]}
    village_ids = {v["village_id"] for v in tables["villages.csv"]}
    assert {b["district_id"] for b in tables["blocks.csv"]} == {"bhopal", "sehore"}
    assert all("(synthetic)" in b["block_name"] for b in tables["blocks.csv"])
    assert all(v["block_id"] in block_ids for v in tables["villages.csv"])
    statuses = tables["scheme_status.csv"]
    assert len({s["record_id"] for s in statuses}) == len(statuses)
    assert {s["scheme_id"] for s in statuses} <= set(SCHEMES)
    for row in statuses:
        assert row["village_id"] in village_ids
        assert row["eligibility_status"] in {"eligible", "potentially_eligible", "not_eligible"}
        if row["enrolled"] == "true":
            assert row["eligibility_status"] == "eligible" and row["missing_documents"] == ""
        docs = [d for d in row["missing_documents"].split("|") if d]
        assert set(docs) <= set(SCHEMES[row["scheme_id"]].required_documents)
        if row["eligibility_status"] == "potentially_eligible":
            assert docs
    # household-level scheme drawn once per household head only
    assert all(s["citizen_id"].endswith("_m1") for s in statuses if s["scheme_id"] == "pm_kisan")


def test_unknown_district_is_rejected(districts):
    with pytest.raises(ValueError, match="atlantis"):
        synthetic.generate(districts, SCHEMES, synthetic.SyntheticConfig(district_ids=("atlantis",)))


def test_ensure_synthetic_dataset_regenerates_only_when_needed(tmp_path, districts):
    target = tmp_path / "ds"
    assert ensure_synthetic_dataset(target, districts, SCHEMES, SMALL) == "no dataset found"
    assert ensure_synthetic_dataset(target, districts, SCHEMES, SMALL) is None
    fewer = {k: v for k, v in SCHEMES.items() if k != "future_scheme"}
    assert ensure_synthetic_dataset(target, districts, fewer, SMALL) == "rule catalog scheme ids changed"
    assert (
        ensure_synthetic_dataset(target, districts, fewer, SMALL, regenerate=True) == "--regenerate requested"
    )
