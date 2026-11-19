"""SYNTHETIC scheme rules for unit tests only. The ids, amounts and conditions are invented test
fixtures and do not describe any real government scheme."""

from __future__ import annotations

from datetime import date

from ai_modules.knowledge_graph.graph_builder import District
from ai_modules.rule_engine.definitions import (
    Benefit,
    BenefitFrequency,
    BenefitType,
    Condition,
    ConditionGroup,
    GroupMode,
    Relationship,
    SchemeLevel,
    SchemeRule,
    Source,
    VerificationStatus,
)
from ai_modules.rule_engine.fields import Provenance

SOURCE = Source("Synthetic test source", "https://example.org/synthetic", "Test fixture", date(2026, 1, 1))
URL = "https://example.org/synthetic/relationship"


def rule(
    scheme_id: str,
    level: SchemeLevel = SchemeLevel.STATE,
    conditions: tuple = (),
    required_documents: tuple[str, ...] = (),
    relationships: tuple[Relationship, ...] = (),
) -> SchemeRule:
    return SchemeRule(
        scheme_id=scheme_id,
        version="1.0.0",
        name=f"Synthetic {scheme_id}",
        level=level,
        state="Madhya Pradesh" if level is SchemeLevel.STATE else None,
        department="Test department",
        effective_from=date(2025, 1, 1),
        verification_status=VerificationStatus.UNVERIFIED,
        benefit=Benefit(BenefitType.RECURRING_CASH, "Synthetic benefit", 1000.0, BenefitFrequency.ANNUAL),
        conditions=conditions,
        sources=(SOURCE,),
        required_documents=required_documents,
        relationships=relationships,
        rule_hash=f"hash-{scheme_id}",
    )


def synthetic_rules() -> list[SchemeRule]:
    """3 schemes, 7 condition nodes (1 group with 2 children), 3 scheme-scheme edges."""
    alpha = rule(
        "syn_alpha",
        level=SchemeLevel.CENTRAL,
        conditions=(
            Condition(
                "income",
                "Income below limit",
                "annual_family_income",
                "lte",
                100000,
                Provenance.DOCUMENT_EXTRACTED,
                ("income_certificate",),
            ),
            Condition(
                "land", "Owns land", "family_owns_cultivable_land", "eq", True, resolves_with=("land_record",)
            ),
        ),
        required_documents=("income_certificate", "land_record"),
        relationships=(Relationship("syn_beta", "COMPLEMENTED_BY", "Synthetic complement", URL),),
    )
    beta = rule(
        "syn_beta",
        conditions=(
            ConditionGroup(
                "residence",
                "Domicile or Samagra",
                GroupMode.ANY,
                (
                    Condition(
                        "domicile",
                        "MP domicile",
                        "domicile_state",
                        "eq",
                        "Madhya Pradesh",
                        resolves_with=("samagra_domicile",),
                    ),
                    Condition(
                        "samagra",
                        "Has Samagra ID",
                        "has_samagra_id",
                        "eq",
                        True,
                        resolves_with=("samagra_domicile", "has_samagra_id"),
                    ),
                ),
            ),
            Condition("age", "Age at least 18", "age", "gte", 18),
        ),
        relationships=(Relationship("syn_alpha", "TOPS_UP", "Synthetic top-up", URL),),
    )
    gamma = rule(
        "syn_gamma",
        conditions=(
            Condition(
                "marks",
                "Class 12 marks",
                "class12_percentage",
                "gte",
                75,
                Provenance.DOCUMENT_EXTRACTED,
                ("class12_marksheet",),
            ),
        ),
        required_documents=("class12_marksheet",),
        relationships=(Relationship("syn_beta", "MUTUALLY_EXCLUSIVE_WITH", "Synthetic exclusion", None),),
    )
    return [alpha, beta, gamma]


DISTRICTS = (District("bhopal", "Bhopal"), District("indore", "Indore"))
