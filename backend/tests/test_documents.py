"""Document upload API.

Fast tests replace ``ai_modules.ocr.process_document`` with a function returning a constructed
``OcrDocumentResult`` (they test the backend's handling, not OCR) and never import PaddleOCR.
``@pytest.mark.slow`` tests run real PaddleOCR on the synthetic sample certificates.
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
from pathlib import Path

import pytest
from app.config import get_settings
from app.dependencies import get_explainer
from app.models import AuditLog, Citizen, Document, EligibilityEvaluation, ProfileField
from app.utils.logger import JsonFormatter
from sqlalchemy import func, select, text

from ai_modules import ocr
from ai_modules.ocr import ExtractedField, OcrDocumentResult, OcrUnavailableError
from ai_modules.rag import Explanation

pytestmark = pytest.mark.integration

SAMPLES = Path(__file__).resolve().parents[2] / "data" / "raw" / "sample_documents"
PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic test bytes"  # passes magic-byte sniffing; OCR is faked


def _field(name, value, confidence=0.95, needs_review=False, errors=None) -> ExtractedField:
    return ExtractedField(
        name=name,
        value=value,
        raw_text=str(value),
        confidence=confidence,
        needs_review=needs_review,
        validation_errors=errors or [],
    )


def _result(
    fields,
    document_type="income_certificate",
    missing=None,
    warnings=None,
    detected_type=None,
    mismatch=False,
) -> OcrDocumentResult:
    return OcrDocumentResult(
        document_type=document_type,
        classification_confidence=0.9,
        page_count=1,
        engine="fake-ocr",
        text_length=120,
        fields=fields,
        missing_fields=missing or [],
        warnings=warnings or [],
        detected_type=detected_type or document_type,
        type_mismatch=mismatch,
    )


@pytest.fixture
def fake_ocr(monkeypatch):
    """Install a fake ``process_document``; returns the list of calls made to it."""
    calls: list[dict] = []
    state = {"result": _result([])}

    def process_document(data, filename, declared_type=None):
        calls.append({"size": len(data), "filename": filename, "declared_type": declared_type})
        if isinstance(state["result"], Exception):
            raise state["result"]
        return state["result"]

    monkeypatch.setattr(ocr, "process_document", process_document)
    calls_and_state = calls, state
    return calls_and_state


def _upload(
    client, citizen_id, content=PNG, filename="certificate.png", document_type=None, content_type=None
):
    data = {"document_type": document_type} if document_type else None
    return client.post(
        f"/api/v1/citizens/{citizen_id}/documents",
        files={"file": (filename, content, content_type or "application/octet-stream")},
        data=data,
    )


def _profile(client, citizen_id) -> dict:
    return client.get(f"/api/v1/citizens/{citizen_id}/profile").json()["fields"]


def test_unsupported_type_is_415_and_nothing_stored(client, citizen_id, fake_ocr, db):
    calls, _ = fake_ocr
    response = _upload(client, citizen_id, b"just some text, not an image", "notes.png")
    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_media_type"
    assert calls == []
    assert db.scalars(select(Document).where(Document.citizen_id == uuid.UUID(citizen_id))).all() == []


def test_empty_file_is_422(client, citizen_id, fake_ocr):
    response = _upload(client, citizen_id, b"")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "corrupt_document"


def test_oversized_file_is_413_while_reading(client, citizen_id, fake_ocr, monkeypatch):
    calls, _ = fake_ocr
    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    response = _upload(client, citizen_id, PNG + b"x" * (1024 * 1024))
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert calls == []


def test_declared_type_validation(client, citizen_id, fake_ocr):
    unknown = _upload(client, citizen_id, document_type="passport")
    assert unknown.status_code == 422
    assert unknown.json()["error"]["code"] == "validation_error"
    not_ocr = _upload(client, citizen_id, document_type="land_record")
    assert not_ocr.status_code == 422
    assert not_ocr.json()["error"]["code"] == "document_type_not_ocr_supported"


def test_ocr_unavailable_is_503(client, citizen_id, fake_ocr, db):
    _, state = fake_ocr
    state["result"] = OcrUnavailableError("paddleocr is not installed")
    response = _upload(client, citizen_id)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ocr_unavailable"
    assert db.scalars(select(Document).where(Document.citizen_id == uuid.UUID(citizen_id))).all() == []


def test_missing_ocr_dependency_is_503_not_500(client, citizen_id, fake_ocr, db):
    # Core-only install: the loader's lazy `import PIL` / `import pymupdf` fails inside process_document.
    _, state = fake_ocr
    state["result"] = ModuleNotFoundError("No module named 'PIL'")
    response = _upload(client, citizen_id)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ocr_unavailable"
    assert db.scalars(select(Document).where(Document.citizen_id == uuid.UUID(citizen_id))).all() == []


def test_corrupt_pdf_is_422(client, citizen_id):
    pytest.importorskip("fitz")  # real loader (PyMuPDF); fails before any OCR model is loaded
    pytest.importorskip("PIL")
    response = _upload(client, citizen_id, b"%PDF-1.4\n%garbage that is not a pdf", "broken.pdf")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "corrupt_document"


def test_provenance_application_review_and_conflicts(client, citizen_id, fake_ocr, db):
    _, state = fake_ocr
    client.patch(
        f"/api/v1/citizens/{citizen_id}/profile",
        json={"fields": {"social_category": "obc", "district": "Indore"}},
    )
    before = db.get(Citizen, uuid.UUID(citizen_id)).updated_at
    state["result"] = _result(
        [
            _field("annual_family_income", 180000, 0.93),  # absent -> applied
            _field("social_category", "obc", 0.91),  # equal self_declared -> upgraded
            _field("district", "Sehore", 0.97),  # differing self_declared -> conflict
            _field("full_name", "Synthetic Person", 0.55, needs_review=True),  # review only
            _field("class12_percentage", None, 0.9, errors=["Percentage 140 is above 100"]),
        ],
        missing=["domicile_state"],
        warnings=["Declared type confirmed"],
    )
    response = _upload(client, citizen_id, document_type="income_certificate")
    assert response.status_code == 201, response.text
    body = response.json()
    document_id = body["document"]["id"]
    assert body["duplicate"] is False
    assert {a["field"]: a["previous_provenance"] for a in body["applied_fields"]} == {
        "annual_family_income": None,
        "social_category": "self_declared",
    }
    assert [(c["field"], c["current_value"], c["extracted_value"]) for c in body["conflicts"]] == [
        ("district", "Indore", "Sehore")
    ]
    assert {r["field"] for r in body["review_required"]} == {"full_name", "class12_percentage"}
    assert body["missing_fields"] == ["domicile_state"]
    assert body["document"]["content_type"] == "image/png"
    assert body["document"]["mean_confidence"] == pytest.approx((0.93 + 0.91 + 0.97 + 0.55 + 0.9) / 5)

    fields = _profile(client, citizen_id)
    assert fields["annual_family_income"]["provenance"] == "document_extracted"
    assert fields["annual_family_income"]["confidence"] == pytest.approx(0.93)
    assert fields["annual_family_income"]["source_document_id"] == document_id
    assert fields["social_category"]["provenance"] == "document_extracted"
    assert fields["district"] == {**fields["district"], "value": "Indore", "provenance": "self_declared"}
    assert "full_name" not in fields  # needs review: stored on the document only

    stored = db.get(Document, uuid.UUID(document_id))
    assert all("raw_text" not in item for item in stored.extracted_fields)
    db.expire_all()
    assert db.get(Citizen, uuid.UUID(citizen_id)).updated_at > before

    # The user confirms the reviewed name -> self_declared.
    client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"full_name": "Synthetic Person"}})
    assert _profile(client, citizen_id)["full_name"]["provenance"] == "self_declared"


def test_later_document_replaces_earlier_but_never_verified(client, citizen_id, fake_ocr, db):
    _, state = fake_ocr
    state["result"] = _result([_field("annual_family_income", 180000), _field("district", "Sehore")])
    first = _upload(client, citizen_id, PNG + b"1").json()
    db.add(ProfileField(citizen_id=uuid.UUID(citizen_id), field_name="age", value=30, provenance="verified"))
    db.commit()
    state["result"] = _result([_field("annual_family_income", 175000), _field("age", 31)])
    second = _upload(client, citizen_id, PNG + b"2").json()
    assert [a["field"] for a in second["applied_fields"]] == ["annual_family_income"]
    assert [(c["field"], c["current_provenance"]) for c in second["conflicts"]] == [("age", "verified")]
    fields = _profile(client, citizen_id)
    assert fields["annual_family_income"]["value"] == 175000
    assert fields["annual_family_income"]["source_document_id"] == second["document"]["id"]
    assert fields["district"]["source_document_id"] == first["document"]["id"]
    assert fields["age"]["provenance"] == "verified" and fields["age"]["value"] == 30


def test_duplicate_upload_returns_existing_without_ocr(client, citizen_id, fake_ocr):
    calls, state = fake_ocr
    state["result"] = _result([_field("annual_family_income", 180000), _field("full_name", "X Y", 0.5, True)])
    first = _upload(client, citizen_id)
    second = _upload(client, citizen_id)
    assert first.status_code == 201 and second.status_code == 200
    assert len(calls) == 1
    body = second.json()
    assert body["duplicate"] is True
    assert body["document"]["id"] == first.json()["document"]["id"]
    assert body["document"]["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert body["applied_fields"] == [] and [r["field"] for r in body["review_required"]] == ["full_name"]


def test_identity_numbers_never_applied_and_filename_sanitized(client, citizen_id, fake_ocr, db):
    _, state = fake_ocr
    state["result"] = _result([_field("full_name", "1234 5678 9012", 0.99)])
    body = _upload(client, citizen_id, filename="..\\..\\secret/1234-5678-9012 card<>.png").json()
    assert body["applied_fields"] == []
    assert body["review_required"][0]["value"] is None
    assert "identity number" in body["review_required"][0]["validation_errors"][0]
    name = body["document"]["original_filename"]
    assert "/" not in name and "\\" not in name and "1234" not in name and name.endswith(".png")
    stored = db.get(Document, uuid.UUID(body["document"]["id"]))
    assert "1234" not in str(stored.extracted_fields)


def test_list_get_delete_document(client, citizen_id, fake_ocr, db):
    _, state = fake_ocr
    state["result"] = _result(
        [_field("annual_family_income", 180000), _field("district", "Sehore"), _field("age", 19)]
    )
    document_id = _upload(client, citizen_id).json()["document"]["id"]
    # The user corrects one OCR value: it is no longer sourced from the document.
    client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"district": "Bhopal"}})

    listed = client.get(f"/api/v1/citizens/{citizen_id}/documents").json()
    assert [d["id"] for d in listed] == [document_id]
    assert client.get(f"/api/v1/documents/{document_id}").json()["document_type"] == "income_certificate"

    response = client.delete(f"/api/v1/documents/{document_id}")
    assert response.status_code == 200
    assert response.json()["removed_profile_fields"] == ["age", "annual_family_income"]
    fields = _profile(client, citizen_id)
    assert set(fields) == {"district"} and fields["district"]["provenance"] == "self_declared"
    assert client.get(f"/api/v1/documents/{document_id}").status_code == 404
    assert client.delete(f"/api/v1/documents/{document_id}").json()["error"]["code"] == "document_not_found"
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_id == document_id)).all()
    assert sorted(actions) == ["delete", "upload"]


def test_upload_for_unknown_citizen_is_404(client, fake_ocr):
    response = _upload(client, uuid.uuid4())
    assert response.status_code == 404


# ------------------------------------------------------------------------- hardening regressions


@pytest.mark.parametrize("ocr_flags_fields", [True, False])
def test_type_mismatch_applies_nothing_and_is_surfaced(client, citizen_id, fake_ocr, ocr_flags_fields):
    _, state = fake_ocr
    mismatch = "declared_type_mismatch:caste_certificate!=income_certificate"
    state["result"] = _result(
        [
            # OCR forces review on mismatch; the backend must not apply even if a field slipped through.
            _field("annual_family_income", 180000, 0.97, needs_review=ocr_flags_fields),
            _field("district", "Sehore", 0.96, needs_review=ocr_flags_fields),
        ],
        document_type="caste_certificate",
        detected_type="income_certificate",
        warnings=["Declared 'caste_certificate' but the content looks like 'income_certificate'", mismatch],
        mismatch=True,
    )
    response = _upload(client, citizen_id, document_type="caste_certificate")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["type_mismatch"] is True and mismatch in body["warnings"]
    assert body["applied_fields"] == [] and body["conflicts"] == []
    assert {(r["field"], r["value"]) for r in body["review_required"]} == {
        ("annual_family_income", 180000),
        ("district", "Sehore"),
    }
    assert body["document"]["document_type"] == "caste_certificate"
    assert _profile(client, citizen_id) == {}
    duplicate = _upload(client, citizen_id, document_type="caste_certificate").json()
    assert duplicate["duplicate"] is True and duplicate["type_mismatch"] is True


def test_confidence_threshold_boundary(client, citizen_id, fake_ocr):
    _, state = fake_ocr
    state["result"] = _result(
        [
            _field("annual_family_income", 180000, 0.80),  # exactly at the threshold -> applied
            _field("district", "Sehore", 0.79999),  # just below: review even if OCR did not flag it
            _field("age", 19, float("nan")),  # non-finite confidence is never trusted
        ]
    )
    body = _upload(client, citizen_id).json()
    assert [a["field"] for a in body["applied_fields"]] == ["annual_family_income"]
    review = {r["field"]: r for r in body["review_required"]}
    assert set(review) == {"district", "age"}
    assert review["district"]["value"] == "Sehore" and review["district"]["confidence"] == pytest.approx(0.8)
    assert review["age"]["confidence"] == 0.0
    fields = _profile(client, citizen_id)
    assert set(fields) == {"annual_family_income"}
    assert fields["annual_family_income"]["confidence"] == pytest.approx(0.80)


def test_soft_notes_keep_value_for_review_hard_errors_drop_it(client, citizen_id, fake_ocr, db):
    _, state = fake_ocr
    note = "OCR and the PDF text layer disagree on this value"
    layer_only = "Read from the PDF text layer only; OCR did not confirm it"
    state["result"] = _result(
        [
            # Soft note: OCR kept the value but flagged it -> shown with its value, never applied.
            _field("annual_family_income", 180000, 0.97, needs_review=True, errors=[note]),
            # Soft note without the OCR review flag: still never applied.
            _field("district", "Sehore", 0.97, errors=[layer_only]),
            # Hard failure: OCR found no valid value.
            _field("class12_percentage", None, 0.9, needs_review=True, errors=["Percentage 140 > 100"]),
            # Soft note but the backend's own validation rejects the value -> dropped.
            _field("full_name", "1234 5678 9012", 0.97, needs_review=True, errors=[note]),
        ]
    )
    body = _upload(client, citizen_id).json()
    assert body["applied_fields"] == []
    review = {r["field"]: r for r in body["review_required"]}
    assert review["annual_family_income"]["value"] == 180000
    assert review["annual_family_income"]["validation_errors"] == [note]
    assert review["district"]["value"] == "Sehore"
    assert review["class12_percentage"]["value"] is None
    assert review["full_name"]["value"] is None
    assert any("identity number" in e for e in review["full_name"]["validation_errors"])
    assert _profile(client, citizen_id) == {}
    stored = db.get(Document, uuid.UUID(body["document"]["id"]))
    assert "1234" not in str(stored.extracted_fields)


@pytest.mark.parametrize(
    ("content", "claimed", "detected"),
    [
        (PNG, "application/pdf", "image/png"),
        (b"%PDF-1.4\n" + b"synthetic pdf body", "image/png", "application/pdf"),
        (b"\xff\xd8\xff\xe0" + b"synthetic jpeg body", "application/pdf", "image/jpeg"),
    ],
)
def test_content_type_is_sniffed_not_trusted(client, citizen_id, fake_ocr, content, claimed, detected):
    _, state = fake_ocr
    state["result"] = _result([])
    response = _upload(client, citizen_id, content, "file.bin", content_type=claimed)
    assert response.status_code == 201, response.text
    assert response.json()["document"]["content_type"] == detected


def test_lying_header_on_unsupported_content_is_415(client, citizen_id, fake_ocr):
    calls, _ = fake_ocr
    for claimed in ("image/png", "application/pdf"):
        response = _upload(client, citizen_id, b"<html>not an image</html>", "x.png", content_type=claimed)
        assert response.status_code == 415
    assert calls == []


def test_duplicate_upload_never_reapplies(client, citizen_id, fake_ocr):
    calls, state = fake_ocr
    state["result"] = _result([_field("annual_family_income", 180000)])
    first = _upload(client, citizen_id).json()
    assert [a["field"] for a in first["applied_fields"]] == ["annual_family_income"]
    client.patch(f"/api/v1/citizens/{citizen_id}/profile", json={"fields": {"annual_family_income": 175000}})
    second = _upload(client, citizen_id)
    assert second.status_code == 200 and second.json()["applied_fields"] == []
    income = _profile(client, citizen_id)["annual_family_income"]
    assert income["value"] == 175000 and income["provenance"] == "self_declared"
    assert len(calls) == 1


def test_delete_after_conflicts_keeps_user_values_and_reverts_confirmed(client, citizen_id, fake_ocr, db):
    _, state = fake_ocr
    client.patch(
        f"/api/v1/citizens/{citizen_id}/profile",
        json={"fields": {"district": "Indore", "social_category": "obc"}},
    )
    state["result"] = _result(
        [
            _field("district", "Sehore", 0.97),  # conflict: user value kept
            _field("social_category", "obc", 0.95),  # confirms the declared value -> upgraded
            _field("annual_family_income", 180000, 0.93),  # new value
        ],
        document_type="caste_certificate",
    )
    body = _upload(client, citizen_id).json()
    assert [c["field"] for c in body["conflicts"]] == ["district"]
    fields = _profile(client, citizen_id)
    assert fields["social_category"]["provenance"] == "document_extracted"

    deleted = client.delete(f"/api/v1/documents/{body['document']['id']}").json()
    assert deleted["removed_profile_fields"] == ["annual_family_income"]
    assert deleted["reverted_profile_fields"] == ["social_category"]
    fields = _profile(client, citizen_id)
    assert set(fields) == {"district", "social_category"}
    assert fields["district"]["value"] == "Indore" and fields["district"]["provenance"] == "self_declared"
    social = fields["social_category"]
    assert (social["value"], social["provenance"], social["confidence"], social["source_document_id"]) == (
        "obc",
        "self_declared",
        None,
        None,
    )
    audit = db.scalars(
        select(AuditLog).where(AuditLog.entity_id == body["document"]["id"], AuditLog.action == "delete")
    ).one()
    assert audit.details["reverted_profile_fields"] == ["social_category"]
    assert "obc" not in str(audit.details) and "Indore" not in str(audit.details)


def test_document_rows_hold_metadata_only_and_logs_no_values(client, citizen_id, fake_ocr, db):
    _, state = fake_ocr
    secret_name = "Zqxwv Syntheticuploadname"
    raw_marker = "RAWTEXT-MARKER-should-never-be-stored"
    content = PNG + b"FILEBYTES-MARKER"
    filename = "C:\\Users\\me\\Aadhaar 1234.5678.9012 scan<1>.png"
    state["result"] = _result(
        [
            ExtractedField("full_name", secret_name, f"{raw_marker} {secret_name}", 0.97, False, []),
            ExtractedField("annual_family_income", 180000, f"{raw_marker} 1,80,000", 0.95, False, []),
        ]
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    previous = root.level
    root.setLevel(logging.DEBUG)
    try:
        body = _upload(client, citizen_id, content, filename).json()
        client.get(f"/api/v1/citizens/{citizen_id}/documents")
        client.delete(f"/api/v1/documents/{body['document']['id']}")
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)
    logs = stream.getvalue()
    assert "document_processed" in logs
    for secret in (secret_name, raw_marker, "180000", "1234"):
        assert secret not in logs

    # Upload again (the first one was deleted) and inspect the stored row column by column.
    document_id = _upload(client, citizen_id, content, filename).json()["document"]["id"]
    row = db.execute(text("SELECT * FROM documents WHERE id = :id"), {"id": document_id}).mappings().one()
    dumped = repr(dict(row))
    assert raw_marker not in dumped and "FILEBYTES-MARKER" not in dumped and "raw_text" not in dumped
    assert not any(isinstance(v, bytes | memoryview) for v in row.values())
    assert row["original_filename"] == "Aadhaar _redacted_ scan_1_.png"
    assert row["sha256"] == hashlib.sha256(content).hexdigest()
    assert {item["name"] for item in row["extracted_fields"]} == {"full_name", "annual_family_income"}


class _EchoExplainer:
    def explain(self, evaluation, rule, question=None, language="en") -> Explanation:
        return Explanation(
            answer="test double",
            decision=evaluation.status.value,
            citations=(),
            grounded=False,
            provider="none",
            model=None,
            uncertainty=None,
            language=language,
            scheme_id=rule.scheme_id,
        )


def test_upload_and_delete_refresh_recommendations_and_explanations(client, citizen_id, fake_ocr, app, db):
    _, state = fake_ocr
    app.dependency_overrides[get_explainer] = _EchoExplainer
    try:
        client.patch(
            f"/api/v1/citizens/{citizen_id}/profile",
            json={"fields": {"age": 20, "occupation": "student", "enrolled_in_higher_education": True}},
        )
        base = client.get(f"/api/v1/citizens/{citizen_id}/recommendations").json()

        state["result"] = _result([_field("annual_family_income", 150000, 0.95)])
        document_id = _upload(client, citizen_id).json()["document"]["id"]
        latest = client.get(f"/api/v1/citizens/{citizen_id}/evaluations/latest").json()
        assert latest["stale_reasons"] == ["profile_changed"]
        after_upload = client.get(f"/api/v1/citizens/{citizen_id}/recommendations").json()
        assert after_upload["evaluated_now"] is True and after_upload["run_id"] != base["run_id"]
        used = after_upload["evaluations"][0]
        url = f"/api/v1/citizens/{citizen_id}/schemes/{used['scheme_id']}/explanation"
        explanation = client.post(url).json()
        assert explanation["run_id"] == after_upload["run_id"] and explanation["evaluated_now"] is False
        assert explanation["evaluation_id"] == used["evaluation_id"]
        assert explanation["rule_version"] == used["rule_version"]

        client.delete(f"/api/v1/documents/{document_id}")
        explanation = client.post(url).json()
        assert explanation["evaluated_now"] is True and explanation["stale_reasons"] == ["profile_changed"]
        after_delete = client.get(f"/api/v1/citizens/{citizen_id}/recommendations").json()
        assert after_delete["evaluated_now"] is False
        assert after_delete["run_id"] == explanation["run_id"] != after_upload["run_id"]
        # Same stored profile as before the upload, so the fingerprint matches the first run again.
        assert after_delete["profile_fingerprint"] == base["profile_fingerprint"]
        runs = db.scalar(
            select(func.count(func.distinct(EligibilityEvaluation.run_id))).where(
                EligibilityEvaluation.citizen_id == uuid.UUID(citizen_id)
            )
        )
        assert runs == 3
    finally:
        app.dependency_overrides.pop(get_explainer, None)


# ------------------------------------------------------------------------------------- real PaddleOCR


def _conditions(run: dict, scheme_id: str) -> dict[str, str]:
    for group in ("eligible", "potentially_eligible", "not_eligible"):
        for item in run[group]:
            if item["scheme_id"] == scheme_id:
                return {c["condition_id"]: c["outcome"] for c in item["conditions"]}
    raise AssertionError(f"{scheme_id} not evaluated")


@pytest.mark.slow
def test_real_ocr_certificates_update_profile_and_evaluation(client, citizen_id):
    pytest.importorskip("paddleocr")
    client.patch(
        f"/api/v1/citizens/{citizen_id}/profile",
        json={"fields": {"age": 20, "occupation": "student", "enrolled_in_higher_education": True}},
    )
    before = client.post(f"/api/v1/citizens/{citizen_id}/evaluations").json()
    assert _conditions(before, "hostel_assistance")["category_sc_or_st"] == "missing"
    assert _conditions(before, "post_matric_scholarship_obc")["category_obc"] == "missing"

    income = _upload(
        client,
        citizen_id,
        (SAMPLES / "synthetic_income_certificate_en_01.png").read_bytes(),
        "synthetic_income_certificate_en_01.png",
        "income_certificate",
    )
    assert income.status_code == 201, income.text
    caste = _upload(
        client,
        citizen_id,
        (SAMPLES / "synthetic_caste_certificate_en_01.png").read_bytes(),
        "synthetic_caste_certificate_en_01.png",
    )  # type detected from the content
    assert caste.status_code == 201, caste.text
    assert caste.json()["document"]["document_type"] == "caste_certificate"

    fields = _profile(client, citizen_id)
    assert fields["annual_family_income"]["value"] == 416000  # expected_fields of the synthetic sample
    assert fields["annual_family_income"]["provenance"] == "document_extracted"
    assert fields["social_category"]["value"] == "st"
    assert fields["social_category"]["provenance"] == "document_extracted"
    assert fields["social_category"]["source_document_id"] == caste.json()["document"]["id"]

    after = client.post(f"/api/v1/citizens/{citizen_id}/evaluations").json()
    assert _conditions(after, "hostel_assistance")["category_sc_or_st"] == "pass"
    assert _conditions(after, "post_matric_scholarship_obc")["category_obc"] == "fail"  # ST, not OBC
    assert _conditions(after, "post_matric_scholarship_obc")["income_up_to_3_lakh"] == "fail"  # INR 4,16,000
