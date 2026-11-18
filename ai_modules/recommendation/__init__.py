"""Benefit-gap detection and ranked recommendations over deterministic rule-engine results.

from ai_modules.recommendation import build_recommendations
report = build_recommendations(evaluations, enrolled_scheme_ids, complements, rules)
report.to_dict()
"""

from .complementary import MutualExclusionWarning, Presentation
from .gap_detector import ActionKind, DocumentGap, FieldNeed, GapAnalysis, MissingItem, detect_gaps
from .ranker import RankInput, rank
from .report import (
    BenefitSummary,
    BenefitTotals,
    ComplementaryRecommendation,
    NextAction,
    RecommendationReport,
    SchemeRecommendation,
    TotalLine,
    build_recommendations,
)

__all__ = [
    "ActionKind",
    "BenefitSummary",
    "BenefitTotals",
    "ComplementaryRecommendation",
    "DocumentGap",
    "FieldNeed",
    "GapAnalysis",
    "MissingItem",
    "MutualExclusionWarning",
    "NextAction",
    "Presentation",
    "RankInput",
    "RecommendationReport",
    "SchemeRecommendation",
    "TotalLine",
    "build_recommendations",
    "detect_gaps",
    "rank",
]
