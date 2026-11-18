"""Post-generation grounding checks for LLM answers (run after citation validation).

A valid ``[n]`` marker only proves the model *named* a retrieved passage. This module measures
whether the cited passage actually supports the sentence that cites it, and whether every number in
the answer comes from the evidence:

* **Citation support.** For each sentence and each marker in it, the cited passage supports the
  sentence when EITHER
    - lexical coverage >= ``LEXICAL_SUPPORT_MIN`` (0.5): at least half of the sentence's content
      tokens (``retriever.tokenize``: stop-words removed, numbers kept) occur in the passage; OR
    - semantic similarity >= ``SEMANTIC_SUPPORT_MIN`` (0.32): the cosine similarity (multilingual
      embedder, so Hindi answers can cite English passages) between the sentence and the passage,
      or the best-matching sentence of the passage, reaches the retriever's evidence threshold.
  Unsupported markers are removed from the answer.
  Calibration (real index, paraphrase-multilingual-MiniLM-L12-v2, live qwen2.5:7b-instruct answers):
  supported cited sentences scored lexical 0.58-1.0 (English) and cosine 0.32-0.84 (incl. Hindi
  answers on English passages); unrelated text cited against those passages ("The capital of France
  is Paris") scored lexical 0.0 / cosine 0.05-0.12. On-topic paraphrased fabrications ("must submit a
  caste certificate") also reach cosine 0.35-0.6, so this check catches citations of unrelated
  passages, not every subtle fabrication; the number check and the contradiction guard cover the
  highest-risk claims (amounts, ages, percentages, the decision).
* **Uncited statements.** A sentence without a marker is accepted when it matches the fixed engine
  facts (decision + condition results) or a retrieved passage by the same measure; otherwise it is
  reported as unsupported.
* **Numbers.** Every number in the answer (amounts, percentages, ages, years; Devanagari digits and
  Indian digit grouping normalised; list enumerators ignored) must occur in a passage cited by a
  supported sentence, in the engine facts or in the user's own question. When a number comes from a
  retrieved passage the sentence did not cite, and that passage supports the sentence, its marker is
  added (the model saw the passage but cited the wrong one); otherwise the number is reported.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from .retriever import tokenize

if TYPE_CHECKING:
    import numpy as np

    from .retriever import RetrievedPassage

LEXICAL_SUPPORT_MIN = 0.5
SEMANTIC_SUPPORT_MIN = 0.32
MIN_STATEMENT_TOKENS = 3  # shorter fragments ("Aadhaar Number", "This is also explained in") are not claims

_MARKER_RE = re.compile(r"\[(\d{1,3})\]")
_ABBREVIATIONS = ("Rs.", "No.", "Nos.", "Dr.", "Mr.", "Mrs.", "Smt.", "Shri.", "Sh.", "viz.", "e.g.", "i.e.")
_ABBREV_GUARD = "".join(f"(?<!\\b{re.escape(a)})" for a in _ABBREVIATIONS)
# Sentence boundary: line break, or end punctuation (not an abbreviation, not "1." of a list) + space.
_BOUNDARY_RE = re.compile(rf"\n+|{_ABBREV_GUARD}(?<!^\d\.)(?<!\s\d\.)(?<=[.!?।])[ \t]+", re.MULTILINE)
_LIST_ENUM_RE = re.compile(r"^\s*(?:[-*•]|\(?\d{1,2}[.)]|\(?[a-z][.)]|[ivx]+[.)])\s+", re.IGNORECASE)
_NUMBER_RE = re.compile(r"\d+(?:[,.]\d+)*")
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")


class SupportsEmbed(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...


@dataclass(slots=True)
class GroundingReport:
    answer: str  # answer with unsupported markers removed
    cited: list[int]  # markers still present, sorted
    removed_markers: list[int] = field(default_factory=list)  # unsupported markers taken out
    added_markers: list[int] = field(default_factory=list)  # passages cited for an uncited number
    unsupported_numbers: list[str] = field(default_factory=list)
    unsupported_statements: list[str] = field(default_factory=list)
    supported_cited_sentences: int = 0

    @property
    def grounded(self) -> bool:
        return (
            self.supported_cited_sentences > 0
            and not self.unsupported_numbers
            and not self.unsupported_statements
        )


def sentence_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence / list line; marker-only fragments ("[2]") join the previous one."""
    spans: list[tuple[int, int]] = []
    start = 0
    boundaries = [(m.start(), m.end()) for m in _BOUNDARY_RE.finditer(text)] + [(len(text), len(text))]
    for end, next_start in boundaries:
        fragment = text[start:end]
        if fragment.strip():
            lead = len(fragment) - len(fragment.lstrip())
            span = (start + lead, start + len(fragment.rstrip()))
            if spans and not _MARKER_RE.sub("", fragment).strip(" .,;:।"):
                spans[-1] = (spans[-1][0], span[1])
            else:
                spans.append(span)
        start = next_start
    return spans


