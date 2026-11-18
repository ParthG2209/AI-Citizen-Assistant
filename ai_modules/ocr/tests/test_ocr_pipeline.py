"""Fast ``process_document`` tests: real upload decoding, a scripted OCR engine (no models)."""

import dataclasses
import io
import json
import logging
import re
import zipfile
from datetime import date

import pytest

from ai_modules.ocr import (
    CorruptDocumentError,
    FileTooLargeError,
    OcrLine,
    UnsupportedFileError,
    process_document,
)
from ai_modules.ocr.field_extractor import classify
from ai_modules.ocr.validator import DEFAULT_REVIEW_THRESHOLD, combine_confidence

pytest.importorskip("PIL")
pymupdf = pytest.importorskip("pymupdf")

AS_OF = date(2025, 6, 1)
AADHAAR_LIKE = re.compile(r"(?<!\d)\d{4}[ .\-]?\d{4}[ .\-]?\d{4}(?:[ .\-]?\d{4})?(?!\d)")


class ScriptedEngine:
    """Stands in for ``PaddleEngine``: returns fixed rows of text for every page."""

    name = "scripted"

    def __init__(self, *rows, confidence=0.98):
        self.rows = rows
        self.confidence = confidence
        self.calls = 0

    def recognize(self, image, page=0):
        self.calls += 1
        lines = []
        for r, row in enumerate(self.rows):
            x = 50.0
            for text in [row] if isinstance(row, str) else row:
                conf = self.confidence
                if isinstance(text, tuple):
                    text, conf = text
                width = 12.0 * len(text)
                lines.append(OcrLine(text, conf, (x, 40.0 * r, x + width, 40.0 * r + 24), page))
                x += width + 40
        return lines


INCOME_ROWS = (
    ["INCOME CERTIFICATE"],
    ["Name of Applicant:", "Priya Verma"],
    ["Father's Name:", "Mohan Verma"],
    ["District:", "Sehore"],
    ["Annual Family Income:", "Rs. 1,80,000/-"],
    ["Issued by Tehsildar"],
)


def png_bytes(size=(64, 32)):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, "white").save(buf, format="PNG")
    return buf.getvalue()


def pdf_bytes(*lines, pages=1):
    with pymupdf.open() as pdf:
        for _ in range(pages):
            page = pdf.new_page()
            for i, text in enumerate(lines):
                page.insert_text((72, 72 + 24 * i), text)
        return pdf.tobytes()


def run(data, filename="doc.png", declared_type=None, engine=None):
    return process_document(
        data, filename, declared_type, engine=engine or ScriptedEngine(*INCOME_ROWS), as_of=AS_OF
    )


