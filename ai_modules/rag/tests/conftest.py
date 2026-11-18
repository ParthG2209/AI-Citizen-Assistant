"""Shared fixtures for the RAG tests.

The fixture corpus is a handful of passages copied verbatim from official documents listed in
data/raw/circulars/manifest.yaml (URLs below), so retrieval tests run on real text without
downloading anything. Scheme evaluations are built directly from the shared result contract.
"""

from __future__ import annotations

from datetime import date

import pytest

from ai_modules.rag.chunker import Chunk, chunk_document
from ai_modules.rag.ingest import ExtractedDocument, PageText, SourceDocument
from ai_modules.rule_engine.definitions import (
    Benefit,
    BenefitFrequency,
    BenefitType,
    Condition,
    SchemeLevel,
    SchemeRule,
    Source,
    VerificationStatus,
)
from ai_modules.rule_engine.result import ConditionResult, EligibilityStatus, Outcome, SchemeEvaluation

PM_KISAN_URL = "https://pmkisan.gov.in/Documents/RevisedPM-KISANOperationalGuidelines(English).pdf"
GKB_URL = "https://khargone.nic.in/en/scheme/gaon-ki-beti-yojana"

# Verbatim text of page 2 of the PM-KISAN Operational Guidelines (revised 29.03.2020), as extracted.
PM_KISAN_PAGE_2 = """PRADHAN MANTRI KISAN SAMMAN NIDHI (PM-KISAN) SCHEME
OPERATIONAL GUIDELINES
1. Scheme
With a view to provide income support to all landholding farmers’ families in the country,
having cultivable land, the Central Government has implemented a Central Sector
Scheme, namely, “Pradhan Mantri Kisan Samman Nidhi (PM-KISAN)”.
2. Objective and Benefits
The scheme aims to supplement the financial needs of all landholding farmers’ families
in procuring various inputs to ensure proper crop health and appropriate yields,
commensurate with the anticipated farm income as well as for domestic needs. Under
the Scheme an amount of Rs.6000/- per year is released by the Central Government
online directly into the bank accounts of the eligible farmers under Direct Benefit
Transfer mode, subject to certain exclusions.
3. Definition of farmer’s family
A landholder farmer’s family is defined as “a family comprising of husband, wife
and minor children who owns cultivable land as per land records of the concerned
State/UT”. The existing land-ownership system will be used for identification of
beneficiaries for calculation of benefit.
4 Exclusions
4.1 The following categories of beneficiaries of higher economic status shall not be
eligible for benefit under the scheme:
(a) All Institutional Land holders; and
(b) Farmer families in which one or more of its members belong to following
categories :
i) Former and present holders of constitutional posts
ii) Former and present Ministers / State Ministers and former / present Members of
Lok Sabha / Rajya Sabha / State Legislative Assemblies / State Legislative
Councils, former and present Mayors of Municipal Corporations, former and
present Chairpersons of District Panchayats."""

# Verbatim text of the Gaon Ki Beti Yojana page of District Khargone (khargone.nic.in), as extracted.
GKB_PAGE = """Plan Commencement Date
Year 2005
Plan details
• The girl is a resident of the village.
• Staying in the village, passing 12th standard from the village school with minimum 60 per cent marks and \
studying in a graduate class in a government / non-government college or university. All categories of girl \
students are given benefits.
Benefits to the beneficiary
For conventional courses, Rs. 500 / – per month for 10 months, Rs. 5000 / – per year and for studies in \
technical education and medical education, Rs. 750 / – per month for 10 months, Rs. 7500 / – per year.
How to avail the scheme
Every year, applications are made by the online portal of scholarship http://scholarshipportal.mp.nic.in on \
the prescribed dates.
Approval is given by the principal of the college concerned.
After acceptance, the amount gets deposited in the bank account of the students."""


