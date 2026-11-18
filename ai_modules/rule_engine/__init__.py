"""Deterministic, versioned eligibility rule engine for welfare schemes.

Public API::

    from ai_modules.rule_engine import RuleEngine, load_rules
    engine = RuleEngine()                          # loads ai_modules/rule_engine/rules/**/*.yaml
    evaluation = engine.evaluate("pm_kisan", profile, as_of=date(2026, 11, 18))
"""

from .definitions import (
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
from .engine import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    NoEffectiveRuleError,
    ProfileValueError,
    RuleEngine,
    UnknownSchemeError,
    format_value,
)
from .fields import (
    DOCUMENT_TYPES,
    PROFILE_FIELDS,
    TRUST,
    FieldSpec,
    FieldType,
    FieldValue,
    Provenance,
    coerce_value,
)
from .loader import (
    DEFAULT_RULES_DIR,
    RELATION_TYPES,
    RuleValidationError,
    compute_rule_hash,
    load_rule_file,
    load_rules,
    parse_rule,
    validate_rule_set,
)
from .operators import OPERATORS, apply_operator
from .result import DISCLAIMER, ConditionResult, EligibilityStatus, MissingReason, Outcome, SchemeEvaluation

__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_RULES_DIR",
    "DISCLAIMER",
    "DOCUMENT_TYPES",
    "OPERATORS",
    "PROFILE_FIELDS",
    "RELATION_TYPES",
    "TRUST",
    "Benefit",
    "BenefitFrequency",
    "BenefitType",
    "Condition",
    "ConditionGroup",
    "ConditionResult",
    "EligibilityStatus",
    "FieldSpec",
    "FieldType",
    "FieldValue",
    "GroupMode",
    "MissingReason",
    "NoEffectiveRuleError",
    "Outcome",
    "ProfileValueError",
    "Provenance",
    "Relationship",
    "RuleEngine",
    "RuleValidationError",
    "SchemeEvaluation",
    "SchemeLevel",
    "SchemeRule",
    "Source",
    "UnknownSchemeError",
    "VerificationStatus",
    "apply_operator",
    "coerce_value",
    "compute_rule_hash",
    "format_value",
    "load_rule_file",
    "load_rules",
    "parse_rule",
    "validate_rule_set",
]
