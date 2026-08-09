"""schema drift tracking

Adds `last_crawled_at` to `data_sources` and `schema_hash` to
`catalog_tables` -- the real signals a re-crawl scheduler needs to answer
"how stale is this data source" and "did this table's structure actually
change since we last crawled it," neither of which existed anywhere in
this catalog before (Phase 13, LIMITATIONS.md item 61's "still open"
re-validation-scheduler bullet).

Both columns are nullable with no backfill: `last_crawled_at IS NULL`
means "never crawled since this column existed" (including every
DataSource registered before this migration), and `schema_hash IS NULL`
means "never hashed since this column existed" -- both are honest,
distinguishable-from-real-values states, not inferred from existing data.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_sources",
        sa.Column("last_crawled_at", sa.DateTime(), nullable=True),
    )
    op.add_column(
        "catalog_tables",
        sa.Column("schema_hash", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("catalog_tables", "schema_hash")
    op.drop_column("data_sources", "last_crawled_at")
