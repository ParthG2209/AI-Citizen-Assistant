"""Grounded explanations of rule-engine decisions and scheme Q&A (contract 3.5).

Guarantees enforced here, independently of what the language model writes:
* `decision` is copied from the engine's `SchemeEvaluation.status`; the LLM never sets it.
* Only passages whose cosine similarity passes the evidence threshold are shown to the LLM.
* Citation markers [n] must refer to those passages; invalid markers are removed and flagged.
* An answer citing nothing, or flagged INSUFFICIENT_EVIDENCE by the model, is `grounded=False`
  with an `uncertainty` message.
* An answer that contradicts the engine decision (e.g. "you are eligible" for a not-eligible
  result) is discarded and replaced by an extractive answer.
* Questions unrelated to welfare schemes are refused; embedded instructions that try to change
  the assistant's behaviour or the decision are ignored.
* No provider reachable -> extractive answer quoting the retrieved passages, labelled as such.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .embedder import get_embedder
from .llm_router import LLMRouter
from .prompts import INSUFFICIENT_EVIDENCE_TOKEN, build_explain_messages, build_qa_messages
from .retriever import RetrievedPassage, Retriever
from .vector_store import DEFAULT_INDEX_DIR, IndexNotFoundError, VectorStore

if TYPE_CHECKING:
    from ai_modules.rule_engine.definitions import SchemeRule
    from ai_modules.rule_engine.result import SchemeEvaluation

logger = logging.getLogger(__name__)

SUPPORTED_LANGUAGES = ("en", "hi")
EXCERPT_CHARS = 320
MAX_CONDITION_QUERIES = 4

# Words that mark a question as being about welfare schemes / this eligibility result.
# Prefix stems match any word starting with them; short words must match exactly.
_TOPIC_STEMS_EN = (
    "scheme",
    "yojana",
    "yojna",
    "eligib",
    "ineligib",
    "benefit",
    "apply",
    "applic",
    "document",
    "certificat",
    "income",
    "caste",
    "categor",
    "scholar",
    "subsid",
    "pension",
    "kisan",
    "farmer",
    "farming",
    "hous",
    "awas",
    "awaas",
    "pucca",
    "kutcha",
    "health",
    "hospital",
    "insur",
    "ayushman",
    "treatment",
    "student",
    "tuition",
    "hostel",
    "laptop",
    "daughter",
    "ration",
    "samagra",
    "aadhaar",
    "instal",
    "amount",
    "money",
    "rupee",
    "payment",
    "reject",
    "criteri",
    "condition",
    "govern",
    "welfare",
    "household",
    "percent",
    "college",
    "admission",
    "domicile",
    "resident",
    "tribal",
    "verif",
    "portal",
    "regist",
    "deadline",
    "renew",
    "sanction",
    "landhold",
    "cultivat",
    "disburs",
    "entitle",
    "exclu",
)
_TOPIC_WORDS_EN = frozenset(
    "fee fees land rent girl girls beti bank cash grant kyc sc st obc bpl secc pmay pmjay family marks class "
    "status mmvy gkb".split()
)
_TOPIC_WORDS_HI = (
    "योजना",
    "पात्र",
    "लाभ",
    "आवेदन",
    "दस्तावेज",
    "दस्तावेज़",
    "प्रमाण",
    "आय ",
    "जाति",
    "छात्रवृत्ति",
    "किसान",
    "आवास",
    "स्वास्थ्य",
    "बीमा",
    "शुल्क",
    "छात्र",
    "बेटी",
    "राशन",
    "समग्र",
    "बैंक",
    "किस्त",
    "राशि",
    "सरकार",
    "मकान",
    "ज़मीन",
    "जमीन",
    "भूमि",
    "पेंशन",
    "सहायता",
    "अस्पताल",
    "इलाज",
    "कॉलेज",
    "प्रवेश",
    "निवासी",
    "पंजीयन",
    "पोर्टल",
)
# Generic follow-ups that only make sense about the decision being explained.
_DECISION_FOLLOW_UPS_EN = frozenset(
    "why reason reasons next step steps missing fail failed decision result fix".split()
)
_DECISION_FOLLOW_UPS_HI = ("क्यों", "कारण", "अगला", "आगे", "निर्णय", "परिणाम")

_INJECTION_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(ignore|disregard|forget|override|bypass)\b.{0,40}\b(instruction|rule|prompt|guideline|polic|system)",
        r"\b(you are now|act as|pretend (to be|you are)|role-?play|jailbreak|developer mode|dan mode)\b",
        r"\b(reveal|show|print|repeat|output)\b.{0,20}\b(system )?prompt\b",
        r"\b(say|tell|declare|state|confirm|mark|write|answer)\b.{0,30}\b(i am|i'm|me|applicant is|user is|"
        r"i'm now|we are)\b.{0,20}\b(eligible|approved|qualified|selected)\b",
        r"\b(change|modify|override|overwrite|flip)\b.{0,15}\b(your|the engine'?s|the rule engine'?s|"
        r"the system'?s)\s+(decision|answer|output|result)",
        r"(निर्देश|नियम).{0,20}(अनदेखा|नज़रअंदाज़|नजरअंदाज|भूल)",
        r"(मुझे|मैं).{0,20}पात्र.{0,15}(घोषित|बताओ|बताएं|लिखो|कहो)",
    )
)

_SUBJECT_EN = (
    r"(?:you|the applicant|applicant|the household|the family|your family|your household|this person)"
)
_AFFIRM_EN = re.compile(
    rf"\b{_SUBJECT_EN}\s+(?:(?:are|is|will be)\s+"
    r"(?:now\s+|definitely\s+|fully\s+|therefore\s+|also\s+|indeed\s+)?"
    r"(?:eligible|qualified)\b|(?:qualify|qualifies)\b)",
    re.IGNORECASE,
)
_POTENTIAL_EN = re.compile(
    rf"\b{_SUBJECT_EN}\s+(?:are|is|may|might|could)\s+(?:be\s+)?(?:potentially|possibly|likely|still)?\s*"
    r"(?:be\s+)?eligible\b",
    re.IGNORECASE,
)
_DENY_EN = re.compile(
    rf"\b{_SUBJECT_EN}\s+(?:(?:are|is)\s+(?:not\s+eligible|ineligible)|(?:do|does)\s+not\s+qualify)\b",
    re.IGNORECASE,
)
_AFFIRM_HI = re.compile(r"आप[^।.]{0,40}?(?<!अ)पात्र\s+(?:हैं|है|होंगे|होंगी)")
_DENY_HI = re.compile(r"आप[^।.]{0,40}?(?:पात्र\s+नहीं|अपात्र)")
_CONDITIONAL = re.compile(r"\b(?:if|once|after|when|unless|provided)\b|यदि|अगर|जब|बशर्ते", re.IGNORECASE)
_MARKER_LIST_RE = re.compile(r"\[(\d{1,3}(?:\s*,\s*\d{1,3})+)\]")
_MARKER_RE = re.compile(r"\[(\d{1,3})\]")

_TEXT = {
    "en": {
        "decision": "Rule-engine decision",
        "extractive": "Relevant official passages (extractive: quoted verbatim, no language model was used)",
        "no_llm": "No language model was reachable; the answer quotes the retrieved official passages "
        "verbatim.",
        "no_evidence": "No official passage relevant enough to support an explanation was found in the "
        "indexed documents, so the explanation is limited to the rule engine's own condition results.",
        "no_index": "The document index has not been built yet (run scripts/build_vector_index.py), so no "
        "official passages could be retrieved.",
        "insufficient": "The retrieved official passages do not contain enough information to answer this.",
        "insufficient_phrase": "insufficient evidence",
        "uncited": "The generated answer did not cite any official passage, so it could not be verified "
        "against the sources.",
        "invalid": "Some citation markers did not refer to a retrieved passage and were removed.",
        "conflict": "The language model's answer contradicted the rule engine's decision and was discarded; "
        "the official passages are quoted instead.",
        "injection": "Your question contained instructions to change the assistant's behaviour or the "
        "decision; they were ignored. Eligibility is decided only by the rule engine.",
        "off_topic": "I can only answer questions about government welfare schemes and eligibility results.",
        "off_topic_reason": "The question is not related to welfare schemes.",
        "unverified": "The rule definition for this scheme is not fully verified against official sources; "
        "treat the decision as indicative.",
        "conditions": "Condition results",
        "status": {
            "eligible": "Eligible",
            "potentially_eligible": "Potentially eligible (some information is missing)",
            "not_eligible": "Not eligible",
        },
        "outcome": {"pass": "Met", "fail": "Not met", "missing": "Missing information"},
    },
    "hi": {
        "decision": "नियम इंजन का निर्णय",
        "extractive": "संबंधित आधिकारिक अंश (उद्धरण: मूल पाठ, किसी भाषा मॉडल का उपयोग नहीं किया गया)",
        "no_llm": "कोई भाषा मॉडल उपलब्ध नहीं था; उत्तर में प्राप्त आधिकारिक अंश मूल रूप में उद्धृत हैं।",
        "no_evidence": "अनुक्रमित दस्तावेज़ों में व्याख्या का समर्थन करने लायक प्रासंगिक आधिकारिक अंश नहीं मिला, इसलिए "
        "व्याख्या केवल नियम इंजन की शर्तों के परिणामों तक सीमित है।",
        "no_index": "दस्तावेज़ अनुक्रमणिका अभी नहीं बनी है (scripts/build_vector_index.py चलाएँ), इसलिए कोई "
        "आधिकारिक अंश नहीं मिला।",
        "insufficient": "प्राप्त आधिकारिक अंशों में इस प्रश्न का उत्तर देने के लिए पर्याप्त जानकारी नहीं है।",
        "insufficient_phrase": "पर्याप्त प्रमाण नहीं",
        "uncited": "उत्पन्न उत्तर में किसी आधिकारिक अंश का संदर्भ नहीं था, इसलिए स्रोतों से इसकी पुष्टि नहीं हो सकी।",
        "invalid": "कुछ संदर्भ चिह्न किसी प्राप्त अंश से मेल नहीं खाते थे और हटा दिए गए।",
        "conflict": "भाषा मॉडल का उत्तर नियम इंजन के निर्णय के विपरीत था, इसलिए हटा दिया गया; इसके स्थान पर "
        "आधिकारिक अंश उद्धृत हैं।",
        "injection": "आपके प्रश्न में सहायक के व्यवहार या निर्णय को बदलने के निर्देश थे; उन्हें अनदेखा किया गया। "
        "पात्रता का निर्णय केवल नियम इंजन करता है।",
        "off_topic": "मैं केवल सरकारी कल्याण योजनाओं और पात्रता परिणामों से जुड़े प्रश्नों का उत्तर दे सकता हूँ।",
        "off_topic_reason": "प्रश्न कल्याण योजनाओं से संबंधित नहीं है।",
        "unverified": "इस योजना की नियम परिभाषा आधिकारिक स्रोतों से पूरी तरह सत्यापित नहीं है; निर्णय को सांकेतिक मानें।",
        "conditions": "शर्तों के परिणाम",
        "status": {
            "eligible": "पात्र",
            "potentially_eligible": "संभावित रूप से पात्र (कुछ जानकारी अनुपलब्ध है)",
            "not_eligible": "पात्र नहीं",
        },
        "outcome": {"pass": "पूरी", "fail": "पूरी नहीं", "missing": "जानकारी अनुपलब्ध"},
    },
}


@dataclass(frozen=True, slots=True)
class Citation:
    marker: int  # the [n] used in the answer
    chunk_id: str
    doc_id: str
    source_title: str
    url: str
    page: int | None
    section: str | None
    score: float  # dense cosine similarity of the passage to the retrieval query
    excerpt: str


@dataclass(frozen=True, slots=True)
class Explanation:
    answer: str
    decision: str | None  # engine EligibilityStatus value (None for general scheme Q&A)
    citations: tuple[Citation, ...]
    grounded: bool
    provider: str  # "ollama" | "gemini" | "none"
    model: str | None
    uncertainty: str | None
    language: str = "en"
    scheme_id: str | None = None
    refused: bool = False
    flags: tuple[str, ...] = ()
    latency_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["citations"] = [asdict(c) for c in self.citations]
        data["flags"] = list(self.flags)
        return data


@dataclass(slots=True)
class _Draft:
    """Mutable working state while assembling an Explanation."""

    language: str
    passages: list[RetrievedPassage] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _excerpt(text: str) -> str:
    return text if len(text) <= EXCERPT_CHARS else text[: EXCERPT_CHARS - 1].rsplit(" ", 1)[0] + "…"


def detect_prompt_injection(text: str) -> bool:
    return any(p.search(text) for p in _INJECTION_PATTERNS)


def contradicts_decision(answer: str, status: str) -> bool:
    """True when the answer asserts, about the applicant, an outcome different from the engine's status."""
    for sentence in re.split(r"(?<=[.!?।])\s+", answer):
        if _CONDITIONAL.search(sentence):
            continue  # "you would be eligible once the income certificate is uploaded" restates a condition
        denies = bool(_DENY_EN.search(sentence) or _DENY_HI.search(sentence))
        affirms = not denies and bool(
            _AFFIRM_EN.search(sentence) or (_AFFIRM_HI.search(sentence) and "संभावित" not in sentence)
        )
        if status == "eligible" and denies:
            return True
        if status == "potentially_eligible" and (affirms or denies):
            return True
        if status == "not_eligible" and not denies and (affirms or _POTENTIAL_EN.search(sentence)):
            return True
    return False


