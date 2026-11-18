"""Unit tests for the post-generation grounding checks (no model download: lexical path, or a stub
embedder with hand-set vectors for the semantic path)."""

from __future__ import annotations

import numpy as np
import pytest

from ai_modules.rag.chunker import Chunk
from ai_modules.rag.grounding import (
    LEXICAL_SUPPORT_MIN,
    SEMANTIC_SUPPORT_MIN,
    extract_numbers,
    split_sentences,
    verify_grounding,
)
from ai_modules.rag.retriever import RetrievedPassage

from .conftest import GKB_PAGE

# Verbatim sentences of the fixture documents (see conftest.py).
PM_KISAN_BENEFIT = (
    "Under the Scheme an amount of Rs.6000/- per year is released by the Central Government online directly "
    "into the bank accounts of the eligible farmers under Direct Benefit Transfer mode, subject to certain "
    "exclusions."
)
PM_KISAN_FAMILY = (
    "A landholder farmer's family is defined as a family comprising of husband, wife and minor children who "
    "owns cultivable land as per land records of the concerned State/UT."
)


def _passages(*texts: str) -> list[RetrievedPassage]:
    return [
        RetrievedPassage(
            Chunk(f"c{i}", "d", text, ("pm_kisan",), "T", "https://pmkisan.gov.in/x.pdf", "p", 1, None, "en"),
            0.6,
            0.6,
            0.0,
            True,
        )
        for i, text in enumerate(texts)
    ]