def source(
    doc_id: str, url: str, scheme_ids: tuple[str, ...], fmt: str = "pdf", language: str = "en"
) -> SourceDocument:
    return SourceDocument(
        doc_id=doc_id,
        title=f"Fixture: {doc_id}",
        url=url,
        publisher="Government of India / Government of Madhya Pradesh",
        scheme_ids=scheme_ids,
        language=language,
        format=fmt,
    )


@pytest.fixture(scope="session")
def fixture_documents() -> list[ExtractedDocument]:
    return [
        ExtractedDocument(
            source=source("pm_kisan_guidelines", PM_KISAN_URL, ("pm_kisan",)),
            sha256="0" * 64,
            pages=[PageText(page=2, text=PM_KISAN_PAGE_2)],
        ),
        ExtractedDocument(
            source=source("khargone_gaon_ki_beti", GKB_URL, ("gaon_ki_beti",), fmt="html"),
            sha256="1" * 64,
            pages=[PageText(page=1, text=GKB_PAGE)],
        ),
    ]


@pytest.fixture(scope="session")
def fixture_chunks(fixture_documents: list[ExtractedDocument]) -> list[Chunk]:
    return [c for d in fixture_documents for c in chunk_document(d, chunk_size=400, chunk_overlap=40)]


@pytest.fixture(scope="session")
def embedder():
    from ai_modules.rag.embedder import get_embedder

    return get_embedder()


@pytest.fixture(scope="session")
def fixture_retriever(fixture_chunks: list[Chunk], embedder):
    from ai_modules.rag.retriever import Retriever
    from ai_modules.rag.vector_store import VectorStore

    vectors = embedder.embed([c.text for c in fixture_chunks])
    store = VectorStore.build(fixture_chunks, vectors, model_name=embedder.model_name)
    return Retriever(store, embedder)


def _condition(cid: str, description: str, field: str, outcome: Outcome, message: str) -> ConditionResult:
    return ConditionResult(
        condition_id=cid,
        description=description,
        field=field,
        operator="is_false",
        expected=False,
        actual=outcome is Outcome.FAIL,
        provenance="self_declared",
        outcome=outcome,
        missing_reason=None,
        message=message,
    )


@pytest.fixture
def pm_kisan_rule() -> SchemeRule:
    return SchemeRule(
        scheme_id="pm_kisan",
        version="test",
        name="Pradhan Mantri Kisan Samman Nidhi (PM-KISAN)",
        level=SchemeLevel.CENTRAL,
        department="Department of Agriculture & Farmers Welfare",
        effective_from=date(2020, 3, 29),
        verification_status=VerificationStatus.VERIFIED,
        benefit=Benefit(BenefitType.RECURRING_CASH, "Income support", 6000, BenefitFrequency.ANNUAL),
        conditions=(
            Condition(
                "owns_cultivable_land",
                "Family owns cultivable land",
                "family_owns_cultivable_land",
                "is_true",
            ),
        ),
        sources=(
            Source("PM-KISAN Operational Guidelines", PM_KISAN_URL, "DA&FW", date(2026, 11, 18), "4.1"),
        ),
    )


@pytest.fixture
def not_eligible_evaluation() -> SchemeEvaluation:
    """Test evaluation: one condition failed (a family member is a constitutional post holder)."""
    return SchemeEvaluation(
        scheme_id="pm_kisan",
        scheme_name="Pradhan Mantri Kisan Samman Nidhi (PM-KISAN)",
        rule_version="test",
        rule_hash="test-hash",
        verification_status="verified",
        status=EligibilityStatus.NOT_ELIGIBLE,
        conditions=(
            _condition(
                "owns_cultivable_land",
                "Family owns cultivable land",
                "family_owns_cultivable_land",
                Outcome.PASS,
                "The family owns cultivable land (self-declared).",
            ),
            _condition(
                "no_constitutional_post_holder",
                "No family member holds or held a constitutional post",
                "family_member_constitutional_post",
                Outcome.FAIL,
                "A family member holds or held a constitutional post, which is an exclusion.",
            ),
        ),
        input_hash="test-input",
        as_of=date(2026, 11, 18),
    )