# --------------------------------------------------------------------------- upload rejection
@pytest.mark.parametrize(
    ("data", "filename"),
    [
        (b"Annual income Rs. 1,20,000", "certificate.txt"),
        (b"Annual income Rs. 1,20,000", "certificate.pdf"),
        (b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>', "scan.svg"),
        (b'<svg xmlns="http://www.w3.org/2000/svg"></svg>', "scan.png"),
        (b"GIF89a" + b"\x00" * 32, "scan.gif"),
        (b"BM" + b"\x00" * 64, "scan.bmp"),
    ],
)
def test_unsupported_formats_rejected_before_ocr(data, filename):
    engine = ScriptedEngine(*INCOME_ROWS)
    with pytest.raises(UnsupportedFileError):
        run(data, filename, engine=engine)
    assert engine.calls == 0


def test_docx_zip_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", "<w:document/>")
    engine = ScriptedEngine(*INCOME_ROWS)
    for name in ("certificate.docx", "certificate.pdf"):
        with pytest.raises(UnsupportedFileError):
            run(buf.getvalue(), name, engine=engine)
    assert engine.calls == 0


def test_empty_file_rejected():
    engine = ScriptedEngine(*INCOME_ROWS)
    with pytest.raises(CorruptDocumentError, match="empty"):
        run(b"", "a.png", engine=engine)
    assert engine.calls == 0


@pytest.mark.parametrize("cut", [60, 0.5])
def test_truncated_png_rejected(cut):
    data = png_bytes((400, 300))
    data = data[: cut if isinstance(cut, int) else int(len(data) * cut)]
    engine = ScriptedEngine(*INCOME_ROWS)
    with pytest.raises(CorruptDocumentError):
        run(data, "a.png", engine=engine)
    assert engine.calls == 0


@pytest.mark.parametrize(
    "data",
    [
        b"%PDF-1.7\n",
        b"%PDF-1.7\n" + bytes(range(256)) * 20,
        pdf_bytes("Income Certificate")[:200],
    ],
)
def test_corrupted_pdf_rejected(data):
    engine = ScriptedEngine(*INCOME_ROWS)
    with pytest.raises(CorruptDocumentError):
        run(data, "a.pdf", engine=engine)
    assert engine.calls == 0


def test_damaged_but_repairable_pdf_warns():
    data = pdf_bytes("Income Certificate")
    result = run(data[: len(data) // 2], "a.pdf", declared_type="income_certificate")
    assert any("repaired" in w for w in result.warnings)


def test_oversized_file_rejected_via_env(monkeypatch):
    monkeypatch.setenv("ACA_OCR_MAX_FILE_MB", "0.001")
    engine = ScriptedEngine(*INCOME_ROWS)
    with pytest.raises(FileTooLargeError):
        run(png_bytes() + b"\x00" * 2048, "a.png", engine=engine)
    assert engine.calls == 0


def test_too_many_pages_rejected_via_env(monkeypatch):
    monkeypatch.setenv("ACA_OCR_MAX_PAGES", "2")
    engine = ScriptedEngine(*INCOME_ROWS)
    with pytest.raises(FileTooLargeError, match="pages"):
        run(pdf_bytes("x", pages=3), "a.pdf", engine=engine)
    assert engine.calls == 0
    assert run(pdf_bytes("x", pages=2), "a.pdf").page_count == 2


# --------------------------------------------------------------------------- sniffing wins
def test_png_named_pdf_is_read_as_png():
    result = run(png_bytes(), "certificate.pdf")
    assert result.page_count == 1 and "pdf-text-layer" not in result.engine
    assert any("'.pdf' does not match its content (png)" in w for w in result.warnings)
    assert result.document_type == "income_certificate"


def test_pdf_named_png_is_read_as_pdf():
    data = pdf_bytes("INCOME CERTIFICATE", "District: Sehore", pages=2)
    result = run(data, "certificate.png")
    assert result.page_count == 2 and result.engine.endswith("+ pdf-text-layer")
    assert any("'.png' does not match its content (pdf)" in w for w in result.warnings)


# --------------------------------------------------------------------------- confidence threshold
def test_threshold_is_inclusive_at_the_boundary(monkeypatch):
    """Docs: values *below* the review threshold (0.80) need review, so exactly 0.80 does not."""
    monkeypatch.delenv("ACA_OCR_REVIEW_THRESHOLD", raising=False)
    assert combine_confidence(DEFAULT_REVIEW_THRESHOLD, 1.0) == DEFAULT_REVIEW_THRESHOLD
    at = run(
        png_bytes(),
        declared_type="income_certificate",
        engine=ScriptedEngine(["INCOME CERTIFICATE"], [("District: Sehore", 0.80)], confidence=0.99),
    )
    below = run(
        png_bytes(),
        declared_type="income_certificate",
        engine=ScriptedEngine(["INCOME CERTIFICATE"], [("District: Sehore", 0.7999)], confidence=0.99),
    )
    (at_field,) = at.fields
    (below_field,) = below.fields
    assert at_field.confidence == 0.8 and not at_field.needs_review
    assert below_field.confidence == 0.7999 and below_field.needs_review
    assert at_field.value == below_field.value == "Sehore"


def test_threshold_boundary_follows_env(monkeypatch):
    monkeypatch.setenv("ACA_OCR_REVIEW_THRESHOLD", "0.9")
    rows = (["INCOME CERTIFICATE"], [("District: Sehore", 0.9)])
    (at_field,) = run(png_bytes(), declared_type="income_certificate", engine=ScriptedEngine(*rows)).fields
    assert not at_field.needs_review
    rows = (["INCOME CERTIFICATE"], [("District: Sehore", 0.8999)])
    (below,) = run(png_bytes(), declared_type="income_certificate", engine=ScriptedEngine(*rows)).fields
    assert below.needs_review


# --------------------------------------------------------------------------- text layer vs OCR
def test_text_layer_and_ocr_disagreement_forces_review():
    data = pdf_bytes("INCOME CERTIFICATE", "District: Sehore", "Annual Family Income: Rs. 1,80,000")
    engine = ScriptedEngine(
        ["INCOME CERTIFICATE"], ["District:", "Indore"], ["Annual Family Income:", "Rs. 1,80,000/-"]
    )
    result = run(data, "a.pdf", declared_type="income_certificate", engine=engine)
    fields = {f.name: f for f in result.fields}
    assert fields["district"].value == "Indore" and fields["district"].needs_review
    assert any("disagree" in e for e in fields["district"].validation_errors)
    assert any("'district'" in w and "disagree" in w for w in result.warnings)
    # Agreement: confident and not flagged.
    assert fields["annual_family_income"].value == 180000 and not fields["annual_family_income"].needs_review


def test_value_only_in_text_layer_forces_review():
    data = pdf_bytes("INCOME CERTIFICATE", "District: Sehore")
    result = run(
        data, "a.pdf", declared_type="income_certificate", engine=ScriptedEngine(["INCOME CERTIFICATE"])
    )
    (district,) = result.fields
    assert district.value == "Sehore" and district.needs_review
    assert any("text layer only" in e for e in district.validation_errors)


# --------------------------------------------------------------------------- declared vs detected
def test_declared_type_matching_content_is_not_a_mismatch():
    result = run(png_bytes(), declared_type="income_certificate")
    assert result.type_mismatch is False
    assert not any(w.startswith("declared_type_mismatch:") for w in result.warnings)
    assert {f.name: f.needs_review for f in result.fields} == {
        "full_name": False,
        "annual_family_income": False,
        "district": False,
    }


def test_declared_type_mismatch_forces_review_of_every_field():
    result = run(png_bytes(), declared_type="caste_certificate")
    assert result.document_type == "caste_certificate"
    assert result.detected_type == "income_certificate"
    assert result.classification_confidence >= 0.5
    assert result.type_mismatch is True
    assert "declared_type_mismatch:caste_certificate!=income_certificate" in result.warnings
    assert result.fields, "caste template still extracts the applicant name"
    assert all(f.needs_review for f in result.fields)
    # Values are kept for the citizen to confirm; no validation error is invented.
    assert {f.name: f.value for f in result.fields} == {"full_name": "Priya Verma", "district": "Sehore"}
    assert all(not f.validation_errors for f in result.fields)
    assert "social_category" in result.missing_fields


def test_mismatch_boundary_uses_uncertain_classification_threshold():
    at_rows = (["Annual Income:", "Rs. 90,000"], ["Tehsildar"], ["Name of Applicant:", "Priya Verma"])
    text = "Annual Income: Rs. 90,000\nTehsildar\nName of Applicant: Priya Verma"
    assert classify(text).document_type == "income_certificate" and classify(text).confidence == 0.5
    at = run(png_bytes(), declared_type="caste_certificate", engine=ScriptedEngine(*at_rows))
    assert at.type_mismatch and all(f.needs_review for f in at.fields)

    below_rows = (*at_rows, ["caste"])
    assert classify(text + "\ncaste").confidence < 0.5
    below = run(png_bytes(), declared_type="caste_certificate", engine=ScriptedEngine(*below_rows))
    assert below.detected_type == "income_certificate" and not below.type_mismatch
    assert not any(w.startswith("declared_type_mismatch:") for w in below.warnings)
    assert any("looks like 'income_certificate'" in w for w in below.warnings)


def test_undeclared_type_never_mismatches():
    result = run(png_bytes())
    assert result.document_type == result.detected_type == "income_certificate"
    assert result.type_mismatch is False


# --------------------------------------------------------------------------- privacy
AADHAAR = "2345 6789 0123"
AADHAAR_DOTTED = "3456.7890.1234"
AADHAAR_BARE = "456789012345"
VID = "5678-9012-3456-7890"
SAMAGRA_MEMBER = "123456789"
SAMAGRA_FAMILY = "87654321"
SECRETS = (AADHAAR, AADHAAR_DOTTED, AADHAAR_BARE, VID, SAMAGRA_MEMBER, SAMAGRA_FAMILY)

SAMAGRA_ROWS = (
    ["समग्र / SAMAGRA PROFILE"],
    ["Samagra Member ID:", SAMAGRA_MEMBER],
    ["Samagra Family ID:", SAMAGRA_FAMILY],
    ["Name of Member:", "Pooja Lodhi"],
    ["Gender:", "Female"],
    ["Date of Birth:", "12/05/2007"],
    ["District:", "Vidisha", "Aadhaar No.:", AADHAAR],
    ["State:", "Madhya Pradesh"],
    ["Residence:", "Rural", f"UID {AADHAAR_DOTTED}"],
    [f"Aadhaar {AADHAAR_BARE} VID {VID}"],
)


def _digits(text):
    return re.sub(r"\D", "", text)


def test_result_never_exposes_identifiers_text_or_bytes(caplog):
    data = png_bytes((300, 200))
    with caplog.at_level(logging.DEBUG):
        result = run(data, "samagra.png", engine=ScriptedEngine(*SAMAGRA_ROWS))
    assert result.document_type == "samagra_domicile"
    values = {f.name: f.value for f in result.fields}
    assert values["has_samagra_id"] is True and values["full_name"] == "Pooja Lodhi"

    payload = dataclasses.asdict(result)
    blob = json.dumps(payload, ensure_ascii=False, default=repr)
    for secret in SECRETS:
        assert _digits(secret) not in _digits(blob), secret
    assert not AADHAAR_LIKE.search(blob)
    assert not re.search(r"\d{8,}", blob.replace(" ", ""))

    # No full OCR text: no string in the result spans more than one source row.
    def strings(obj):
        if isinstance(obj, dict):
            for v in obj.values():
                yield from strings(v)
        elif isinstance(obj, list | tuple):
            for v in obj:
                yield from strings(v)
        elif isinstance(obj, str):
            yield obj

    for s in strings(payload):
        assert "Pooja Lodhi" not in s or "Vidisha" not in s
        assert "SAMAGRA PROFILE" not in s
    assert result.text_length > 0

    # No bytes anywhere in the result.
    def has_bytes(obj):
        if isinstance(obj, bytes | bytearray | memoryview):
            return True
        if isinstance(obj, dict):
            return any(has_bytes(v) for v in obj.values())
        if isinstance(obj, list | tuple):
            return any(has_bytes(v) for v in obj)
        return False

    assert not has_bytes(payload)

    # Logging: the pipeline logs nothing that contains extracted values or identifiers.
    logged = "\n".join(r.getMessage() for r in caplog.records if r.name.startswith("ai_modules"))
    for needle in ("Pooja", "Lodhi", "Vidisha", *SECRETS):
        assert needle not in logged
    for record in caplog.records:
        message = record.getMessage()
        assert "Pooja" not in message
        for secret in SECRETS:
            assert _digits(secret) not in _digits(message)