class StubEmbedder:
    """Returns fixed unit vectors per text; unknown texts get an orthogonal 'unrelated' vector."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors

    def embed(self, texts: list[str]) -> np.ndarray:
        rows = []
        for text in texts:
            vector = np.asarray(self.vectors.get(text, [0.0, 0.0, 1.0]), dtype=np.float32)
            rows.append(vector / np.linalg.norm(vector))
        return np.vstack(rows)


def test_thresholds_are_the_documented_values():
    assert LEXICAL_SUPPORT_MIN == 0.5
    assert SEMANTIC_SUPPORT_MIN == 0.32


def test_sentence_split_keeps_abbreviations_list_items_and_trailing_markers_together():
    text = (
        "Rs. 500 per month for 10 months. Next sentence [1].\n1. Pucca houses: excluded.\n2. Other item\n[2]"
    )
    assert split_sentences(text) == [
        "Rs. 500 per month for 10 months.",
        "Next sentence [1].",
        "1. Pucca houses: excluded.",
        "2. Other item\n[2]",
    ]


def test_numbers_are_normalised():
    assert extract_numbers("Rs.6000/- per year, 1,20,000 and ६००० rupees, 2.5 acres, 60% and para 4.1.2") == {
        "6000",
        "120000",
        "2.5",
        "60",
        "4",
        "1",
        "2",
    }
    assert extract_numbers("INR 10,000.00") == {"10000"}


def test_lexically_supported_citation_is_kept_and_grounded():
    passages = _passages(PM_KISAN_BENEFIT, PM_KISAN_FAMILY)
    report = verify_grounding(
        "PM-KISAN releases Rs. 6,000 per year directly into the bank accounts of eligible farmers [1].",
        passages,
        embedder=None,
    )
    assert report.grounded
    assert report.cited == [1] and report.removed_markers == [] and report.unsupported_numbers == []


def test_citation_of_a_passage_that_does_not_support_the_sentence_is_removed():
    passages = _passages(PM_KISAN_BENEFIT, PM_KISAN_FAMILY)
    report = verify_grounding(
        "PM-KISAN releases Rs.6000 per year into the bank accounts of eligible farmers [1][2].",
        passages,
        embedder=None,
    )
    assert report.removed_markers == [2]
    assert report.cited == [1]
    assert report.answer.endswith("farmers [1].")
    assert report.grounded


def test_answer_whose_only_citation_is_unsupported_is_not_grounded():
    report = verify_grounding(
        "The capital of France is Paris [1].", _passages(PM_KISAN_BENEFIT), embedder=None
    )
    assert report.answer == "The capital of France is Paris."
    assert report.removed_markers == [1] and report.cited == []
    assert report.supported_cited_sentences == 0
    assert report.unsupported_statements == ["The capital of France is Paris."]
    assert not report.grounded


@pytest.mark.parametrize(
    "answer",
    [
        "PM-KISAN releases Rs. 12,000 per year into the bank accounts of eligible farmers [1].",
        "पीएम-किसान में हर साल 9000 रुपये eligible farmers के bank accounts में मिलते हैं [1]।",
        "PM-KISAN releases Rs.6000 per year to eligible farmers aged 18 or more [1].",
    ],
)
def test_numbers_absent_from_cited_passages_and_facts_are_flagged(answer: str):
    report = verify_grounding(answer, _passages(PM_KISAN_BENEFIT), embedder=None)
    assert report.unsupported_numbers
    assert "6000" not in report.unsupported_numbers
    assert not report.grounded


def test_numbers_from_engine_facts_and_question_are_allowed():
    facts = ["No family member is a retired pensioner drawing INR 10,000 or more per month"]
    answer = (
        "PM-KISAN releases Rs.6000 per year to eligible farmers [1]. "
        "A family member draws a pension of INR 10,000 or more per month, which is an exclusion. "
        "At 65 years the age you mentioned is not a PM-KISAN condition."
    )
    report = verify_grounding(
        answer,
        _passages(PM_KISAN_BENEFIT),
        embedder=None,
        facts=facts,
        question="I am 65, can I get PM-KISAN?",
    )
    assert report.unsupported_numbers == []


def test_uncited_engine_fact_statement_is_accepted_but_unrelated_statement_is_not():
    facts = [
        "Rule-engine decision: Not eligible.",
        "A family member paid income tax in the last assessment year.",
    ]
    passages = _passages(PM_KISAN_BENEFIT)
    ok = verify_grounding(
        "PM-KISAN releases Rs.6000 per year to eligible farmers [1]. "
        "A family member paid income tax in the last assessment year.",
        passages,
        embedder=None,
        facts=facts,
    )
    assert ok.grounded and ok.unsupported_statements == []
    bad = verify_grounding(
        "PM-KISAN releases Rs.6000 per year to eligible farmers [1]. "
        "Applicants should visit the district collector with a caste certificate.",
        passages,
        embedder=None,
        facts=facts,
    )
    assert bad.unsupported_statements == [
        "Applicants should visit the district collector with a caste certificate."
    ]
    assert not bad.grounded


def test_number_from_a_retrieved_but_uncited_passage_gets_that_citation():
    gkb_benefit = next(line for line in GKB_PAGE.splitlines() if line.startswith("For conventional"))
    passages = _passages("Plan details: the girl is a resident of the village.", gkb_benefit)
    report = verify_grounding(
        "For technical and medical education the scholarship is Rs. 750 per month for 10 months [1].",
        passages,
        embedder=None,
    )
    assert report.unsupported_numbers == []
    assert report.added_markers == [2]
    assert "[2]" in report.answer and report.grounded


def test_semantic_support_admits_cross_language_paraphrase_above_threshold_only():
    hindi = "जिस परिवार के किसी सदस्य ने पिछले वर्ष आयकर दिया, वह योजना से बाहर है [1]।"
    claim = "जिस परिवार के किसी सदस्य ने पिछले वर्ष आयकर दिया, वह योजना से बाहर है।"
    passage = "Families in which a member paid income tax in the last assessment year are excluded."
    cos = SEMANTIC_SUPPORT_MIN + 0.05
    close = StubEmbedder({claim: [cos, np.sqrt(1 - cos**2), 0.0], passage: [1.0, 0.0, 0.0]})
    report = verify_grounding(hindi, _passages(passage), embedder=close)
    assert report.cited == [1] and report.grounded

    cos = SEMANTIC_SUPPORT_MIN - 0.05
    far = StubEmbedder({claim: [cos, np.sqrt(1 - cos**2), 0.0], passage: [1.0, 0.0, 0.0]})
    report = verify_grounding(hindi, _passages(passage), embedder=far)
    assert report.removed_markers == [1] and not report.grounded
