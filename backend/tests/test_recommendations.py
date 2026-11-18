from __future__ import annotations

import uuid
from collections import Counter

import pytest
from app.config import get_settings
from app.services.recommendation_service import citizen_recommendations

from ai_modules.knowledge_graph import KnowledgeGraph, Neo4jSettings
from ai_modules.rule_engine.fields import DOCUMENT_TYPES

pytestmark = pytest.mark.integration

# Synthetic student profile with income / caste left out so that document gaps exist.
STUDENT = {
    "age": 19,
    "gender": "female",
    "domicile_state": "Madhya Pradesh",
    "district": "Sehore",
    "residence_type": "rural",
    "occupation": "student",
    "enrolled_in_higher_education": True,
    "class12_percentage": 82.4,
    "class12_board": "mpbse",
    "class12_passing_year": 2025,
}
# Synthetic farmer profile.
FARMER = {
    "age": 46,
    "gender": "male",
    "domicile_state": "Madhya Pradesh",
    "district": "Vidisha",
    "residence_type": "rural",
    "occupation": "farmer",
    "social_category": "sc",
    "annual_family_income": 90000,
    "family_owns_cultivable_land": True,
    "owns_pucca_house": False,
    "family_member_income_tax_payer": False,
    "family_member_government_employee": False,
    "family_member_pensioner_above_10k": False,
    "listed_in_secc_or_nfsa": True,
}


def _citizen(client, profile: dict) -> str:
    citizen_id = client.post("/api/v1/citizens", json={"label": "pytest synthetic"}).json()["id"]
    assert client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": profile}).status_code == 200
    return citizen_id


def _recommend(client, citizen_id: str, **params) -> dict:
    response = client.get(f"/api/v1/citizens/{citizen_id}/recommendations", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def _offered(report: dict) -> set[str]:
    return {
        r["scheme_id"] for key in ("unclaimed", "potentially_eligible", "complementary") for r in report[key]
    }


def test_response_shape_and_run_reuse(client):
    citizen_id = _citizen(client, FARMER)
    first = _recommend(client, citizen_id)
    assert first["evaluated_now"] is True  # no run existed yet -> evaluated and persisted
    assert first["graph_source"] in {"neo4j", "fallback"}
    assert first["citizen_id"] == citizen_id
    assert first["disclaimer"]
    for key in ("unclaimed", "potentially_eligible", "complementary", "document_gaps", "information_needs"):
        assert isinstance(first[key], list)

    latest = client.get(f"/api/v1/citizens/{citizen_id}/evaluations/latest").json()
    assert latest["run_id"] == first["run_id"]
    statuses = {i["scheme_id"]: i["status"] for g in ("eligible", "potentially_eligible") for i in latest[g]}
    for rec in first["unclaimed"]:
        assert statuses[rec["scheme_id"]] == "eligible"
    for rec in first["potentially_eligible"]:
        assert statuses[rec["scheme_id"]] == "potentially_eligible"

    second = _recommend(client, citizen_id)
    assert second["evaluated_now"] is False
    assert second["run_id"] == first["run_id"]
    assert second["profile_changed_since_evaluation"] is False


def test_profile_edit_reevaluates_and_refresh_forces_new_run(client):
    citizen_id = _citizen(client, FARMER)
    first = _recommend(client, citizen_id)
    client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"annual_family_income": 95000}})
    updated = _recommend(client, citizen_id)
    assert updated["evaluated_now"] is True and updated["stale_reasons"] == ["profile_changed"]
    assert updated["run_id"] != first["run_id"]
    assert updated["profile_changed_since_evaluation"] is False
    assert _recommend(client, citizen_id)["run_id"] == updated["run_id"]  # fresh again: reused
    forced = _recommend(client, citizen_id, refresh="true")
    assert forced["evaluated_now"] is True and forced["stale_reasons"] == []
    assert forced["run_id"] != updated["run_id"]


def test_enrolled_and_applied_schemes_are_excluded(client):
    citizen_id = _citizen(client, FARMER)
    before = _recommend(client, citizen_id)
    candidates = [r["scheme_id"] for r in before["unclaimed"] + before["potentially_eligible"]]
    assert len(candidates) >= 2, before
    enrolled, applied = candidates[0], candidates[1]
    client.put(f"/api/v1/citizens/{citizen_id}/enrollments/{enrolled}", json={"status": "enrolled"})
    client.put(f"/api/v1/citizens/{citizen_id}/enrollments/{applied}", json={"status": "applied"})

    after = _recommend(client, citizen_id)
    assert after["run_id"] == before["run_id"]  # enrollment changes do not need a new evaluation
    assert set(after["enrolled_scheme_ids"]) == {enrolled, applied}
    assert enrolled not in _offered(after) and applied not in _offered(after)
    for totals in (after["unclaimed_totals"], after["potential_totals"]):
        assert not {enrolled, applied} & set(totals["counted_scheme_ids"])

    client.put(f"/api/v1/citizens/{citizen_id}/enrollments/{applied}", json={"status": "not_enrolled"})
    assert applied in _offered(_recommend(client, citizen_id))


