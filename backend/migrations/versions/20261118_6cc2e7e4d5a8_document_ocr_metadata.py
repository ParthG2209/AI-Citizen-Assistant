"""Document OCR metadata: classification confidence, page count, missing fields, wider engine name,
one document per (citizen, sha256) so duplicate uploads return the existing document.

Revision ID: 6cc2e7e4d5a8
Revises: 414654505a8d
Create Date: 2026-11-18 16:30:45.779363
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6cc2e7e4d5a8"
down_revision: str | None = "414654505a8d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("classification_confidence", sa.Float(), nullable=True))
    op.add_column("documents", sa.Column("page_count", sa.Integer(), nullable=True))
    op.add_column(
        "documents",
        sa.Column("missing_fields", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
    )
    op.alter_column(
        "documents", "ocr_engine", existing_type=sa.VARCHAR(length=64), type_=sa.String(length=128), existing_nullable=True
    )
    op.create_check_constraint(
        op.f("ck_documents_classification_confidence_range"),
        "documents",
        "classification_confidence IS NULL OR (classification_confidence >= 0 AND classification_confidence <= 1)",
    )
    op.create_check_constraint(
        op.f("ck_documents_page_count_non_negative"), "documents", "page_count IS NULL OR page_count >= 0"
    )
    op.drop_index(op.f("ix_documents_sha256"), table_name="documents")
    op.create_unique_constraint("uq_documents_citizen_sha256", "documents", ["citizen_id", "sha256"])


def downgrade() -> None:
    op.drop_constraint("uq_documents_citizen_sha256", "documents", type_="unique")
    op.create_index(op.f("ix_documents_sha256"), "documents", ["sha256"], unique=False)
    op.drop_constraint(op.f("ck_documents_page_count_non_negative"), "documents", type_="check")
    op.drop_constraint(op.f("ck_documents_classification_confidence_range"), "documents", type_="check")
    op.alter_column(
        "documents", "ocr_engine", existing_type=sa.String(length=128), type_=sa.VARCHAR(length=64), existing_nullable=True
    )
    op.drop_column("documents", "missing_fields")
    op.drop_column("documents", "page_count")
    op.drop_column("documents", "classification_confidence")
