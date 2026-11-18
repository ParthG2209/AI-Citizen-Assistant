"""End-to-end tests running REAL PaddleOCR on generated, watermarked synthetic certificates.

Run with: python -m pytest ai_modules/ocr/tests -q   (marked ``slow``; first run downloads ~20 MB
of models into ~/.paddlex/official_models).
"""

import io
import json
import math
import re
from datetime import date

import pytest

from ai_modules.ocr import OcrUnavailableError, ocr_available, process_document
from ai_modules.ocr.synthetic import VARIANTS, devanagari_font, generate, image_to_pdf
from ai_modules.rule_engine.fields import DOCUMENT_TYPES

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not ocr_available(), reason="paddleocr/paddlepaddle not installed"),
]

AS_OF = date(2025, 6, 1)
AADHAAR_LIKE = re.compile(r"(?<!\d)\d{4}\s?\d{4}\s?\d{4}(?!\d)")


@pytest.fixture(scope="module")
def engine():
    from ai_modules.ocr.paddle_engine import get_engine

    try:
        return get_engine()
    except OcrUnavailableError as exc:
        pytest.skip(f"PaddleOCR models unavailable: {exc}")


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("synthetic_documents")
    variants = VARIANTS if devanagari_font() else ("en",)
    docs = generate(out, seed=11, per_type=1, variants=variants, as_of=AS_OF)
    return out, {
        doc.doc_id: json.loads((out / f"{doc.doc_id}.json").read_text(encoding="utf-8")) for doc in docs
    }


def sample_ids():
    variants = VARIANTS if devanagari_font() else ("en",)
    return [
        f"synthetic_{t}_{v}_01" for t in DOCUMENT_TYPES if DOCUMENT_TYPES[t].ocr_supported for v in variants
    ]


def same(actual, expected):
    if isinstance(expected, float) and isinstance(actual, int | float):
        return math.isclose(actual, expected, abs_tol=0.01)
    return actual == expected


def assert_matches_truth(result, truth):
    fields = {f.name: f for f in result.fields}
    for name, expected in truth["expected_fields"].items():
        assert name in fields, f"{name} missing; warnings={result.warnings}"
        assert same(fields[name].value, expected), f"{name}: {fields[name].value!r} != {expected!r}"
        assert not fields[name].validation_errors, fields[name].validation_errors
        assert 0 <= fields[name].confidence <= 1
    assert result.missing_fields == []
    assert result.document_type == truth["document_type"]
    assert result.detected_type == truth["document_type"]


def assert_no_identifiers(result, truth):
    blob = json.dumps([[f.value, f.raw_text] for f in result.fields], ensure_ascii=False, default=str)
    for number in truth["synthetic_identifiers"].values():
        assert number.replace(" ", "") not in blob.replace(" ", "")
    assert not AADHAAR_LIKE.search(blob)


@pytest.mark.parametrize("doc_id", sample_ids())
def test_png_matches_ground_truth(engine, samples, doc_id):
    out, truths = samples
    truth = truths[doc_id]
    result = process_document(
        (out / f"{doc_id}.png").read_bytes(), f"{doc_id}.png", engine=engine, as_of=AS_OF
    )
    assert_matches_truth(result, truth)
    assert_no_identifiers(result, truth)
    assert result.page_count == 1 and result.text_length > 50
    assert result.classification_confidence >= 0.5


@pytest.mark.parametrize("doc_id", sample_ids())
def test_digital_pdf_cross_checked_with_text_layer(engine, samples, doc_id):
    out, truths = samples
    truth = truths[doc_id]
    result = process_document((out / f"{doc_id}.pdf").read_bytes(), "upload.pdf", engine=engine, as_of=AS_OF)
    assert_matches_truth(result, truth)
    assert_no_identifiers(result, truth)
    assert result.engine.endswith("+ pdf-text-layer")
    assert not any("disagree" in w for w in result.warnings)