def split_sentences(text: str) -> list[str]:
    return [text[a:b] for a, b in sentence_spans(text)]


def _canonical_number(raw: str) -> str:
    """'6,000' / '1,20,000' / '6000.00' / '०६' -> '6000' / '120000' / '6000' / '6'."""
    raw = raw.translate(_DEVANAGARI_DIGITS)
    if raw.count(".") == 1 and re.fullmatch(r"[\d,]*\.\d+", raw):
        whole, frac = raw.replace(",", "").split(".")
        frac = frac.rstrip("0")
        whole = whole.lstrip("0") or "0"
        return f"{whole}.{frac}" if frac else whole
    return raw.replace(",", "").replace(".", "").lstrip("0") or "0"


def extract_numbers(text: str) -> set[str]:
    text = text.translate(_DEVANAGARI_DIGITS)
    found: set[str] = set()
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0).rstrip(".,")
        if "." in token and token.count(".") > 1:  # "4.1.2" section numbers: keep each part
            found.update(_canonical_number(p) for p in token.split(".") if p)
            continue
        found.add(_canonical_number(token))
    return found


def _answer_numbers(sentence: str) -> set[str]:
    return extract_numbers(_LIST_ENUM_RE.sub("", _MARKER_RE.sub("", sentence)))


def _tidy(text: str) -> str:
    """Collapse the spaces left behind by removed markers ("Paris [1]." -> "Paris.")."""
    return re.sub(r"[ \t]+([.,;:।])", r"\1", re.sub(r"[ \t]{2,}", " ", text)).strip()


def _remove_markers(sentence: str, markers: set[int]) -> str:
    return _MARKER_RE.sub(lambda m: "" if int(m.group(1)) in markers else m.group(0), sentence)


def _lexical(tokens: set[str], text_tokens: set[str]) -> float:
    return len(tokens & text_tokens) / len(tokens) if tokens else 0.0


class _Evidence:
    """Embeds evidence texts (whole and per sentence) once; scores claims against them."""

    def __init__(self, texts: Sequence[str], embedder: SupportsEmbed | None) -> None:
        self.texts = list(texts)
        self.tokens = [set(tokenize(t)) for t in self.texts]
        self.embedder = embedder
        self._segments: list[list[str]] = [[t, *split_sentences(t)] for t in self.texts]
        self._vectors: list[np.ndarray] | None = None

    def _ensure_vectors(self) -> list[np.ndarray]:
        if self._vectors is None:
            assert self.embedder is not None
            flat = [s for segs in self._segments for s in segs]
            matrix = self.embedder.embed(flat) if flat else None
            vectors, start = [], 0
            for segs in self._segments:
                vectors.append(matrix[start : start + len(segs)] if matrix is not None else None)
                start += len(segs)
            self._vectors = vectors
        return self._vectors

    def supports(self, claim: str, claim_vector: np.ndarray | None, index: int) -> bool:
        if _lexical(set(tokenize(claim)), self.tokens[index]) >= LEXICAL_SUPPORT_MIN:
            return True
        if claim_vector is None or self.embedder is None:
            return False
        segment_vectors = self._ensure_vectors()[index]
        if segment_vectors is None:
            return False
        return float((segment_vectors @ claim_vector).max()) >= SEMANTIC_SUPPORT_MIN

    def supports_any(self, claim: str, claim_vector: np.ndarray | None) -> bool:
        return any(self.supports(claim, claim_vector, i) for i in range(len(self.texts)))


