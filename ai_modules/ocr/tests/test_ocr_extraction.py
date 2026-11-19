"""Fast tests of classification, extraction and validation on hand-built OCR lines (no models)."""

from datetime import date

import pytest

from ai_modules.ocr.field_extractor import (
    FieldCandidate,
    classify,
    extract_fields,
    load_templates,
    mask_long_numbers,
    prepare_lines,
    redact_identifiers,
)
from ai_modules.ocr.paddle_engine import OcrLine, choose_reading
from ai_modules.ocr.validator import combine_confidence, review_threshold, validate
from ai_modules.rule_engine.fields import DOCUMENT_TYPES


def page(*rows, confidence=0.98):
    """Build OCR lines: each row is a list of box texts laid out left to right."""
    lines = []
    for r, row in enumerate([row] if isinstance(row, str) else row for row in rows):
        x = 50.0
        for text in row:
            width = 12.0 * len(text)
            lines.append(OcrLine(text, confidence, (x, 40.0 * r, x + width, 40.0 * r + 24), 0))
            x += width + 40
    return prepare_lines(lines)


def values(doc_type, lines, as_of=date(2025, 6, 1)):
    found = extract_fields(load_templates()[doc_type], lines, as_of)
    return {name: c.value for name, c in found.items()}


def test_templates_cover_ocr_supported_documents():
    templates = load_templates()
    supported = {d.id for d in DOCUMENT_TYPES.values() if d.ocr_supported}
    assert set(templates) == supported
    for doc_type, template in templates.items():
        assert set(DOCUMENT_TYPES[doc_type].provides_fields) <= set(template.fields)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("मध्यप्रदेश शासन आय प्रमाण-पत्र वार्षिक आय रु. 1,20,000", "income_certificate"),
        ("INCOME CERTIFICATE Annual Family Income Rs. 50,000 Tehsildar", "income_certificate"),
        ("CASTE CERTIFICATE Category: Scheduled Tribe Sub-Divisional Officer", "caste_certificate"),
        ("जाति प्रमाण-पत्र अन्य पिछड़ा वर्ग", "caste_certificate"),
        ("Samagra Member ID 123456789 Domicile Certificate", "samagra_domicile"),
        ("मध्य प्रदेश का मूल निवासी प्रमाण पत्र", "samagra_domicile"),
        ("HIGHER SECONDARY STATEMENT OF MARKS Grand Total 392/500 Roll No", "class12_marksheet"),
        ("हायर सेकेण्डरी परीक्षा अंक सूची कुल प्राप्तांक", "class12_marksheet"),
    ],
)
def test_classify(text, expected):
    result = classify(text)
    assert result.document_type == expected
    assert 0.5 <= result.confidence <= 1


def test_classify_unknown_text():
    result = classify("Electricity bill for the month of March, units consumed 120")
    assert result.document_type is None and result.confidence == 0


def test_income_certificate_english_layout():
    lines = page(
        ["INCOME CERTIFICATE"],
        ["Name of Applicant:", "Priya Verma"],
        ["Father's Name:", "Mohan Verma"],
        ["District:", "Sehore"],
        ["Annual Family Income:", "Rs. 1,80,000/-"],
    )
    assert values("income_certificate", lines) == {
        "full_name": "Priya Verma",
        "annual_family_income": 180000,
        "district": "Sehore",
    }


def test_income_certificate_bilingual_same_box_and_sentence():
    lines = page(
        ["आय प्रमाण-पत्र"],
        ["आवेदक का नाम / Name of Applicant: Ramesh Yadav"],
        ["पिता का नाम / Father's Name: Mohan Yadav"],
        ["जिला / District: सीहोर"],
        ["वार्षिक आय / Annual Income : रु. ९६,०००/-"],
    )
    assert values("income_certificate", lines) == {
        "full_name": "Ramesh Yadav",
        "annual_family_income": 96000,
        "district": "Sehore",
    }
    sentence = page(
        ["प्रमाणित किया जाता है कि श्री रमेश यादव पिता श्री मोहन यादव निवासी ग्राम रामपुरा"],
        ["तहसील आष्टा जिला सीहोर की समस्त स्त्रोतों से वार्षिक आय रु. 96,000 है।"],
    )
    assert values("income_certificate", sentence) == {
        "full_name": "रमेश यादव",
        "annual_family_income": 96000,
        "district": "Sehore",
    }


def test_value_below_label():
    lines = page(["Name of Applicant"], ["Kavita Sahu"], ["Annual Income"], ["Rs. 72,000"])
    assert values("income_certificate", lines) == {"full_name": "Kavita Sahu", "annual_family_income": 72000}


def test_caste_category_label_and_sentence():
    lines = page(["वर्ग / Category:", "अन्य पिछड़ा वर्ग"], ["District:", "Indore"])
    assert values("caste_certificate", lines) == {"social_category": "obc", "district": "Indore"}
    sentence = page(
        ["This is to certify that the applicant belongs to a caste recognised as Scheduled Tribe"]
    )
    assert values("caste_certificate", sentence) == {"social_category": "st"}


def test_caste_scan_requires_a_unique_category():
    ambiguous = page(["Reserved for Scheduled Caste"], ["and Scheduled Tribe applicants"])
    assert "social_category" not in values("caste_certificate", ambiguous)


