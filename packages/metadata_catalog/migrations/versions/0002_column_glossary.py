"""column glossary

Creates the `column_glossary` table -- a business-glossary enrichment layer
on top of `catalog_columns` (business name, synonyms, description, source),
matching `navigraph_catalog.models.ColumnGlossary` exactly (same columns,
types, constraints, unique FK to `catalog_columns.id` with
ondelete="CASCADE").

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-29

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "column_glossary",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("column_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_name", sa.String(), nullable=False),
        sa.Column("synonyms", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["column_id"], ["catalog_columns.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("column_id", name="uq_column_glossary_column_id"),
    )


def downgrade() -> None:
    op.drop_table("column_glossary")
