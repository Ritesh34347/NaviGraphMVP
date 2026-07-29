"""Real lineage-store operations: record events, read a trace back.

Every function here takes an already-open `Session` (dependency injection --
see `navigraph_lineage.db.session_scope` for how callers obtain one) rather
than creating its own, matching `navigraph_catalog.api`'s identical
convention. Functions `flush` where needed but never `commit` -- that is the
caller's `session_scope`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass

from navigraph_shared.contracts import LineageEvent
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from navigraph_lineage.models import LineageEventRecord


@dataclass(frozen=True)
class RecordEventsResult:
    """How many of the events passed to `record_events` were newly
    inserted vs. already present (idempotent no-ops, matched by
    `event_id`)."""

    recorded_count: int
    duplicate_count: int


def record_events(session: Session, *, events: list[LineageEvent]) -> RecordEventsResult:
    """Idempotently insert `events`.

    Uses a single `INSERT ... ON CONFLICT (event_id) DO NOTHING ...
    RETURNING event_id` statement for the whole batch. A conflicting row is
    skipped by `ON CONFLICT DO NOTHING` and never appears in the `RETURNING`
    output, so counting the returned rows is a real, verifiable count of
    exactly how many were newly inserted, straight from the database.

    REAL BUG FOUND AND FIXED while building this: the first version of this
    function used `result.rowcount` instead of `RETURNING`, assuming
    Postgres reports the number of rows actually inserted by a bulk
    `INSERT ... ON CONFLICT DO NOTHING`. It does not, for this exact shape:
    SQLAlchemy 2.0 compiles a multi-row `.values(list_of_dicts)` insert
    using its "insertmanyvalues" batching strategy, and `rowcount` for that
    strategy is documented as unreliable/driver-dependent -- caught live via
    `tests/integration/lineage_pipeline/`, which asserted a real recorded
    count and got `rowcount=-1` (Postgres/psycopg's own "count not
    available" sentinel) instead of the correct `1`. `RETURNING` is not
    subject to that ambiguity: every row that actually lands in the table
    is unconditionally listed.
    """

    if not events:
        return RecordEventsResult(recorded_count=0, duplicate_count=0)

    values = [
        {
            "event_id": event.event_id,
            "agent_name": event.agent_name,
            "timestamp": event.timestamp,
            "input_summary": event.input_summary,
            "output_summary": event.output_summary,
            "tenant_id": event.tenant_id,
            "trace_id": event.trace_id,
        }
        for event in events
    ]

    statement = (
        insert(LineageEventRecord)
        .values(values)
        .on_conflict_do_nothing(index_elements=["event_id"])
        .returning(LineageEventRecord.event_id)
    )
    result = session.execute(statement)
    session.flush()

    recorded_count = len(result.scalars().all())
    duplicate_count = len(events) - recorded_count
    return RecordEventsResult(recorded_count=recorded_count, duplicate_count=duplicate_count)


def get_trace(
    session: Session, *, trace_id: str, tenant_id: str
) -> list[LineageEventRecord]:
    """Every `LineageEventRecord` for `(tenant_id, trace_id)`, ordered by
    `timestamp` then `event_id` (a stable tiebreak for same-millisecond
    events).

    `tenant_id` is required, not optional -- a bare `trace_id`-only lookup
    would let one tenant's caller fetch another tenant's trace if trace_ids
    ever collide or are guessed, mirroring every other tenant-scoped read in
    this codebase (`navigraph_catalog.api.list_data_sources(session, *,
    tenant_id=...)`).
    """

    return list(
        session.execute(
            select(LineageEventRecord)
            .where(
                LineageEventRecord.tenant_id == tenant_id,
                LineageEventRecord.trace_id == trace_id,
            )
            .order_by(LineageEventRecord.timestamp, LineageEventRecord.event_id)
        ).scalars()
    )