def mentions_welfare_topic(
    question: str, extra_terms: Sequence[str] = (), follow_up_ok: bool = False
) -> bool:
    """Cheap lexical check: does the question use welfare-scheme vocabulary (English or Hindi)?"""
    lowered = question.lower()
    words = re.findall(r"[a-z0-9]+", lowered)
    if any(w in _TOPIC_WORDS_EN or w.startswith(_TOPIC_STEMS_EN) for w in words):
        return True
    if follow_up_ok and (
        any(w in _DECISION_FOLLOW_UPS_EN for w in words)
        or any(w in question for w in _DECISION_FOLLOW_UPS_HI)
    ):
        return True
    if any(word in question for word in _TOPIC_WORDS_HI):
        return True
    return any(term and term.lower() in lowered for term in extra_terms)


def validate_citations(answer: str, passages: Sequence[RetrievedPassage]) -> tuple[str, list[int], list[int]]:
    """Normalise markers, drop those outside 1..len(passages). Returns (answer, valid, invalid)."""
    answer = _MARKER_LIST_RE.sub(lambda m: "".join(f"[{n.strip()}]" for n in m.group(1).split(",")), answer)
    valid: list[int] = []
    invalid: list[int] = []

    def check(match: re.Match[str]) -> str:
        n = int(match.group(1))
        if 1 <= n <= len(passages):
            if n not in valid:
                valid.append(n)
            return match.group(0)
        invalid.append(n)
        return ""

    answer = _MARKER_RE.sub(check, answer)
    answer = re.sub(r"[ \t]+([.,;:।])", r"\1", re.sub(r"[ \t]{2,}", " ", answer)).strip()
    return answer, sorted(valid), invalid