def test_document_gaps_point_at_missing_conditions(client):
    citizen_id = _citizen(client, STUDENT)
    report = _recommend(client, citizen_id)
    assert report["document_gaps"], "income / caste certificates should be requested"
    run = client.get(f"/api/v1/citizens/{citizen_id}/evaluations/latest").json()
    missing_ids: dict[str, set[str]] = {}

    def collect(scheme_id: str, conditions: list[dict]) -> None:
        for c in conditions:
            if c["outcome"] == "missing":
                missing_ids.setdefault(scheme_id, set()).add(c["condition_id"])
            collect(scheme_id, c["children"])

    for item in run["potentially_eligible"]:
        collect(item["scheme_id"], item["conditions"])

    for gap in report["document_gaps"]:
        assert gap["doc_id"] in DOCUMENT_TYPES
        assert gap["schemes"]
        for scheme in gap["schemes"]:
            assert scheme["scheme_id"] in missing_ids, gap
            assert set(scheme["condition_ids"]) <= missing_ids[scheme["scheme_id"]]


def test_totals_never_double_count(client):
    for profile in (FARMER, STUDENT):
        report = _recommend(client, _citizen(client, profile))
        unclaimed, potential = report["unclaimed_totals"], report["potential_totals"]
        for totals in (unclaimed, potential):
            counted = totals["counted_scheme_ids"]
            assert len(counted) == len(set(counted))
            assert len({line["scheme_id"] for line in totals["lines"]}) == len(totals["lines"])
            by_category = Counter()
            for line in totals["lines"]:
                if line["amount_inr"] is not None:
                    by_category[line["category"]] += line["amount_inr"]
            assert by_category["one_time_cash"] == pytest.approx(totals["one_time_cash_inr"])
            assert by_category["fee_reimbursement"] == pytest.approx(totals["fee_reimbursement_inr"])
        assert not set(unclaimed["counted_scheme_ids"]) & set(potential["counted_scheme_ids"])
        assert set(unclaimed["counted_scheme_ids"]) <= {r["scheme_id"] for r in report["unclaimed"]}


def test_unknown_citizen_is_404(client):
    response = client.get(f"/api/v1/citizens/{uuid.uuid4()}/recommendations")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "citizen_not_found"


def test_fallback_graph_is_used_when_forced(app, client):
    engine = app.state.rule_engine
    original = app.state.knowledge_graph
    app.state.knowledge_graph = KnowledgeGraph.connect(rules=engine.schemes(), use_neo4j=False)
    try:
        report = _recommend(client, _citizen(client, FARMER))
    finally:
        app.state.knowledge_graph = original
    assert report["graph_source"] == "fallback"


def test_missing_graph_is_503(app, client):
    citizen_id = _citizen(client, FARMER)
    original = app.state.knowledge_graph
    app.state.knowledge_graph = None
    try:
        response = client.get(f"/api/v1/citizens/{citizen_id}/recommendations")
    finally:
        app.state.knowledge_graph = original
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "knowledge_graph_unavailable"


def test_neo4j_and_fallback_give_identical_reports(app, client, db, skip_or_fail):
    settings = get_settings()
    engine = app.state.rule_engine
    neo4j_graph = KnowledgeGraph.connect(
        rules=engine.schemes(),
        settings=Neo4jSettings(
            uri=settings.neo4j_uri,
            user=settings.neo4j_user,
            password=settings.neo4j_password.get_secret_value(),
        ),
    )
    fallback_graph = KnowledgeGraph.connect(rules=engine.schemes(), use_neo4j=False)
    try:
        if neo4j_graph.source != "neo4j":
            skip_or_fail("Neo4j not reachable; start `docker compose up -d neo4j`")
        for profile in (FARMER, STUDENT):
            citizen_id = uuid.UUID(_citizen(client, profile))
            via_neo4j = citizen_recommendations(db, engine, neo4j_graph, citizen_id)
            via_fallback = citizen_recommendations(db, engine, fallback_graph, citizen_id)
            assert via_neo4j.graph_source == "neo4j" and via_fallback.graph_source == "fallback"
            exclude = {"graph_source", "evaluated_now"}
            assert via_neo4j.model_dump(exclude=exclude) == via_fallback.model_dump(exclude=exclude)
    finally:
        neo4j_graph.close()
        fallback_graph.close()
