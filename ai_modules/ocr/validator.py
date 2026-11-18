"""Validate extracted values and turn OCR + extraction confidence into a reviewable field."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from ai_modules.ocr.field_extractor import FieldCandidate
from ai_modules.rule_engine.fields import coerce_value

DEFAULT_REVIEW_THRESHOLD = 0.80
MAX_PLAUSIBLE_INCOME_INR = 100_000_000  # INR 10 crore: anything above is almost surely an OCR error
# A non-zero annual income below INR 1,000 is almost surely a misread separator ("4.16" for 4,16,000).
MIN_PLAUSIBLE_NONZERO_INCOME_INR = 1_000


def review_threshold() -> float:
    """``ACA_OCR_REVIEW_THRESHOLD`` (default 0.80): fields below it are flagged ``needs_review``."""
    value = float(os.getenv("ACA_OCR_REVIEW_THRESHOLD", str(DEFAULT_REVIEW_THRESHOLD)))
    if not 0 <= value <= 1:
        raise ValueError("ACA_OCR_REVIEW_THRESHOLD must be between 0 and 1")
    return value


@dataclass(slots=True)
class ExtractedField:
    """A profile value read from a document. Never trusted as ``verified``.

    ``value`` is ``None`` when the text was found but failed validation; ``raw_text`` then
    shows what was read so the citizen can correct it.
    """

    name: str
    value: Any
    raw_text: str
    confidence: float
    needs_review: bool
    validation_errors: list[str] = field(default_factory=list)


def plausibility_errors(name: str, value: Any, as_of: date) -> list[str]:
    """Domain checks beyond the type/range checks of ``coerce_value``."""
    errors: list[str] = []
    if name == "annual_family_income" and value > MAX_PLAUSIBLE_INCOME_INR:
        errors.append(f"Annual income {value:,.0f} is implausibly high; check the amount")
    elif name == "annual_family_income" and 0 < value < MIN_PLAUSIBLE_NONZERO_INCOME_INR:
        errors.append(f"Annual income {value:,.2f} is implausibly low; check the amount")
    elif name == "class12_passing_year" and value > as_of.year:
        errors.append(f"Passing year {value} is in the future")
    elif name == "age" and not 1 <= value <= 120:
        errors.append(f"Age {value} is outside the plausible range 1-120")
    elif name == "full_name" and not 2 <= len(value) <= 100:
        errors.append("Name length is implausible")
    return errors


def combine_confidence(ocr_confidence: float, extraction_confidence: float) -> float:
    """Product of OCR line confidence and extraction (label/normaliser) confidence, in [0, 1]."""
    return round(max(0.0, min(1.0, ocr_confidence * extraction_confidence)), 4)


def validate(candidate: FieldCandidate, threshold: float, as_of: date | None = None) -> ExtractedField:
    as_of = as_of or date.today()
    errors: list[str] = []
    value: Any = None
    try:
        value = coerce_value(candidate.name, candidate.value)
        errors.extend(plausibility_errors(candidate.name, value, as_of))
    except ValueError as exc:
        errors.append(str(exc))
    if errors:
        value = None
    confidence = combine_confidence(candidate.ocr_confidence, candidate.extraction_confidence)
    # ``notes`` carry soft problems (e.g. OCR and PDF text layer disagree): keep the value, force review.
    return ExtractedField(
        name=candidate.name,
        value=value,
        raw_text=candidate.raw_text,
        confidence=confidence,
        needs_review=bool(errors or candidate.notes) or confidence < threshold,
        validation_errors=[*candidate.notes, *errors],
    )
