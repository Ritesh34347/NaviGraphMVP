"""catalog column pii flag

Adds `is_pii` to `catalog_columns` -- Phase 6 (Guardrail domain)'s PII
Exposure Checker agent needs a per-column PII flag it can check directly
against the live Postgres catalog. Defaults false for every existing row;
real values are set by a deliberate, human-run backfill
(tools/scripts/tag_pii_columns.py), never inferred by this migration.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-29

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "catalog_columns",
        sa.Column("is_pii", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("catalog_columns", "is_pii")
