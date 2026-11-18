"""Retrieval-augmented explanations grounded in official scheme documents (contract 3.5).

    from ai_modules.rag import ExplanationService
    ExplanationService().explain(evaluation, rule, question=None, language="en") -> Explanation
    ExplanationService().answer_question(question, scheme_id=None, scheme_name=None, language="en")

Heavy dependencies (sentence-transformers / torch, faiss, PyMuPDF) are imported lazily.
"""

from .service import Citation, Explanation, ExplanationService

__all__ = ["Citation", "Explanation", "ExplanationService"]
