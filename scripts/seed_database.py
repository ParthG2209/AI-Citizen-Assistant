"""Prepare a development database (idempotent).

1. Apply Alembic migrations up to head.
2. Sync scheme rules (all versions loaded by the rule engine) into ``schemes`` / ``scheme_rule_versions``.
3. Create a few clearly SYNTHETIC demo citizens (``is_synthetic=true``) with mixed provenance.
   Their "documents" are metadata-only seed records: no file was uploaded and no OCR was run.

Run from the repository root:
    python scripts/seed_database.py
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for path in (REPO_ROOT, REPO_ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.database import get_sessionmaker  # noqa: E402
from app.models import Citizen, Document, ProfileField  # noqa: E402
from app.services.scheme_service import all_rule_versions, sync_rules  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from ai_modules.rule_engine import RuleEngine  # noqa: E402
from ai_modules.rule_engine.fields import Provenance, coerce_value  # noqa: E402

SYNTHETIC_WARNING = "Synthetic seed record: no file was uploaded and no OCR was run."


@dataclass(frozen=True)
class SeedDocument:
    document_type: str
    confidence: float  # demo value for the seed record, not an OCR measurement
    fields: dict[str, Any]


@dataclass(frozen=True)
class SeedCitizen:
    label: str
    self_declared: dict[str, Any]
    documents: tuple[SeedDocument, ...] = field(default_factory=tuple)


DEMO_CITIZENS: tuple[SeedCitizen, ...] = (
    SeedCitizen(
        label="Synthetic demo: rural OBC student (Sehore)",
        self_declared={
            "age": 19,
            "gender": "female",
            "residence_type": "rural",
            "occupation": "student",
            "enrolled_in_higher_education": True,
        },
        documents=(
            SeedDocument(
                "samagra_domicile",
                0.93,
                {"domicile_state": "Madhya Pradesh", "district": "Sehore", "has_samagra_id": True},
            ),
            SeedDocument("income_certificate", 0.94, {"annual_family_income": 150000}),
            SeedDocument("caste_certificate", 0.91, {"social_category": "obc"}),
            SeedDocument(
                "class12_marksheet",
                0.96,
                {"class12_percentage": 82.4, "class12_board": "mpbse", "class12_passing_year": 2025},
            ),
        ),
    ),
    SeedCitizen(
        label="Synthetic demo: small farmer household (Vidisha)",
        self_declared={
            "age": 46,
            "gender": "male",
            "domicile_state": "Madhya Pradesh",
            "district": "Vidisha",
            "residence_type": "rural",
            "occupation": "farmer",
            "social_category": "sc",
            "family_owns_cultivable_land": True,
            "owns_pucca_house": False,
            "family_member_income_tax_payer": False,
            "family_member_government_employee": False,
            "family_member_pensioner_above_10k": False,
            "listed_in_secc_or_nfsa": True,
        },
        # Deliberately low confidence: shows how the engine treats unclear document values.
        documents=(SeedDocument("income_certificate", 0.62, {"annual_family_income": 90000}),),
    ),
    SeedCitizen(
        label="Synthetic demo: urban salaried applicant (Indore)",
        self_declared={
            "age": 34,
            "gender": "female",
            "domicile_state": "Madhya Pradesh",
            "district": "Indore",
            "residence_type": "urban",
            "occupation": "private_salaried",
            "annual_family_income": 650000,
            "social_category": "general",
            "owns_pucca_house": True,
            "family_member_income_tax_payer": True,
        },
    ),
)


def migrate() -> None:
    config = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    print("migrations: database at head")


def seed_citizen(db: Session, seed: SeedCitizen) -> bool:
    """Create one demo citizen; returns False when it already exists."""
    existing = db.scalar(select(Citizen).where(Citizen.label == seed.label, Citizen.is_synthetic.is_(True)))
    if existing is not None:
        return False
    citizen = Citizen(label=seed.label, is_synthetic=True)
    db.add(citizen)
    db.flush()
    for name, value in seed.self_declared.items():
        db.add(
            ProfileField(
                citizen_id=citizen.id,
                field_name=name,
                value=coerce_value(name, value),
                provenance=Provenance.SELF_DECLARED.value,
            )
        )
    for doc in seed.documents:
        marker = f"{seed.label}|{doc.document_type}".encode()
        document = Document(
            citizen_id=citizen.id,
            document_type=doc.document_type,
            original_filename=f"synthetic_{doc.document_type}.pdf",
            content_type="application/pdf",
            size_bytes=0,
            sha256=hashlib.sha256(marker).hexdigest(),
            status="processed",
            ocr_engine="synthetic-seed",
            mean_confidence=doc.confidence,
            extracted_fields=[
                {"name": n, "value": coerce_value(n, v), "confidence": doc.confidence, "needs_review": False}
                for n, v in doc.fields.items()
            ],
            warnings=[SYNTHETIC_WARNING],
        )
        db.add(document)
        db.flush()
        for name, value in doc.fields.items():
            db.add(
                ProfileField(
                    citizen_id=citizen.id,
                    field_name=name,
                    value=coerce_value(name, value),
                    provenance=Provenance.DOCUMENT_EXTRACTED.value,
                    confidence=doc.confidence,
                    source_document_id=document.id,
                )
            )
    return True


def main() -> int:
    settings = get_settings()
    migrate()
    engine = RuleEngine(rules_dir=settings.rules_dir)
    with get_sessionmaker()() as db:
        result = sync_rules(db, all_rule_versions(engine))
        print(f"rules: {result}")
        for seed in DEMO_CITIZENS:
            created = seed_citizen(db, seed)
            print(f"citizen: {'created' if created else 'exists '} {seed.label}")
        db.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
