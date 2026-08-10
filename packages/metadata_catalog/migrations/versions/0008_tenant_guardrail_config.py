"""tenant guardrail config

Creates `tenant_guardrail_configs` -- Phase 5 of the configurable-
platform build plan: per-tenant overrides for
`QueryCostEstimatorAgent`'s row-limit thresholds
(`role_row_limits`/`default_role_row_limit`/`max_rows_cap`), all
nullable and additive-only. At most one row per tenant -- a real UNIQUE
constraint, not a partial-unique-index/versioning scheme like
`semantic_models`', mirroring `tenant_identity_configs`' identical shape.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-10

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_guardrail_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("role_row_limits", postgresql.JSONB(), nullable=True),
        sa.Column("default_role_row_limit", sa.Integer(), nullable=True),
        sa.Column("max_rows_cap", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_guardrail_configs_tenant_id"),
    )
    op.create_index(
        op.f("ix_tenant_guardrail_configs_tenant_id"),
        "tenant_guardrail_configs",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_tenant_guardrail_configs_tenant_id"), table_name="tenant_guardrail_configs"
    )
    op.drop_table("tenant_guardrail_configs")
