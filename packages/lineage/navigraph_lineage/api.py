"""Real lineage-store operations: record events, read a trace back, search
across traces.

Every function here takes an already-open `Session` (dependency injection --
see `navigraph_lineage.db.session_scope` for how callers obtain one) rather
than creating its own, matching `navigraph_catalog.api`'s identical
convention. Functions `flush` where needed but never `commit` -- that is the
caller's `session_scope`'s job.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from navigraph_shared.contracts import LineageEvent
from sqlalchemy import distinct, func, or_, select
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


@dataclass(frozen=True)
class TraceSummary:
    """One trace's aggregate shape -- what a search RESULT LIST shows,
    before a caller drills into the full chain with `get_trace`. Real
    aggregates computed by the database (`MIN`/`MAX`/`COUNT`/`ARRAY_AGG`),
    not assembled in Python from a full row fetch -- `list_traces` never
    loads a whole trace's events just to summarize it."""

    trace_id: str
    tenant_id: str
    first_event_at: datetime
    last_event_at: datetime
    event_count: int
    agent_names: list[str]


def list_traces(
    session: Session,
    *,
    tenant_id: str,
    agent_name: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    search_text: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[TraceSummary]:
    """Search this tenant's traces, most-recently-active first.

    Every filter narrows which individual EVENTS are considered before
    grouping into per-trace summaries -- e.g. `agent_name="query.sql_generation"`
    returns every trace that has at least one event from that agent (not
    every event of that trace), matching how a real operator investigating
    "which conversations touched SQL Generation" actually wants this to
    behave. `search_text` does a real substring match (case-insensitive)
    against `input_summary`/`output_summary`, the same free-text fields
    every agent already writes a real, human-readable summary into.

    `tenant_id` is required, not optional -- see `get_trace`'s identical
    rationale; a global, tenant-unscoped search would be a real isolation
    violation for what is otherwise this codebase's most sensitive read
    surface (every question asked, across every conversation).
    """

    conditions = [LineageEventRecord.tenant_id == tenant_id]
    if agent_name is not None:
        conditions.append(LineageEventRecord.agent_name == agent_name)
    if since is not None:
        conditions.append(LineageEventRecord.timestamp >= since)
    if until is not None:
        conditions.append(LineageEventRecord.timestamp <= until)
    if search_text is not None:
        pattern = f"%{search_text}%"
        conditions.append(
            or_(
                LineageEventRecord.input_summary.ilike(pattern),
                LineageEventRecord.output_summary.ilike(pattern),
            )
        )

    statement = (
        select(
            LineageEventRecord.trace_id,
            LineageEventRecord.tenant_id,
            func.min(LineageEventRecord.timestamp).label("first_event_at"),
            func.max(LineageEventRecord.timestamp).label("last_event_at"),
            func.count().label("event_count"),
            func.array_agg(distinct(LineageEventRecord.agent_name)).label("agent_names"),
        )
        .where(*conditions)
        .group_by(LineageEventRecord.trace_id, LineageEventRecord.tenant_id)
        .order_by(func.max(LineageEventRecord.timestamp).desc())
        .limit(limit)
        .offset(offset)
    )

    rows = session.execute(statement).all()
    return [
        TraceSummary(
            trace_id=row.trace_id,
            tenant_id=row.tenant_id,
            first_event_at=row.first_event_at,
            last_event_at=row.last_event_at,
            event_count=row.event_count,
            agent_names=sorted(row.agent_names),
        )
        for row in rows
    ]
