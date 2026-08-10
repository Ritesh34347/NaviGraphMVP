"""tenant identity config

Creates `tenant_identity_configs` -- Phase 4 of the configurable-platform
build plan: which identity-verification provider (`provider_type`, e.g.
`"azure_ad"`/`"oidc"`) a tenant uses, and that provider's own real,
non-secret settings (`provider_settings`, e.g. an Azure AD tenant/client
ID pair or an OIDC issuer/audience). At most one row per tenant -- a real
UNIQUE constraint, not a partial-unique-index/versioning scheme like
`semantic_models`', since there's no "previous provider" a rollback would
ever want to reactivate.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-10

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tenant_identity_configs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("provider_type", sa.String(), nullable=False),
        sa.Column("provider_settings", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("tenant_id", name="uq_tenant_identity_configs_tenant_id"),
    )
    op.create_index(
        op.f("ix_tenant_identity_configs_tenant_id"),
        "tenant_identity_configs",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_tenant_identity_configs_tenant_id"), table_name="tenant_identity_configs"
    )
    op.drop_table("tenant_identity_configs")