class ExplanationService:
    def __init__(
        self,
        *,
        index_dir: Path = DEFAULT_INDEX_DIR,
        retriever: Retriever | None = None,
        router: LLMRouter | None = None,
        top_k: int = 5,
    ) -> None:
        self.index_dir = index_dir
        self._retriever = retriever
        self._retriever_loaded = retriever is not None
        self.router = router or LLMRouter()
        self.top_k = top_k

    # ------------------------------------------------------------ retrieval

    @property
    def retriever(self) -> Retriever | None:
        """Lazily load the FAISS index; None when it has not been built yet."""
        if not self._retriever_loaded:
            embedder = get_embedder()
            try:
                store = VectorStore.load(self.index_dir, expected_model=embedder.model_name)
            except IndexNotFoundError:
                logger.warning("RAG vector index not found in %s", self.index_dir)
                store = None
            self._retriever = Retriever(store, embedder) if store is not None else None
            self._retriever_loaded = True
        return self._retriever

    def _retrieve(
        self, queries: Sequence[str], scheme_ids: Sequence[str] | None, context: str | None
    ) -> list[RetrievedPassage]:
        retriever = self.retriever
        if retriever is None:
            return []
        best: dict[str, RetrievedPassage] = {}
        for query in queries:
            for passage in retriever.retrieve(query, scheme_ids=scheme_ids, k=self.top_k, context=context):
                known = best.get(passage.chunk.chunk_id)
                if known is None or passage.score > known.score:
                    best[passage.chunk.chunk_id] = passage
        ranked = sorted(best.values(), key=lambda p: p.score, reverse=True)
        return [p for p in ranked if p.sufficient][: self.top_k]

    def _is_on_topic(self, question: str, extra_terms: Sequence[str], follow_up_ok: bool) -> bool:
        if mentions_welfare_topic(question, extra_terms, follow_up_ok):
            return True
        # Last resort: semantic closeness to the official corpus.
        retriever = self.retriever
        return retriever is not None and retriever.best_dense_score(question) >= retriever.min_score

    # ------------------------------------------------------------ public API

    def explain(
        self,
        evaluation: SchemeEvaluation,
        rule: SchemeRule,
        question: str | None = None,
        language: str = "en",
    ) -> Explanation:
        start = time.perf_counter()
        language = _check_language(language)
        if evaluation.scheme_id != rule.scheme_id:
            raise ValueError(f"evaluation is for {evaluation.scheme_id}, rule is {rule.scheme_id}")
        text = _TEXT[language]
        draft = _Draft(language)
        decision = evaluation.status.value
        question = (question or "").strip() or None

        if question and detect_prompt_injection(question):
            draft.flags.append("prompt_injection_ignored")
            draft.notes.append(text["injection"])
            question = None
        if question and not self._is_on_topic(question, _scheme_terms(rule), follow_up_ok=True):
            return self._refusal(language, decision, rule.scheme_id, start)
        if evaluation.verification_status != "verified":
            draft.flags.append(f"rule_{evaluation.verification_status}")
            draft.notes.append(text["unverified"])

        # Evidence is retrieved for the question and for the conditions that decided the outcome
        # (failed / missing ones; all conditions when everything passed).
        focus = [c for c in evaluation.conditions if c.outcome.value != "pass"] or list(evaluation.conditions)
        queries = ([question] if question else []) + [c.description for c in focus[:MAX_CONDITION_QUERIES]]
        queries = queries or ["eligibility conditions and benefits"]
        draft.passages = self._retrieve(queries, [rule.scheme_id], context=rule.name)

        if not draft.passages:
            draft.flags.append("no_evidence")
            draft.notes.append(text["no_index"] if self.retriever is None else text["no_evidence"])
            answer = _decision_summary(evaluation, language)
            return _finish(draft, answer, [], decision, rule.scheme_id, "none", None, False, start)

        messages = build_explain_messages(evaluation, draft.passages, question, language)
        return self._generate(draft, messages, decision, rule.scheme_id, start, evaluation=evaluation)

    def answer_question(
        self,
        question: str,
        *,
        scheme_id: str | None = None,
        scheme_name: str | None = None,
        language: str = "en",
    ) -> Explanation:
        """General scheme Q&A (no personal decision); optionally restricted to one scheme's documents."""
        start = time.perf_counter()
        language = _check_language(language)
        text = _TEXT[language]
        draft = _Draft(language)
        question = question.strip()
        if not question:
            raise ValueError("question must not be empty")
        if detect_prompt_injection(question):
            draft.flags.append("prompt_injection_detected")
            return self._refusal(
                language, None, scheme_id, start, reason=text["injection"], flags=draft.flags
            )
        terms = [t for t in (scheme_id, scheme_name, (scheme_id or "").replace("_", " ")) if t]
        if not self._is_on_topic(question, terms, follow_up_ok=False):
            return self._refusal(language, None, scheme_id, start)

        draft.passages = self._retrieve([question], [scheme_id] if scheme_id else None, context=scheme_name)
        if not draft.passages:
            draft.flags.append("no_evidence")
            draft.notes.append(text["no_index"] if self.retriever is None else text["insufficient"])
            return _finish(draft, text["insufficient"], [], None, scheme_id, "none", None, False, start)
        names = [scheme_name] if scheme_name else sorted({p.chunk.source_title for p in draft.passages})
        messages = build_qa_messages(names, draft.passages, question, language)
        return self._generate(draft, messages, None, scheme_id, start)

    # ------------------------------------------------------------ helpers

    def _generate(
        self,
        draft: _Draft,
        messages: list[Any],
        decision: str | None,
        scheme_id: str | None,
        start: float,
        evaluation: SchemeEvaluation | None = None,
    ) -> Explanation:
        text = _TEXT[draft.language]
        response = self.router.generate(messages)
        if response is None:
            draft.flags.append("llm_unavailable")
            draft.notes.append(text["no_llm"])
            answer, cited = _extractive_answer(draft, evaluation)
            return _finish(draft, answer, cited, decision, scheme_id, "none", None, True, start)

        answer, cited, invalid = validate_citations(response.text, draft.passages)
        if invalid:
            draft.flags.append("invalid_citations_removed:" + ",".join(str(n) for n in invalid))
            draft.notes.append(text["invalid"])
        if decision is not None and contradicts_decision(answer, decision):
            draft.flags.append("llm_answer_contradicted_decision")
            draft.notes.append(text["conflict"])
            answer, cited = _extractive_answer(draft, evaluation)
            return _finish(draft, answer, cited, decision, scheme_id, "none", None, True, start)

        grounded = True
        if INSUFFICIENT_EVIDENCE_TOKEN in answer:
            if answer.lstrip().startswith(INSUFFICIENT_EVIDENCE_TOKEN):
                answer = answer.lstrip()[len(INSUFFICIENT_EVIDENCE_TOKEN) :].lstrip(" :.-\n")
            answer = answer.replace(INSUFFICIENT_EVIDENCE_TOKEN, text["insufficient_phrase"]).strip()
            answer = answer or text["insufficient"]
            draft.flags.append("insufficient_evidence")
            draft.notes.append(text["insufficient"])
            grounded = False
        elif not cited:
            draft.flags.append("uncited_answer")
            draft.notes.append(text["uncited"])
            grounded = False
        return _finish(
            draft, answer, cited, decision, scheme_id, response.provider, response.model, grounded, start
        )

    def _refusal(
        self,
        language: str,
        decision: str | None,
        scheme_id: str | None,
        start: float,
        *,
        reason: str | None = None,
        flags: Sequence[str] = (),
    ) -> Explanation:
        text = _TEXT[language]
        return Explanation(
            answer=text["off_topic"] if reason is None else reason,
            decision=decision,
            citations=(),
            grounded=False,
            provider="none",
            model=None,
            uncertainty=reason or text["off_topic_reason"],
            language=language,
            scheme_id=scheme_id,
            refused=True,
            flags=(*flags, "refused_off_topic" if reason is None else "refused"),
            latency_ms=int((time.perf_counter() - start) * 1000),
        )


