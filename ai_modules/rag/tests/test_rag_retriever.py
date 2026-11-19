from __future__ import annotations

import pytest

from ai_modules.rag.retriever import BM25, tokenize


def test_tokenize_handles_english_and_hindi():
    assert tokenize("Who is excluded from PM-KISAN?") == ["excluded", "pm", "kisan"]
    assert tokenize("गाँव की बेटी योजना।") == ["गाँव", "बेटी", "योजना"]


def test_bm25_prefers_documents_with_query_terms():
    bm25 = BM25(["pucca house ownership exclusion", "laptop for meritorious students", "pucca pucca roof"])
    assert bm25.score("pucca house", 0) > bm25.score("pucca house", 1) == 0.0
    assert bm25.score("laptop", 1) > 0


@pytest.mark.slow
def test_retrieves_exclusion_paragraph_with_real_embeddings(fixture_retriever):
    passages = fixture_retriever.retrieve(
        "Which families holding constitutional posts are excluded from PM-KISAN?",
        scheme_ids=["pm_kisan"],
        k=3,
    )
    assert passages, "expected at least one PM-KISAN passage"
    top = passages[0]
    assert top.chunk.doc_id == "pm_kisan_guidelines"
    assert "constitutional posts" in top.chunk.text
    assert top.sufficient and top.dense_score >= fixture_retriever.min_score
    assert all(-1.0 <= p.dense_score <= 1.0 for p in passages)
    assert [p.score for p in passages] == sorted((p.score for p in passages), reverse=True)


@pytest.mark.slow
def test_scheme_filter_only_returns_that_schemes_documents(fixture_retriever):
    passages = fixture_retriever.retrieve(
        "How much money is paid per year?", scheme_ids=["gaon_ki_beti"], k=5
    )
    assert passages
    assert {p.chunk.doc_id for p in passages} == {"khargone_gaon_ki_beti"}
    unfiltered = fixture_retriever.retrieve("How much money is paid per year?", k=10)
    assert {p.chunk.doc_id for p in unfiltered} == {"khargone_gaon_ki_beti", "pm_kisan_guidelines"}
    assert fixture_retriever.retrieve("anything", scheme_ids=["unknown_scheme"]) == []


@pytest.mark.slow
def test_hindi_query_finds_english_passage(fixture_retriever):
    passages = fixture_retriever.retrieve("गाँव की बेटी योजना में कितनी छात्रवृत्ति मिलती है?", k=2)
    assert passages[0].chunk.doc_id == "khargone_gaon_ki_beti"
    assert passages[0].sufficient


@pytest.mark.slow
def test_off_topic_query_scores_below_evidence_threshold(fixture_retriever):
    for query in ("What is the capital of France?", "How do I bake a chocolate cake?"):
        assert fixture_retriever.best_dense_score(query) < fixture_retriever.min_score
        assert not any(p.sufficient for p in fixture_retriever.retrieve(query, k=3))