def test_scanned_pdf_without_text_layer(engine, samples):
    out, truths = samples
    doc_id = "synthetic_income_certificate_en_01"
    scanned = image_to_pdf((out / f"{doc_id}.png").read_bytes())
    result = process_document(scanned, "scan.pdf", engine=engine, as_of=AS_OF)
    assert_matches_truth(result, truths[doc_id])
    assert "pdf-text-layer" not in result.engine


def test_samagra_id_reduced_to_boolean(engine, samples):
    out, truths = samples
    doc_id = "synthetic_samagra_domicile_en_01"
    result = process_document((out / f"{doc_id}.png").read_bytes(), "s.png", engine=engine, as_of=AS_OF)
    samagra = next(f for f in result.fields if f.name == "has_samagra_id")
    assert samagra.value is True
    assert truths[doc_id]["synthetic_identifiers"]["samagra_member_id"] not in samagra.raw_text


def test_declared_type_mismatch_warns(engine, samples):
    out, _ = samples
    data = (out / "synthetic_income_certificate_en_01.png").read_bytes()
    result = process_document(data, "x.png", declared_type="caste_certificate", engine=engine, as_of=AS_OF)
    assert result.document_type == "caste_certificate"
    assert result.detected_type == "income_certificate"
    assert any("looks like 'income_certificate'" in w for w in result.warnings)
    assert "social_category" in result.missing_fields


def test_blank_image_returns_missing_fields(engine):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (1240, 1754), "white").save(buf, format="PNG")
    result = process_document(buf.getvalue(), "blank.png", declared_type="income_certificate", engine=engine)
    assert result.fields == []
    assert result.missing_fields == list(DOCUMENT_TYPES["income_certificate"].provides_fields)
    assert result.text_length == 0
    assert any("No text was recognised" in w for w in result.warnings)

    undeclared = process_document(buf.getvalue(), "blank.png", engine=engine)
    assert undeclared.document_type is None and undeclared.fields == [] and undeclared.missing_fields == []


# (downscale factor, gaussian blur radius, noise sigma, JPEG quality, must_degrade)
DEGRADATIONS = [
    (2.0, 1.0, 10, 50, False),
    (2.0, 1.3, 15, 40, False),
    (2.5, 1.0, 12, 40, False),
    (3.0, 2.2, 45, 30, True),
]


@pytest.mark.parametrize(
    "doc_id", ["synthetic_income_certificate_en_01", "synthetic_class12_marksheet_en_01"]
)
@pytest.mark.parametrize(("scale", "blur", "noise", "quality", "must_degrade"), DEGRADATIONS)
def test_degraded_scan_never_yields_confident_wrong_values(
    engine, samples, doc_id, scale, blur, noise, quality, must_degrade
):
    """Blurred, noisy, low-resolution scans: every value is correct, flagged for review, or missing."""
    import numpy as np
    from PIL import Image, ImageFilter

    out, truths = samples
    with Image.open(out / f"{doc_id}.png") as img:
        size = (int(img.width / scale), int(img.height / scale))
        small = img.convert("L").resize(size).filter(ImageFilter.GaussianBlur(blur))
    rng = np.random.default_rng(0)
    noisy = np.clip(np.asarray(small, dtype=np.float32) + rng.normal(0, noise, (size[1], size[0])), 0, 255)
    buf = io.BytesIO()
    Image.fromarray(noisy.astype(np.uint8)).save(buf, format="JPEG", quality=quality)

    truth = truths[doc_id]
    result = process_document(buf.getvalue(), "bad.jpg", declared_type=truth["document_type"], engine=engine)
    expected = truth["expected_fields"]
    for field in result.fields:
        if not same(field.value, expected.get(field.name)):
            assert field.needs_review, f"confident wrong value for {field.name}: {field.value!r}"
    if must_degrade:
        assert result.missing_fields or any(f.needs_review for f in result.fields)
