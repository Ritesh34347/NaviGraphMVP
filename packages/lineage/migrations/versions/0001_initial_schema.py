"""initial schema

Creates the one `lineage_events` table -- matches `navigraph_lineage.models`
exactly (same columns, types, primary key, index).

Revision ID: 0001
Revises:
Create Date: 2026-07-29

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lineage_events",
        sa.Column("event_id", sa.String(), primary_key=True, nullable=False),
        sa.Column("agent_name", sa.String(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("input_summary", sa.String(), nullable=False),
        sa.Column("output_summary", sa.String(), nullable=False),
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("trace_id", sa.String(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_lineage_events_tenant_trace"),
        "lineage_events",
        ["tenant_id", "trace_id", "timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_lineage_events_tenant_trace"), table_name="lineage_events")
    op.drop_table("lineage_events")
