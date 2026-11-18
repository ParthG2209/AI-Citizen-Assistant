"""End-to-end document processing: load -> OCR -> classify -> extract -> validate."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

from ai_modules.ocr.documents import InputLimits, load_document
from ai_modules.ocr.errors import UnsupportedDocumentTypeError
from ai_modules.ocr.field_extractor import (
    FieldCandidate,
    classify,
    extract_fields,
    full_text,
    load_templates,
    prepare_lines,
)
from ai_modules.ocr.paddle_engine import PaddleEngine, get_engine
from ai_modules.ocr.validator import ExtractedField, review_threshold, validate
from ai_modules.rule_engine.fields import DOCUMENT_TYPES

# Below this classification confidence a detected type is reported as uncertain.
UNCERTAIN_CLASSIFICATION = 0.5


@dataclass(slots=True)
class OcrDocumentResult:
    document_type: str | None  # type used for extraction (declared type wins over detected type)
    classification_confidence: float  # confidence of ``detected_type`` from the document text
    page_count: int
    engine: str
    text_length: int  # characters recognised (the OCR text itself is not returned or stored)
    fields: list[ExtractedField]
    missing_fields: list[str]
    warnings: list[str]
    detected_type: str | None = None
    elapsed_ms: int = 0
    # Declared type contradicts confidently detected content: every field is forced to review and
    # ``warnings`` carries ``declared_type_mismatch:<declared>!=<detected>``.
    type_mismatch: bool = False


def process_document(
    data: bytes,
    filename: str,
    declared_type: str | None = None,
    *,
    engine: PaddleEngine | None = None,
    as_of: date | None = None,
) -> OcrDocumentResult:
    """Read a certificate image/PDF and extract canonical profile fields.

    Raises ``UnsupportedFileError`` (incl. ``UnsupportedDocumentTypeError``),
    ``CorruptDocumentError``, ``FileTooLargeError`` or ``OcrUnavailableError``.
    """
    start = time.perf_counter()
    templates = load_templates()
    if declared_type is not None and declared_type not in templates:
        supported = ", ".join(sorted(templates))
        raise UnsupportedDocumentTypeError(
            f"Document type '{declared_type}' is not supported for OCR; supported: {supported}"
        )
    as_of = as_of or date.today()
    threshold = review_threshold()

    # Validate and decode the upload before paying for model loading.
    doc = load_document(data, filename, InputLimits.from_env())
    engine = engine or get_engine()
    warnings = list(doc.warnings)

    raw_lines = []
    for index, page in enumerate(doc.pages):
        raw_lines.extend(engine.recognize(page, page=index))
    ocr_lines = prepare_lines(raw_lines)
    layer_lines = prepare_lines(doc.text_layer)
    ocr_text = full_text(ocr_lines)
    if not ocr_lines:
        warnings.append("No text was recognised; upload a sharper, well-lit scan of the whole document")

    classification = classify("\n".join([ocr_text, full_text(layer_lines)]), templates)
    detected = classification.document_type
    type_mismatch = False
    if declared_type is not None:
        doc_type: str | None = declared_type
        if detected is not None and detected != declared_type:
            warnings.append(
                f"Declared type '{declared_type}' but the content looks like '{detected}' "
                f"(confidence {classification.confidence:.2f}); fields were extracted as '{declared_type}'"
            )
            if classification.confidence >= UNCERTAIN_CLASSIFICATION:
                type_mismatch = True
                warnings.append(f"declared_type_mismatch:{declared_type}!={detected}")
        elif detected is None and (ocr_lines or layer_lines):
            warnings.append(f"Could not confirm from the text that this is a '{declared_type}'")
    else:
        doc_type = detected
        if detected is None and (ocr_lines or layer_lines):
            warnings.append("Document type could not be identified; specify the document type and retry")
    if detected is not None and classification.confidence < UNCERTAIN_CLASSIFICATION:
        warnings.append(f"Document type detection is uncertain (confidence {classification.confidence:.2f})")

    fields: list[ExtractedField] = []
    missing: list[str] = []
    if doc_type is not None:
        template = templates[doc_type]
        candidates = extract_fields(template, ocr_lines, as_of)
        if layer_lines:
            candidates = _cross_check(candidates, extract_fields(template, layer_lines, as_of), warnings)
        fields = [validate(candidate, threshold, as_of) for candidate in candidates.values()]
        if type_mismatch:  # values may come from the wrong kind of document: never auto-apply them
            for extracted in fields:
                extracted.needs_review = True
        missing = [name for name in DOCUMENT_TYPES[doc_type].provides_fields if name not in candidates]

    engine_name = engine.name + (" + pdf-text-layer" if layer_lines else "")
    return OcrDocumentResult(
        document_type=doc_type,
        classification_confidence=classification.confidence,
        page_count=len(doc.pages),
        engine=engine_name,
        text_length=len(ocr_text),
        fields=fields,
        missing_fields=missing,
        warnings=warnings,
        detected_type=detected,
        elapsed_ms=round((time.perf_counter() - start) * 1000),
        type_mismatch=type_mismatch,
    )


def _same_value(a: Any, b: Any) -> bool:
    if isinstance(a, float | int) and isinstance(b, float | int) and not isinstance(a, bool):
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=0.01)
    if isinstance(a, str) and isinstance(b, str):
        return a.casefold() == b.casefold()
    return a == b


def _cross_check(
    ocr: dict[str, FieldCandidate], layer: dict[str, FieldCandidate], warnings: list[str]
) -> dict[str, FieldCandidate]:
    """Compare OCR values with the PDF's embedded text layer.

    Agreement raises the OCR confidence to the text layer's (exact) confidence; disagreement
    keeps the OCR value but forces review; a value present only in the text layer is used but
    flagged, since it is not visible in the rendered page as far as OCR can tell.
    """
    merged = dict(ocr)
    for name, from_layer in layer.items():
        from_ocr = ocr.get(name)
        if from_ocr is None:
            from_layer.notes.append("Read from the PDF text layer only; OCR did not confirm it")
            merged[name] = from_layer
        elif _same_value(from_ocr.value, from_layer.value):
            from_ocr.ocr_confidence = max(from_ocr.ocr_confidence, from_layer.ocr_confidence)
        else:
            from_ocr.notes.append("OCR and the PDF text layer disagree on this value")
            warnings.append(f"'{name}': OCR and the PDF text layer disagree")
    return merged
