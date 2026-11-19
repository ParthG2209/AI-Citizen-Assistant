"""Analytics API over a small synthetic dataset produced by the real pandas pipeline in a tmp dir."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("pandas")

from app.config import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402

from ai_modules.analytics import synthetic  # noqa: E402
from ai_modules.analytics.pipeline import run_pipeline  # noqa: E402
from ai_modules.analytics.reference import load_districts, schemes_from_rules  # noqa: E402
from ai_modules.rule_engine import RuleEngine  # noqa: E402

DATASET_KEYS = {"dataset_label", "is_synthetic", "generated_at", "engine"}


@pytest.fixture(scope="module")
def outputs_dir(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("analytics")
    districts = load_districts()
    schemes = schemes_from_rules(RuleEngine(rules_dir=get_settings().rules_dir).schemes())
    config = synthetic.SyntheticConfig(
        district_ids=synthetic.DEFAULT_DISTRICTS[:3],
        blocks_per_district=(2, 3),
        villages_per_block=(2, 4),
        households_per_village=(5, 10),
    )
    synthetic.write_dataset(synthetic.generate(districts, schemes, config), root / "input")
    run_pipeline(root / "input", schemes, engine="pandas", output_dir=root / "out", report=False)
    return root / "out"


@pytest.fixture
def api(outputs_dir, monkeypatch) -> TestClient:
    monkeypatch.setattr(get_settings(), "analytics_output_dir", outputs_dir)
    return TestClient(create_app())  # no lifespan needed: analytics only reads files


def _get(api: TestClient, path: str, **params) -> dict:
    response = api.get(f"/api/v1/analytics{path}", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert DATASET_KEYS <= set(body)
    assert body["is_synthetic"] is True and body["dataset_label"]
    return body


def test_summary_and_metadata(api):
    summary = _get(api, "/summary")
    assert summary["districts"] == 3
    assert summary["gap"] == summary["eligible"] - summary["enrolled"]
    metadata = _get(api, "/metadata")
    assert "SYNTHETIC" in metadata["disclaimer"]
    assert metadata["generated_at"] == summary["generated_at"]


def test_districts_and_detail(api):
    districts = _get(api, "/districts")
    assert districts["count"] == len(districts["items"]) == 3
    district = districts["items"][0]
    assert all(item["is_synthetic"] for item in districts["items"])
    detail = _get(api, f"/districts/{district['id']}")
    assert detail["district"]["id"] == district["id"]
    assert len(detail["blocks"]) == district["blocks"]
    assert {b["district_id"] for b in detail["blocks"]} == {district["id"]}
    assert {s["district_id"] for s in detail["schemes"]} == {district["id"]}
    assert sum(s["eligible"] for s in detail["schemes"]) == district["eligible"]


def test_unknown_district_is_404(api):
    response = api.get("/api/v1/analytics/districts/atlantis")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "district_not_found"
    assert api.get("/api/v1/analytics/blocks", params={"district_id": "atlantis"}).status_code == 404
    assert api.get("/api/v1/analytics/villages", params={"block_id": "nope"}).status_code == 404


def test_block_filters(api):
    all_blocks = _get(api, "/blocks")
    district_id = all_blocks["items"][0]["district_id"]
    filtered = _get(api, "/blocks", district_id=district_id)
    assert filtered["count"] == len(filtered["items"]) > 0
    assert {b["district_id"] for b in filtered["items"]} == {district_id}
    under = _get(api, "/blocks", underserved="true")
    over = _get(api, "/blocks", underserved="false")
    assert under["count"] + over["count"] == all_blocks["count"]
    assert all(b["underserved"] for b in under["items"])


def test_village_filters_and_pagination(api):
    everything = _get(api, "/villages", limit=1000)
    total = everything["total"]
    assert total == len(everything["items"]) > 3

    page1 = _get(api, "/villages", limit=2, offset=0)
    page2 = _get(api, "/villages", limit=2, offset=2)
    assert page1["total"] == page2["total"] == total
    assert [v["id"] for v in page1["items"] + page2["items"]] == [v["id"] for v in everything["items"][:4]]
    assert _get(api, "/villages", offset=total)["items"] == []

    block_id = everything["items"][0]["block_id"]
    in_block = _get(api, "/villages", block_id=block_id, limit=1000)
    assert {v["block_id"] for v in in_block["items"]} == {block_id}
    under = _get(api, "/villages", underserved="true", limit=1000)
    assert all(v["underserved"] for v in under["items"])
    assert under["total"] == sum(v["underserved"] for v in everything["items"])

    assert api.get("/api/v1/analytics/villages", params={"limit": 0}).status_code == 422
    assert api.get("/api/v1/analytics/villages", params={"offset": -1}).status_code == 422


def test_scheme_adoption(api):
    schemes = _get(api, "/schemes")
    assert schemes["count"] == len(schemes["items"]) > 0
    for item in schemes["items"]:
        assert item["enrolled"] <= item["eligible"]


def test_outputs_reloaded_after_regeneration(api, outputs_dir, tmp_path, monkeypatch):
    copy = tmp_path / "copy"
    shutil.copytree(outputs_dir, copy)
    monkeypatch.setattr(get_settings(), "analytics_output_dir", copy)
    first = _get(api, "/summary")
    summary_file = copy / "summary.json"
    summary_file.write_text(summary_file.read_text("utf-8").replace(first["engine"], "spark", 1), "utf-8")
    assert _get(api, "/summary")["engine"] == "spark"


def test_missing_outputs_is_503(tmp_path, monkeypatch):
    monkeypatch.setattr(get_settings(), "analytics_output_dir", tmp_path / "empty")
    response = TestClient(create_app()).get("/api/v1/analytics/summary")
    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "analytics_not_generated"
    assert "run_district_analytics.py" in error["details"]["hint"]
