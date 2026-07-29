"""initial schema

Creates the four raw-structure catalog tables: data_sources, catalog_schemas,
catalog_tables, catalog_columns -- matching navigraph_catalog.models exactly
(same columns, types, constraints, FKs with ondelete="CASCADE", unique
constraints).

Revision ID: 0001
Revises:
Create Date: 2026-07-29

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_sources",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("connection_ref", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "name", name="uq_data_sources_tenant_name"),
    )
    op.create_index(
        op.f("ix_data_sources_tenant_id"), "data_sources", ["tenant_id"], unique=False
    )

    op.create_table(
        "catalog_schemas",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("data_source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["data_source_id"], ["data_sources.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "data_source_id", "name", name="uq_catalog_schemas_source_name"
        ),
    )

    op.create_table(
        "catalog_tables",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("schema_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("row_count_estimate", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["schema_id"], ["catalog_schemas.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("schema_id", "name", name="uq_catalog_tables_schema_name"),
    )

    op.create_table(
        "catalog_columns",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("data_type", sa.String(), nullable=False),
        sa.Column("nullable", sa.Boolean(), nullable=False),
        sa.Column("ordinal_position", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["table_id"], ["catalog_tables.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("table_id", "name", name="uq_catalog_columns_table_name"),
    )


def downgrade() -> None:
    # Reverse dependency order: columns -> tables -> schemas -> data_sources.
    op.drop_table("catalog_columns")
    op.drop_table("catalog_tables")
    op.drop_table("catalog_schemas")
    op.drop_index(op.f("ix_data_sources_tenant_id"), table_name="data_sources")
    op.drop_table("data_sources")
