from __future__ import annotations

import hashlib
import uuid

import pytest
from app.models import AuditLog, BenefitEnrollment, Citizen, Document, EligibilityEvaluation, ProfileField
from sqlalchemy import func, select

from ai_modules.rule_engine.fields import PROFILE_FIELDS

pytestmark = pytest.mark.integration


def _add_ocr_field(db, citizen_id: str, name: str, value, confidence: float = 0.91) -> uuid.UUID:
    """Simulate what the OCR pipeline stores: document metadata + a document_extracted field."""
    document = Document(
        citizen_id=uuid.UUID(citizen_id),
        document_type="income_certificate",
        original_filename="synthetic_income_certificate.pdf",
        content_type="application/pdf",
        size_bytes=1024,
        sha256=hashlib.sha256(b"synthetic test document").hexdigest(),
        status="processed",
        ocr_engine="test",
        mean_confidence=confidence,
    )
    db.add(document)
    db.flush()
    db.add(
        ProfileField(
            citizen_id=uuid.UUID(citizen_id),
            field_name=name,
            value=value,
            provenance="document_extracted",
            confidence=confidence,
            source_document_id=document.id,
        )
    )
    db.commit()
    return document.id


def test_create_get_citizen(client):
    created = client.post("/api/v1/citizens", json={"label": "Synthetic demo"})
    assert created.status_code == 201
    body = created.json()
    assert body["label"] == "Synthetic demo"
    assert body["is_synthetic"] is True
    fetched = client.get(f"/api/v1/citizens/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == body


def test_create_citizen_without_body(client):
    response = client.post("/api/v1/citizens")
    assert response.status_code == 201
    assert response.json()["label"] is None


def test_profile_field_catalog(client):
    catalog = client.get("/api/v1/profile-fields").json()
    names = [f["name"] for f in catalog["fields"]]
    assert names == list(PROFILE_FIELDS)
    gender = next(f for f in catalog["fields"] if f["name"] == "gender")
    assert gender["type"] == "enum" and "female" in gender["choices"]
    assert {d["id"] for d in catalog["document_types"]} >= {"income_certificate", "caste_certificate"}
    assert [p["id"] for p in catalog["provenance_levels"]] == [
        "self_declared",
        "document_extracted",
        "verified",
    ]


def test_empty_profile_lists_all_fields_missing(client, citizen_id):
    profile = client.get(f"/api/v1/citizens/{citizen_id}/profile").json()
    assert profile["fields"] == {}
    assert profile["missing_fields"] == list(PROFILE_FIELDS)


def test_patch_profile_sets_self_declared_values_and_persists(client, citizen_id, db):
    response = client.patch(
        f"/api/v1/citizens/{citizen_id}/profile",
        json={"fields": {"age": "19", "gender": "Female", "family_owns_cultivable_land": "no"}},
    )
    assert response.status_code == 200, response.text
    fields = response.json()["fields"]
    assert fields["age"]["value"] == 19  # coerced to int
    assert fields["gender"]["value"] == "female"
    assert fields["family_owns_cultivable_land"]["value"] is False
    assert {f["provenance"] for f in fields.values()} == {"self_declared"}
    assert "age" not in response.json()["missing_fields"]

    rows = db.scalars(select(ProfileField).where(ProfileField.citizen_id == uuid.UUID(citizen_id))).all()
    assert {r.field_name: r.value for r in rows} == {
        "age": 19,
        "gender": "female",
        "family_owns_cultivable_land": False,
    }
    audit = db.scalars(
        select(AuditLog).where(AuditLog.entity_id == citizen_id, AuditLog.action == "profile_update")
    ).one()
    assert {c["field"] for c in audit.details["changed"]} == {"age", "gender", "family_owns_cultivable_land"}
    # Audit details contain field names and provenance only, never the values.
    assert "19" not in str(audit.details) and "female" not in str(audit.details)


def test_patch_null_clears_field(client, citizen_id):
    client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"district": "Sehore"}})
    response = client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"district": None}})
    assert response.status_code == 200
    assert "district" not in response.json()["fields"]
    assert "district" in response.json()["missing_fields"]


def test_user_correction_of_ocr_value_becomes_self_declared(client, citizen_id, db):
    _add_ocr_field(db, citizen_id, "annual_family_income", 180000)
    profile = client.get(f"/api/v1/citizens/{citizen_id}/profile").json()
    income = profile["fields"]["annual_family_income"]
    assert income["provenance"] == "document_extracted"
    assert income["confidence"] == pytest.approx(0.91)
    assert income["source_document_id"] is not None

    response = client.patch(
        f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"annual_family_income": 175000}}
    )
    corrected = response.json()["fields"]["annual_family_income"]
    assert corrected["value"] == 175000
    assert corrected["provenance"] == "self_declared"
    assert corrected["confidence"] is None
    assert corrected["source_document_id"] is None


