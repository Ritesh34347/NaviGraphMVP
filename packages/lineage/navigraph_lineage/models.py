"""SQLAlchemy 2.0 declarative model for the lineage store.

One table, `lineage_events`, storing exactly the fields
`navigraph_shared.contracts.agent_io.LineageEvent` already defines --
`event_id` (a real `"lineage_{uuid4().hex}"` string every agent already
generates) is used directly as the primary key, not a synthetic surrogate.
This makes idempotent re-insertion a real, enforced DB property
(`INSERT ... ON CONFLICT (event_id) DO NOTHING`, see `api.record_events`)
rather than an application-level convention.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for every lineage-store table."""


class LineageEventRecord(Base):
    """One persisted `LineageEvent`, exactly as an agent emitted it."""

    __tablename__ = "lineage_events"
    __table_args__ = (
        # The real read query is always "give me the whole chain for this
        # (tenant, trace), in order" -- tenant_id is part of the composite
        # index (not a separate one) because every real lookup is
        # tenant-scoped, matching this codebase's row-level tenant-isolation
        # discipline everywhere else.
        Index("ix_lineage_events_tenant_trace", "tenant_id", "trace_id", "timestamp"),
    )

    event_id: Mapped[str] = mapped_column(primary_key=True)
    agent_name: Mapped[str] = mapped_column(nullable=False)
    timestamp: Mapped[datetime] = mapped_column(nullable=False)
    input_summary: Mapped[str] = mapped_column(nullable=False)
    output_summary: Mapped[str] = mapped_column(nullable=False)
    tenant_id: Mapped[str] = mapped_column(nullable=False)
    trace_id: Mapped[str] = mapped_column(nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(server_default=func.now())
