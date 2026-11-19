"""Document classification and template-driven field extraction from OCR lines.

Templates live in ``templates/*.yaml`` (one per OCR-supported document type). Each field has
an ordered list of rules; the first rule that yields a normalisable value wins:

* ``labels``  - label proximity: the value follows the label in the same text box, in the next
  box of the same row, or in the box directly below.
* ``pattern`` - regex with a ``(?P<value>...)`` group over the whole page text (sentence-style
  certificates such as "... जिला सीहोर ... वार्षिक आय रु. 1,20,000 ...").
* ``scan``    - run the normaliser over each row (e.g. board names printed in the header).

Aadhaar-like numbers are redacted from every line before anything else happens, and the
Samagra ID number is never returned: only ``has_samagra_id=True`` with a masked snippet.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ai_modules.ocr import normalizer as norm
from ai_modules.ocr.paddle_engine import OcrLine
from ai_modules.rule_engine.fields import DOCUMENT_TYPES, PROFILE_FIELDS

TEMPLATES_DIR = Path(__file__).with_name("templates")

# 12-digit Aadhaar and 16-digit Aadhaar Virtual IDs, optionally grouped 4-4-4(-4) by space, hyphen,
# dot or slash (OCR may add spaces around the separator: "2345 - 6789 - 0123").
_ID_SEP = r"(?:\s?[.\-/]\s?|\s)?"
_AADHAAR_RE = re.compile(rf"(?<!\d)\d{{4}}{_ID_SEP}\d{{4}}{_ID_SEP}\d{{4}}(?:{_ID_SEP}\d{{4}})?(?!\d)")
# Indian mobile numbers: optional +91 / 0 prefix, 10 digits starting 6-9, any grouping.
_PHONE_RE = re.compile(r"(?<![\d+])(?:\+?91[\s\-]?|0)?[6-9](?:[\s\-]?\d){9}(?!\d)")
# Digit runs separated by single spaces/hyphens/dots/slashes (Samagra IDs "1234-5678-9", account numbers).
_LONG_DIGITS_RE = re.compile(r"\d(?:\s?[\-./]?\s?\d){6,}")
# Dates stay readable in snippets; they are not identifiers.
_DATE_SHAPE_RE = re.compile(r"\d{1,2}\s?[./-]\s?\d{1,2}\s?[./-]\s?\d{4}|\d{4}-\d{1,2}-\d{1,2}")
_SEPARATORS = " \t:：-–—|/.,;=)"


def redact_identifiers(text: str) -> str:
    """Replace Aadhaar-like numbers and mobile numbers with ``[REDACTED]``."""
    return _PHONE_RE.sub("[REDACTED]", _AADHAAR_RE.sub("[REDACTED]", text))


def mask_long_numbers(text: str) -> str:
    """Mask digit runs of 8+ digits (ID numbers) in snippets returned to callers; dates are kept."""

    def _mask(m: re.Match[str]) -> str:
        run = m.group(0)
        digits = re.sub(r"\D", "", run)
        if len(digits) < 8 or _DATE_SHAPE_RE.fullmatch(run):
            return run
        return "*" * len(run)

    return _LONG_DIGITS_RE.sub(_mask, text)


# --------------------------------------------------------------------------- templates
@dataclass(frozen=True, slots=True)
class Keyword:
    regex: re.Pattern[str]
    weight: float


@dataclass(frozen=True, slots=True)
class FieldRule:
    normalizer: str
    labels: tuple[re.Pattern[str], ...] = ()
    exclude: re.Pattern[str] | None = None
    pattern: re.Pattern[str] | None = None
    scan: bool = False
    unique: bool = False
    confidence: float = 1.0  # extraction confidence ceiling for this rule


@dataclass(frozen=True, slots=True)
class Template:
    document_type: str
    keywords: tuple[Keyword, ...]
    min_score: float
    confident_score: float
    fields: dict[str, tuple[FieldRule, ...]]

    @property
    def provides_fields(self) -> tuple[str, ...]:
        return DOCUMENT_TYPES[self.document_type].provides_fields

    def all_labels(self) -> list[re.Pattern[str]]:
        return [label for rules in self.fields.values() for rule in rules for label in rule.labels]


def _compile(pattern: str) -> re.Pattern[str]:
    # OCR text is NFC-normalised, so patterns must be too (nukta letters such as ड़ decompose).
    return re.compile(unicodedata.normalize("NFC", pattern), re.IGNORECASE)


def _parse_template(raw: dict[str, Any], path: Path) -> Template:
    doc_type = raw["document_type"]
    if doc_type not in DOCUMENT_TYPES or not DOCUMENT_TYPES[doc_type].ocr_supported:
        raise ValueError(f"{path.name}: '{doc_type}' is not an OCR-supported document type")
    fields: dict[str, tuple[FieldRule, ...]] = {}
    for name, spec in raw["fields"].items():
        if name not in PROFILE_FIELDS:
            raise ValueError(f"{path.name}: unknown profile field '{name}'")
        rules = []
        for rule in spec["rules"]:
            if rule["normalizer"] not in NORMALIZERS:
                raise ValueError(
                    f"{path.name}: field '{name}' uses unknown normalizer '{rule['normalizer']}'"
                )
            rules.append(
                FieldRule(
                    normalizer=rule["normalizer"],
                    labels=tuple(_compile(p) for p in rule.get("labels", [])),
                    exclude=_compile(rule["exclude"]) if rule.get("exclude") else None,
                    pattern=_compile(rule["pattern"]) if rule.get("pattern") else None,
                    scan=bool(rule.get("scan", False)),
                    unique=bool(rule.get("unique", False)),
                    confidence=float(rule.get("confidence", 1.0)),
                )
            )
        fields[name] = tuple(rules)
    cls = raw["classification"]
    return Template(
        document_type=doc_type,
        keywords=tuple(Keyword(_compile(k["pattern"]), float(k["weight"])) for k in cls["keywords"]),
        min_score=float(cls["min_score"]),
        confident_score=float(cls["confident_score"]),
        fields=fields,
    )


@lru_cache(maxsize=1)
def load_templates() -> dict[str, Template]:
    templates: dict[str, Template] = {}
    for path in sorted(TEMPLATES_DIR.glob("*.yaml")):
        with path.open(encoding="utf-8") as fh:
            template = _parse_template(yaml.safe_load(fh), path)
        templates[template.document_type] = template
    return templates


# --------------------------------------------------------------------------- normalisers
# A normaliser maps a text snippet to (value, quality in [0, 1]) or None.
NormalizerFn = Callable[[str, date], "tuple[Any, float] | None"]


def _wrap(fn: Callable[[str], Any]) -> NormalizerFn:
    def run(text: str, _as_of: date) -> tuple[Any, float] | None:
        value = fn(text)
        return None if value is None else (value, 1.0)

    return run


def _district(text: str, _as_of: date) -> tuple[Any, float] | None:
    match = norm.match_district(text)
    return None if match is None else (match.name, match.score)


def _percentage(text: str, _as_of: date) -> tuple[Any, float] | None:
    text = norm.normalize_text(text)
    if "%" not in text and "प्रतिशत" not in text and "percent" not in text.lower():
        ratio = norm.parse_marks_ratio(text)
        if ratio is not None:
            pct = norm.percentage_from_marks(*ratio)
            return None if pct is None else (pct, 1.0)
    value = norm.parse_percentage(text)
    return None if value is None else (value, 1.0)


def _marks_ratio(text: str, _as_of: date) -> tuple[Any, float] | None:
    ratio = norm.parse_marks_ratio(text)
    pct = norm.percentage_from_marks(*ratio) if ratio else None
    return None if pct is None else (pct, 1.0)


def _age(text: str, as_of: date) -> tuple[Any, float] | None:
    value = norm.parse_age(text, as_of)
    return None if value is None else (value, 1.0)


def _id_present(text: str, _as_of: date) -> tuple[Any, float] | None:
    """True when an 8-9 digit ID number follows the label; the number itself is discarded."""
    digits = re.sub(r"\D", "", norm.normalize_text(text))
    return (True, 1.0) if 8 <= len(digits) <= 9 else None


NORMALIZERS: dict[str, NormalizerFn] = {
    "name": _wrap(norm.clean_name),
    "amount_inr": _wrap(norm.parse_amount_inr),
    "district": _district,
    "category": _wrap(norm.parse_category),
    "gender": _wrap(norm.parse_gender),
    "age": _age,
    "percentage": _percentage,
    "marks_ratio": _marks_ratio,
    "year": _wrap(norm.parse_year),
    "board": _wrap(norm.parse_board),
    "state": _wrap(norm.parse_state),
    "residence": _wrap(norm.parse_residence),
    "id_present": _id_present,
}


# --------------------------------------------------------------------------- classification
@dataclass(frozen=True, slots=True)
class Classification:
    document_type: str | None
    confidence: float
    scores: dict[str, float]


def classify(text: str, templates: dict[str, Template] | None = None) -> Classification:
    """Keyword scoring per template. Confidence = margin over the runner-up x saturation."""
    templates = templates or load_templates()
    text = norm.normalize_text(text)
    scores = {
        doc_type: sum(kw.weight for kw in t.keywords if kw.regex.search(text))
        for doc_type, t in templates.items()
    }
    best_type = max(scores, key=lambda k: scores[k])
    best = scores[best_type]
    if best < templates[best_type].min_score:
        return Classification(None, 0.0, scores)
    runner_up = max((s for k, s in scores.items() if k != best_type), default=0.0)
    margin = (best - runner_up) / best
    saturation = min(1.0, best / templates[best_type].confident_score)
    return Classification(best_type, round(margin * saturation, 3), scores)


# --------------------------------------------------------------------------- extraction
@dataclass(slots=True)
class FieldCandidate:
    name: str
    value: Any
    raw_text: str
    ocr_confidence: float
    extraction_confidence: float
    notes: list[str] = field(default_factory=list)


def prepare_lines(lines: Iterable[OcrLine]) -> list[OcrLine]:
    """Normalise text and redact Aadhaar-like numbers before any further processing."""
    prepared = []
    for line in lines:
        text = redact_identifiers(norm.normalize_text(line.text))
        if text:
            prepared.append(OcrLine(text, line.confidence, line.bbox, line.page, line.source, line.lang))
    return prepared


def group_rows(lines: list[OcrLine]) -> list[list[OcrLine]]:
    """Group lines whose vertical centres lie within half a line height (same page)."""
    rows: list[list[OcrLine]] = []
    for line in sorted(lines, key=lambda ln: (ln.page, (ln.bbox[1] + ln.bbox[3]) / 2, ln.bbox[0])):
        centre = (line.bbox[1] + line.bbox[3]) / 2
        if rows and rows[-1][0].page == line.page:
            ref = rows[-1][0]
            if abs(centre - (ref.bbox[1] + ref.bbox[3]) / 2) <= max(ref.bbox[3] - ref.bbox[1], 1) / 2:
                rows[-1].append(line)
                continue
        rows.append([line])
    return [sorted(row, key=lambda ln: ln.bbox[0]) for row in rows]


def full_text(lines: list[OcrLine]) -> str:
    return "\n".join(" ".join(ln.text for ln in row) for row in group_rows(lines))


class _Extractor:
    def __init__(self, template: Template, lines: list[OcrLine], as_of: date) -> None:
        self.template = template
        self.rows = group_rows(lines)
        self.as_of = as_of
        self.all_labels = template.all_labels()
        # Joined page text plus the character span of every line, for regex-pattern rules.
        parts: list[str] = []
        self.spans: list[tuple[int, int, OcrLine]] = []
        pos = 0
        for r, row in enumerate(self.rows):
            for i, line in enumerate(row):
                if i:
                    parts.append(" ")
                    pos += 1
                parts.append(line.text)
                self.spans.append((pos, pos + len(line.text), line))
                pos += len(line.text)
            if r < len(self.rows) - 1:
                parts.append("\n")
                pos += 1
        self.text = "".join(parts)

    def extract(self, name: str) -> FieldCandidate | None:
        for rule in self.template.fields[name]:
            if rule.labels:
                found = self._by_label(name, rule)
            elif rule.pattern is not None:
                found = self._by_pattern(name, rule)
            else:
                found = self._by_scan(name, rule)
            if found is not None:
                return found
        return None

    def _normalize(self, rule: FieldRule, text: str) -> tuple[Any, float] | None:
        return NORMALIZERS[rule.normalizer](text, self.as_of)

    def _strip_value(self, rule: FieldRule, text: str) -> str:
        """Drop leading separators and repeated labels, then cut at the next field's label."""
        text = text.lstrip(_SEPARATORS)
        while True:
            for label in rule.labels:
                m = label.match(text)
                if m and m.end() > 0:
                    text = text[m.end() :].lstrip(_SEPARATORS)
                    break
            else:
                break
        cut = len(text)
        for label in self.all_labels:
            if label in rule.labels:
                continue
            m = label.search(text)
            if m:
                cut = min(cut, m.start())
        return text[:cut].strip(_SEPARATORS + " ")

    def _by_label(self, name: str, rule: FieldRule) -> FieldCandidate | None:
        for r, row in enumerate(self.rows):
            for i, line in enumerate(row):
                matches = [m for label in rule.labels if (m := label.search(line.text))]
                if not matches:
                    continue
                m = max(matches, key=lambda x: x.end())
                if rule.exclude is not None and rule.exclude.search(line.text[: m.end()]):
                    continue
                for value_text, value_lines, quality in self._value_candidates(
                    line.text[m.end() :], row, i, r
                ):
                    value_text = self._strip_value(rule, value_text)
                    if not value_text:
                        continue
                    result = self._normalize(rule, value_text)
                    if result is None:
                        continue
                    value, norm_quality = result
                    used = [line] + [
                        ln
                        for ln in value_lines
                        if ln.text.strip(_SEPARATORS) in value_text or value_text in ln.text
                    ]
                    raw = " ".join(dict.fromkeys(ln.text for ln in used))
                    return FieldCandidate(
                        name,
                        value,
                        raw,
                        min(ln.confidence for ln in used),
                        rule.confidence * quality * norm_quality,
                    )
        return None

    def _value_candidates(
        self, remainder: str, row: list[OcrLine], i: int, r: int
    ) -> Iterable[tuple[str, list[OcrLine], float]]:
        """Where a label's value may be: after the label in the same row, or in the row below.

        Yields (text, contributing lines, proximity quality). Text running into the next field's
        label is cut later by ``_strip_value``.
        """
        line = row[i]
        rest = [ln for ln in row[i + 1 :] if ln.text.strip(_SEPARATORS + " ")]
        has_remainder = bool(remainder.strip(_SEPARATORS + " "))
        if has_remainder or rest:
            text = " ".join([remainder, *(ln.text for ln in rest)])
            yield text, rest, 1.0 if has_remainder else 0.95
        if has_remainder and rest:
            yield remainder, [], 0.95
        if r + 1 < len(self.rows):
            width = line.bbox[2] - line.bbox[0]
            below = [
                ln
                for ln in self.rows[r + 1]
                if ln.bbox[2] > line.bbox[0] and ln.bbox[0] < line.bbox[2] + width
            ]
            if below:
                yield " ".join(ln.text for ln in below), below, 0.85

    def _lines_in_span(self, start: int, end: int) -> list[OcrLine]:
        return [ln for s, e, ln in self.spans if s < end and e > start]

    def _by_pattern(self, name: str, rule: FieldRule) -> FieldCandidate | None:
        assert rule.pattern is not None
        for m in rule.pattern.finditer(self.text):
            result = self._normalize(rule, m.group("value"))
            if result is None:
                continue
            lines = self._lines_in_span(m.start(), m.end()) or self._lines_in_span(
                m.start("value"), m.end("value")
            )
            value, quality = result
            return FieldCandidate(
                name,
                value,
                re.sub(r"\s+", " ", m.group(0)).strip(),
                min(ln.confidence for ln in lines),
                rule.confidence * quality,
            )
        return None

    def _by_scan(self, name: str, rule: FieldRule) -> FieldCandidate | None:
        hits: list[FieldCandidate] = []
        for row in self.rows:
            row_text = " ".join(ln.text for ln in row)
            result = self._normalize(rule, row_text)
            if result is None:
                continue
            value, quality = result
            hits.append(
                FieldCandidate(
                    name, value, row_text, min(ln.confidence for ln in row), rule.confidence * quality
                )
            )
            if not rule.unique:
                break
        if not hits:
            return None
        if rule.unique and len({hit.value for hit in hits}) > 1:
            return None
        return hits[0]


def extract_fields(
    template: Template, lines: list[OcrLine], as_of: date | None = None
) -> dict[str, FieldCandidate]:
    """Extract every template field that can be found; values are normalised but not yet validated."""
    extractor = _Extractor(template, lines, as_of or date.today())
    found: dict[str, FieldCandidate] = {}
    for name in template.fields:
        candidate = extractor.extract(name)
        if candidate is not None:
            candidate.raw_text = mask_long_numbers(candidate.raw_text)
            found[name] = candidate
    return found
