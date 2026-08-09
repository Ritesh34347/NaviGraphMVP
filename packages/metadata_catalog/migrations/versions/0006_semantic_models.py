"""semantic models

Creates `semantic_models` -- Phase 1 of the configurable-platform build
plan: a per-tenant, versioned, persisted
`navigraph_semantic_model.contracts.SemanticModel`, replacing
`navigraph_kg.ontology.RELATIONSHIP_CONCEPTS`'s hardcoded list as the
source of truth `navigraph_kg.ingestion.pipeline._sync_relationship_concepts`
reads from once a tenant has one activated.

Each row is one immutable version of a tenant's compiled semantic model
(`compiled_json`, that model's own `model_dump()`); `activated_at` marks
the one version currently live for that tenant, enforced via a partial
unique index mirroring `data_sources.uq_data_sources_tenant_default`'s
pattern exactly -- at most one activated row per tenant, any number of
inactive (draft/superseded) versions allowed.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-10

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "semantic_models",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("compiled_json", postgresql.JSONB(), nullable=False),
        sa.Column("activated_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", "version", name="uq_semantic_models_tenant_version"),
    )
    op.create_index(
        op.f("ix_semantic_models_tenant_id"), "semantic_models", ["tenant_id"], unique=False
    )
    op.create_index(
        "uq_semantic_models_tenant_active",
        "semantic_models",
        ["tenant_id"],
        unique=True,
        postgresql_where=sa.text("activated_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_semantic_models_tenant_active", table_name="semantic_models")
    op.drop_index(op.f("ix_semantic_models_tenant_id"), table_name="semantic_models")
    op.drop_table("semantic_models")
