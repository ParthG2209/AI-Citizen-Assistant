"""Canonical citizen-profile field catalog, provenance levels and document types.

This module is the shared data contract between the backend, OCR, rule engine,
recommendation and RAG modules. Every profile attribute that a rule may reference
must be declared here with an explicit type.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import Any


class FieldType(StrEnum):
    INTEGER = "integer"
    NUMBER = "number"
    STRING = "string"
    BOOLEAN = "boolean"
    ENUM = "enum"


class Provenance(StrEnum):
    """Where a profile value came from. Never conflate document text with verification."""

    SELF_DECLARED = "self_declared"  # typed in / corrected by the citizen or NGO volunteer
    DOCUMENT_EXTRACTED = "document_extracted"  # read by OCR from an uploaded certificate
    VERIFIED = "verified"  # confirmed against an authoritative source (not implemented yet)


class TrustLevel(IntEnum):
    SELF_DECLARED = 1
    DOCUMENT_EXTRACTED = 2
    VERIFIED = 3


TRUST: dict[Provenance, TrustLevel] = {
    Provenance.SELF_DECLARED: TrustLevel.SELF_DECLARED,
    Provenance.DOCUMENT_EXTRACTED: TrustLevel.DOCUMENT_EXTRACTED,
    Provenance.VERIFIED: TrustLevel.VERIFIED,
}


@dataclass(frozen=True, slots=True)
class FieldSpec:
    name: str
    type: FieldType
    label: str
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class FieldValue:
    """A single profile value with its provenance. ``confidence`` is OCR confidence in [0, 1]."""

    value: Any
    provenance: Provenance
    confidence: float | None = None
    source_document_id: str | None = None


GENDERS = ("female", "male", "transgender")
CATEGORIES = ("general", "ews", "obc", "sc", "st")
RESIDENCE = ("rural", "urban")
OCCUPATIONS = (
    "student",
    "farmer",
    "agricultural_labourer",
    "daily_wage_worker",
    "self_employed",
    "private_salaried",
    "government_employee",
    "unemployed",
    "homemaker",
    "other",
)
BOARDS = ("mpbse", "cbse", "other")

_SPECS: tuple[FieldSpec, ...] = (
    FieldSpec("full_name", FieldType.STRING, "Full name", sensitive=True),
    FieldSpec("age", FieldType.INTEGER, "Age (years)", minimum=0, maximum=120),
    FieldSpec("gender", FieldType.ENUM, "Gender", choices=GENDERS),
    FieldSpec("domicile_state", FieldType.STRING, "State of domicile"),
    FieldSpec("district", FieldType.STRING, "District"),
    FieldSpec("residence_type", FieldType.ENUM, "Residence (rural/urban)", choices=RESIDENCE),
    FieldSpec("annual_family_income", FieldType.NUMBER, "Annual family income (INR)", minimum=0),
    FieldSpec("social_category", FieldType.ENUM, "Social category", choices=CATEGORIES),
    FieldSpec("occupation", FieldType.ENUM, "Occupation / status", choices=OCCUPATIONS),
    FieldSpec("enrolled_in_higher_education", FieldType.BOOLEAN, "Currently enrolled in college / UG course"),
    FieldSpec("class12_percentage", FieldType.NUMBER, "Class 12 percentage", minimum=0, maximum=100),
    FieldSpec("class12_board", FieldType.ENUM, "Class 12 board", choices=BOARDS),
    FieldSpec("class12_passing_year", FieldType.INTEGER, "Class 12 passing year", minimum=1950, maximum=2100),
    FieldSpec("family_owns_cultivable_land", FieldType.BOOLEAN, "Family owns cultivable land"),
    FieldSpec("owns_pucca_house", FieldType.BOOLEAN, "Household owns a pucca house"),
    FieldSpec("family_member_income_tax_payer", FieldType.BOOLEAN, "Any family member paid income tax last year"),
    FieldSpec("family_member_government_employee", FieldType.BOOLEAN, "Any family member is a serving govt employee"),
    FieldSpec("family_member_pensioner_above_10k", FieldType.BOOLEAN, "Any family member gets pension >= INR 10,000/month"),
    FieldSpec("listed_in_secc_or_nfsa", FieldType.BOOLEAN, "Household listed in SECC-2011 / NFSA ration card list"),
    FieldSpec("has_samagra_id", FieldType.BOOLEAN, "Household has an MP Samagra ID"),
)

PROFILE_FIELDS: dict[str, FieldSpec] = {spec.name: spec for spec in _SPECS}


@dataclass(frozen=True, slots=True)
class DocumentType:
    id: str
    label: str
    ocr_supported: bool
    provides_fields: tuple[str, ...] = field(default_factory=tuple)


_DOCS: tuple[DocumentType, ...] = (
    DocumentType("income_certificate", "MP Income Certificate", True, ("full_name", "annual_family_income", "district")),
    DocumentType("caste_certificate", "Caste Certificate", True, ("full_name", "social_category", "district")),
    DocumentType(
        "samagra_domicile",
        "Samagra ID / Domicile Certificate",
        True,
        ("full_name", "domicile_state", "district", "gender", "age", "has_samagra_id"),
    ),
    DocumentType(
        "class12_marksheet",
        "Class 12 Marksheet",
        True,
        ("full_name", "class12_percentage", "class12_board", "class12_passing_year"),
    ),
    DocumentType("land_record", "Land record (Khasra / B-1)", False, ("family_owns_cultivable_land",)),
    DocumentType("ration_card", "NFSA ration card / SECC listing proof", False, ("listed_in_secc_or_nfsa",)),
    DocumentType("college_admission", "College admission proof / fee receipt", False, ("enrolled_in_higher_education",)),
)

DOCUMENT_TYPES: dict[str, DocumentType] = {doc.id: doc for doc in _DOCS}


def coerce_value(name: str, value: Any) -> Any:
    """Validate and coerce a raw value to the declared field type. Raises ValueError."""
    spec = PROFILE_FIELDS.get(name)
    if spec is None:
        raise ValueError(f"Unknown profile field '{name}'")
    if value is None:
        raise ValueError(f"Field '{name}' must not be null")

    if spec.type is FieldType.BOOLEAN:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "yes", "1"}:
            return True
        if isinstance(value, str) and value.strip().lower() in {"false", "no", "0"}:
            return False
        raise ValueError(f"Field '{name}' must be a boolean")

    if spec.type in (FieldType.INTEGER, FieldType.NUMBER):
        if isinstance(value, bool):
            raise ValueError(f"Field '{name}' must be numeric")
        try:
            number = int(value) if spec.type is FieldType.INTEGER else float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Field '{name}' must be {'an integer' if spec.type is FieldType.INTEGER else 'a number'}") from exc
        if spec.type is FieldType.INTEGER and isinstance(value, float) and not value.is_integer():
            raise ValueError(f"Field '{name}' must be an integer")
        if spec.minimum is not None and number < spec.minimum:
            raise ValueError(f"Field '{name}' must be >= {spec.minimum:g}")
        if spec.maximum is not None and number > spec.maximum:
            raise ValueError(f"Field '{name}' must be <= {spec.maximum:g}")
        return number

    text = str(value).strip()
    if not text:
        raise ValueError(f"Field '{name}' must not be empty")
    if spec.type is FieldType.ENUM:
        text = text.lower()
        if text not in spec.choices:
            raise ValueError(f"Field '{name}' must be one of {', '.join(spec.choices)}")
    return text