def verify_grounding(
    answer: str,
    passages: Sequence[RetrievedPassage],
    *,
    embedder: SupportsEmbed | None,
    facts: Sequence[str] = (),
    question: str | None = None,
) -> GroundingReport:
    """Check an answer whose markers were already validated against ``passages`` (1-based)."""
    spans = sentence_spans(answer)
    sentences = [answer[a:b] for a, b in spans]
    claims = [_tidy(_MARKER_RE.sub("", s)) for s in sentences]
    claim_vectors: list[np.ndarray | None] = [None] * len(claims)
    if embedder is not None and claims:
        matrix = embedder.embed(claims)
        claim_vectors = [matrix[i] for i in range(len(claims))]

    passage_evidence = _Evidence([p.chunk.text for p in passages], embedder)
    fact_evidence = _Evidence([f for f in facts if f.strip()], embedder)

    report = GroundingReport(answer=answer, cited=[])
    kept_by_sentence: list[list[int]] = []
    for sentence, claim, vector in zip(sentences, claims, claim_vectors, strict=True):
        markers = list(dict.fromkeys(int(n) for n in _MARKER_RE.findall(sentence)))
        kept = [n for n in markers if passage_evidence.supports(claim, vector, n - 1)]
        kept_by_sentence.append(kept)
        report.removed_markers += [n for n in markers if n not in kept and n not in report.removed_markers]
        if kept:
            report.supported_cited_sentences += 1
        elif len(tokenize(claim)) >= MIN_STATEMENT_TOKENS and not (
            fact_evidence.supports_any(claim, vector) or passage_evidence.supports_any(claim, vector)
        ):
            # Uncited (or no longer cited) statement matching neither the engine facts nor a passage.
            report.unsupported_statements.append(claim)
    report.removed_markers.sort()

    passage_numbers = [extract_numbers(p.chunk.text) for p in passages]
    allowed: set[str] = set().union(*(passage_numbers[n - 1] for kept in kept_by_sentence for n in kept))
    for text in (*facts, question or ""):
        allowed |= extract_numbers(text)

    pieces: list[str] = []
    position = 0
    for (a, b), sentence, claim, vector, kept in zip(
        spans, sentences, claims, claim_vectors, kept_by_sentence, strict=True
    ):
        markers = {int(n) for n in _MARKER_RE.findall(sentence)}
        rewritten = _remove_markers(sentence, markers - set(kept))
        missing = sorted(_answer_numbers(sentence) - allowed)
        if missing:
            # The number may come from a retrieved passage the model forgot to cite: cite it if that
            # passage contains every such number and supports the sentence; otherwise flag the numbers.
            source = next(
                (
                    i + 1
                    for i in range(len(passages))
                    if passage_numbers[i].issuperset(missing) and passage_evidence.supports(claim, vector, i)
                ),
                None,
            )
            if source is None:
                report.unsupported_numbers += [n for n in missing if n not in report.unsupported_numbers]
            else:
                report.added_markers.append(source)
                body = rewritten.rstrip(".!?।:; ")
                rewritten = f"{body}[{source}]{rewritten[len(body) :]}"
                if not kept:
                    report.supported_cited_sentences += 1
        pieces += [answer[position:a], rewritten]
        position = b
    pieces.append(answer[position:])
    answer = "".join(pieces)
    report.answer = _tidy(answer)
    report.cited = sorted({int(n) for n in _MARKER_RE.findall(report.answer)})
    return report
