"""Evaluation freshness keys: profile fingerprint and rule-set hash on every evaluation row.

Existing rows keep NULL (their inputs cannot be reconstructed) and are therefore reported as stale.

Revision ID: 9b1f3c2d7e4a
Revises: 6cc2e7e4d5a8
Create Date: 2026-11-18 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9b1f3c2d7e4a"
down_revision: str | None = "6cc2e7e4d5a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("eligibility_evaluations", sa.Column("profile_fingerprint", sa.String(length=64), nullable=True))
    op.add_column("eligibility_evaluations", sa.Column("rule_set_hash", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("eligibility_evaluations", "rule_set_hash")
    op.drop_column("eligibility_evaluations", "profile_fingerprint")