def test_samagra_fields_and_identifier_privacy():
    lines = page(
        ["Samagra Member ID:", "123456789"],
        ["Name of Member:", "Pooja Lodhi"],
        ["Gender:", "महिला"],
        ["जन्म तिथि / Date of Birth:", "12/05/2007"],
        ["Residence:", "Rural"],
        ["District:", "Vidisha"],
        ["State:", "Madhya Pradesh"],
        ["Aadhaar No.:", "0234 5678 9012"],
    )
    found = extract_fields(load_templates()["samagra_domicile"], lines, date(2025, 6, 1))
    assert {k: c.value for k, c in found.items()} == {
        "full_name": "Pooja Lodhi",
        "gender": "female",
        "age": 18,
        "district": "Vidisha",
        "domicile_state": "Madhya Pradesh",
        "has_samagra_id": True,
        "residence_type": "rural",
    }
    assert "123456789" not in found["has_samagra_id"].raw_text
    assert all("0234" not in line.text for line in lines)


def test_marksheet_percentage_direct_and_computed():
    direct = page(
        ["Board of Secondary Education, Madhya Pradesh, Bhopal"],
        ["HIGHER SECONDARY STATEMENT OF MARKS"],
        ["Name of Candidate:", "Arjun Meena"],
        ["Year of Examination:", "2024"],
        ["Grand Total:", "392 / 500"],
        ["Percentage:", "78.40%"],
    )
    assert values("class12_marksheet", direct) == {
        "full_name": "Arjun Meena",
        "class12_percentage": 78.4,
        "class12_board": "mpbse",
        "class12_passing_year": 2024,
    }
    computed = page(
        ["Central Board of Secondary Education"],
        ["SENIOR SCHOOL CERTIFICATE EXAMINATION MARCH 2023"],
        ["कुल प्राप्तांक / Grand Total:", "431 / 500"],
    )
    assert values("class12_marksheet", computed) == {
        "class12_percentage": 86.2,
        "class12_board": "cbse",
        "class12_passing_year": 2023,
    }


def test_redaction_helpers():
    assert redact_identifiers("Aadhaar 2345 6789 0123 issued") == "Aadhaar [REDACTED] issued"
    assert redact_identifiers("VID 2345-6789-0123-4567") == "VID [REDACTED]"
    assert redact_identifiers("UID 2345.6789.0123") == "UID [REDACTED]"
    assert redact_identifiers("Date 12.05.2007") == "Date 12.05.2007"
    assert redact_identifiers("Income Rs. 1,20,000") == "Income Rs. 1,20,000"
    assert mask_long_numbers("Member ID 123456789") == "Member ID *********"
    assert mask_long_numbers("Roll 1234567") == "Roll 1234567"


def candidate(name, value, ocr=0.99, extraction=1.0):
    return FieldCandidate(name, value, f"raw {value}", ocr, extraction)


def test_validate_confidence_and_threshold():
    ok = validate(candidate("annual_family_income", 120000.0), threshold=0.8)
    assert ok.value == 120000 and not ok.needs_review and ok.confidence == 0.99
    low = validate(candidate("annual_family_income", 120000.0, ocr=0.9, extraction=0.85), threshold=0.8)
    assert low.value == 120000 and low.needs_review and low.confidence == combine_confidence(0.9, 0.85)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("class12_percentage", 104.0),
        ("annual_family_income", -5.0),
        ("annual_family_income", 5_000_000_000.0),
        ("age", 0),
        ("annual_family_income", 4.16),
        ("age", 130),
        ("class12_passing_year", 2031),
        ("social_category", "nomadic"),
    ],
)
def test_validate_rejects_implausible_values(name, value):
    result = validate(candidate(name, value), threshold=0.8, as_of=date(2025, 6, 1))
    assert result.value is None and result.needs_review and result.validation_errors


def test_validate_keeps_value_but_flags_soft_notes():
    c = candidate("district", "Sehore")
    c.notes.append("OCR and the PDF text layer disagree on this value")
    result = validate(c, threshold=0.8)
    assert result.value == "Sehore" and result.needs_review and result.validation_errors


def test_review_threshold_from_env(monkeypatch):
    monkeypatch.delenv("ACA_OCR_REVIEW_THRESHOLD", raising=False)
    assert review_threshold() == 0.8
    monkeypatch.setenv("ACA_OCR_REVIEW_THRESHOLD", "0.95")
    assert review_threshold() == 0.95
    assert validate(candidate("district", "Sehore", ocr=0.93), threshold=review_threshold()).needs_review
    monkeypatch.setenv("ACA_OCR_REVIEW_THRESHOLD", "1.5")
    with pytest.raises(ValueError):
        review_threshold()


def test_choose_reading_prefers_confident_devanagari():
    # The Latin model misreads "जिला" as gibberish with high confidence.
    assert choose_reading([("fuell / District:", 0.96, "en"), ("जिला / District:", 0.90, "hi")])[2] == "hi"
    assert choose_reading([("Barwani", 0.99, "en"), ("Barwani", 0.97, "hi")])[2] == "en"
    assert choose_reading([("Barwani", 0.99, "en"), ("बरवा", 0.4, "hi")])[2] == "en"