def _check_language(language: str) -> str:
    if language not in SUPPORTED_LANGUAGES:
        raise ValueError(f"language must be one of {SUPPORTED_LANGUAGES}, got {language!r}")
    return language


def _scheme_terms(rule: SchemeRule) -> list[str]:
    return [
        rule.scheme_id,
        rule.scheme_id.replace("_", " "),
        rule.name,
        *rule.name.replace("-", " ").split("(")[:1],
    ]


def _decision_summary(evaluation: SchemeEvaluation, language: str) -> str:
    text = _TEXT[language]
    lines = [f"{text['decision']}: {text['status'][evaluation.status.value]}.", f"{text['conditions']}:"]
    lines += [f"- {text['outcome'][c.outcome.value]}: {c.message}" for c in evaluation.conditions]
    return "\n".join(lines)


def _extractive_answer(draft: _Draft, evaluation: SchemeEvaluation | None) -> tuple[str, list[int]]:
    text = _TEXT[draft.language]
    parts = [_decision_summary(evaluation, draft.language), ""] if evaluation is not None else []
    parts.append(f"{text['extractive']}:")
    for n, passage in enumerate(draft.passages, start=1):
        chunk = passage.chunk
        where = f", p. {chunk.page}" if chunk.page else ""
        parts.append(f"[{n}] “{_excerpt(chunk.text)}” — {chunk.source_title}{where}")
    return "\n".join(parts), list(range(1, len(draft.passages) + 1))


def _finish(
    draft: _Draft,
    answer: str,
    cited: Sequence[int],
    decision: str | None,
    scheme_id: str | None,
    provider: str,
    model: str | None,
    grounded: bool,
    start: float,
) -> Explanation:
    citations = tuple(
        Citation(
            marker=n,
            chunk_id=(p := draft.passages[n - 1]).chunk.chunk_id,
            doc_id=p.chunk.doc_id,
            source_title=p.chunk.source_title,
            url=p.chunk.url,
            page=p.chunk.page,
            section=p.chunk.section,
            score=round(p.dense_score, 4),
            excerpt=_excerpt(p.chunk.text),
        )
        for n in cited
    )
    return Explanation(
        answer=answer,
        decision=decision,
        citations=citations,
        grounded=grounded and bool(citations),
        provider=provider,
        model=model,
        uncertainty=" ".join(draft.notes) or None,
        language=draft.language,
        scheme_id=scheme_id,
        flags=tuple(draft.flags),
        latency_ms=int((time.perf_counter() - start) * 1000),
    )
