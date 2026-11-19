from __future__ import annotations

from pathlib import Path

import app.models  # noqa: F401  (register tables)
import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from app.database import Base
from sqlalchemy import create_engine, inspect

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"

pytestmark = pytest.mark.integration


def test_database_is_at_head(database):
    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    engine = create_engine(database)
    try:
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
    assert current == script.get_current_head()


def test_migrations_match_models(database):
    """Autogenerate against the migrated database must find nothing to change."""
    engine = create_engine(database)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": True})
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert diff == []


def test_expected_tables_and_constraints(database):
    engine = create_engine(database)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        checks = {c["name"] for c in inspector.get_check_constraints("profile_fields")}
        uniques = {c["name"] for c in inspector.get_unique_constraints("profile_fields")}
        foreign_keys = {
            fk["referred_table"]: fk["options"] for fk in inspector.get_foreign_keys("profile_fields")
        }
    finally:
        engine.dispose()
    assert tables >= {
        "citizens",
        "profile_fields",
        "documents",
        "schemes",
        "scheme_rule_versions",
        "eligibility_evaluations",
        "condition_results",
        "benefit_enrollments",
        "audit_log",
    }
    assert {"ck_profile_fields_provenance_valid", "ck_profile_fields_confidence_range"} <= checks
    assert "uq_profile_fields_citizen_field" in uniques
    assert foreign_keys["citizens"]["ondelete"] == "CASCADE"
    assert foreign_keys["documents"]["ondelete"] == "SET NULL"