def test_user_confirming_ocr_value_keeps_provenance(client, citizen_id, db):
    document_id = _add_ocr_field(db, citizen_id, "annual_family_income", 180000)
    response = client.patch(
        f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"annual_family_income": "180000"}}
    )
    income = response.json()["fields"]["annual_family_income"]
    assert income["provenance"] == "document_extracted"
    assert income["source_document_id"] == str(document_id)
    assert income["confidence"] == pytest.approx(0.91)


@pytest.mark.parametrize(
    ("fields", "loc_field", "fragment"),
    [
        ({"aadhaar_number": "1234"}, "aadhaar_number", "Unknown profile field"),
        ({"age": "nineteen"}, "age", "must be an integer"),
        ({"age": 19.5}, "age", "must be an integer"),
        ({"age": 130}, "age", "<= 120"),
        ({"annual_family_income": -5}, "annual_family_income", ">= 0"),
        ({"class12_percentage": 101}, "class12_percentage", "<= 100"),
        ({"gender": "unknown"}, "gender", "must be one of"),
        ({"social_category": "xyz"}, "social_category", "must be one of"),
        ({"owns_pucca_house": "maybe"}, "owns_pucca_house", "must be a boolean"),
        ({"district": "   "}, "district", "must not be empty"),
    ],
)
def test_profile_validation_errors(client, citizen_id, fields, loc_field, fragment):
    response = client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": fields})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["body", "fields", loc_field]
    assert fragment in error["details"][0]["msg"]


def test_validation_reports_every_invalid_field_and_writes_nothing(client, citizen_id, db):
    response = client.patch(
        f"/api/v1/citizens/{citizen_id}/profile",
        json={"fields": {"age": 20, "gender": "x", "class12_percentage": "abc"}},
    )
    assert response.status_code == 422
    assert {d["loc"][-1] for d in response.json()["error"]["details"]} == {"gender", "class12_percentage"}
    count = db.scalar(
        select(func.count()).select_from(ProfileField).where(ProfileField.citizen_id == uuid.UUID(citizen_id))
    )
    assert count == 0


def test_empty_patch_rejected(client, citizen_id):
    response = client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {}})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_enrollments_roundtrip(client, citizen_id, db):
    scheme_id = client.get("/api/v1/schemes").json()[0]["scheme_id"]
    response = client.put(
        f"/api/v1/citizens/{citizen_id}/enrollments/{scheme_id}", json={"status": "applied"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["provenance"] == "self_declared"
    client.put(f"/api/v1/citizens/{citizen_id}/enrollments/{scheme_id}", json={"status": "enrolled"})
    listed = client.get(f"/api/v1/citizens/{citizen_id}/enrollments").json()
    assert [(e["scheme_id"], e["status"]) for e in listed] == [(scheme_id, "enrolled")]
    rows = db.scalars(
        select(BenefitEnrollment).where(BenefitEnrollment.citizen_id == uuid.UUID(citizen_id))
    ).all()
    assert len(rows) == 1


def test_enrollment_unknown_scheme_and_bad_status(client, citizen_id):
    unknown = client.put(
        f"/api/v1/citizens/{citizen_id}/enrollments/not_a_scheme", json={"status": "enrolled"}
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "scheme_not_found"
    scheme_id = client.get("/api/v1/schemes").json()[0]["scheme_id"]
    bad = client.put(f"/api/v1/citizens/{citizen_id}/enrollments/{scheme_id}", json={"status": "maybe"})
    assert bad.status_code == 422


def test_delete_citizen_cascades_and_audits(client, citizen_id, db):
    client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"age": 30}})
    _add_ocr_field(db, citizen_id, "annual_family_income", 100000)
    scheme_id = client.get("/api/v1/schemes").json()[0]["scheme_id"]
    client.put(f"/api/v1/citizens/{citizen_id}/enrollments/{scheme_id}", json={"status": "enrolled"})
    assert client.post(f"/api/v1/citizens/{citizen_id}/evaluations").status_code == 201

    response = client.delete(f"/api/v1/citizens/{citizen_id}")
    assert response.status_code == 204
    assert client.get(f"/api/v1/citizens/{citizen_id}").status_code == 404

    cid = uuid.UUID(citizen_id)
    db.expire_all()
    assert db.get(Citizen, cid) is None
    for model in (ProfileField, Document, BenefitEnrollment, EligibilityEvaluation):
        count = db.scalar(select(func.count()).select_from(model).where(model.citizen_id == cid))
        assert count == 0, model.__name__
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == citizen_id)).all()
    assert "delete" in actions and "create" in actions


def test_delete_unknown_citizen_is_404(client):
    response = client.delete(f"/api/v1/citizens/{uuid.uuid4()}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "citizen_not_found"
