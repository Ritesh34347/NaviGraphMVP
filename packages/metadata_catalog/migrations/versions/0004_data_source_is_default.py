"""data source is_default

Adds `is_default` to `data_sources`, plus a partial unique index enforcing
at most one default `DataSource` per tenant at the DB level. Resolves
LIMITATIONS.md items 26 (two registered data sources for one tenant, with
no resolution order) and 42 (data_source_id auto-resolution requires
exactly one match, no "default" concept) -- the Request Orchestrator's
resolution logic now falls back to a tenant's marked default when more
than one DataSource is registered, instead of only ever succeeding for
exactly one.

Defaults false for every existing row; real values are set deliberately
(via `navigraph_catalog.api.set_default_data_source`), never inferred by
this migration.

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-09

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "data_sources",
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index(
        "uq_data_sources_tenant_default",
        "data_sources",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_data_sources_tenant_default", table_name="data_sources")
    op.drop_column("data_sources", "is_default")
