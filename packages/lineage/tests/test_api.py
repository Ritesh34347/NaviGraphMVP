"""Unit tests for `navigraph_lineage.api`, DB-free.

`api.record_events` uses `sqlalchemy.dialects.postgresql.insert(...)
.on_conflict_do_nothing(...)`, a Postgres-specific construct (like
`navigraph_catalog.models`' `JSONB`/`gen_random_uuid()`) that doesn't work
against an in-memory SQLite engine. Rather than requiring a live Postgres
connection for this unit tier, these tests mock `Session` to verify the
correct arithmetic/query shape -- matching
`navigraph_catalog/tests/test_api.py`'s identical DB-free convention. The
real idempotent-insert-against-a-live-database behavior is exercised by
`tests/integration/lineage_pipeline/`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from navigraph_lineage.api import get_trace, record_events
from navigraph_shared.contracts import LineageEvent


def _event(event_id: str, agent_name: str = "understanding.conversation") -> LineageEvent:
    return LineageEvent(
        event_id=event_id,
        agent_name=agent_name,
        timestamp=datetime(2026, 7, 29, tzinfo=timezone.utc),
        input_summary="input",
        output_summary="output",
        tenant_id="navikenz-poc",
        trace_id="trace-1",
    )


class TestRecordEvents:
    """`record_events` counts newly-inserted rows via the statement's
    `RETURNING event_id` output (`result.scalars().all()`), not
    `result.rowcount` -- a real bug found live against Postgres: SQLAlchemy
    2.0's "insertmanyvalues" batching strategy for a multi-row
    `.values(list_of_dicts)` insert makes `rowcount` unreliable (observed
    returning `-1` for a real, single-event insert). See `api.py`'s
    `record_events` docstring for the full story."""

    def test_empty_list_returns_zero_zero_without_calling_session(self) -> None:
        session = MagicMock()

        result = record_events(session, events=[])

        assert result.recorded_count == 0
        assert result.duplicate_count == 0
        session.execute.assert_not_called()

    def test_all_new_events_are_fully_recorded(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = [
            "e1",
            "e2",
            "e3",
        ]
        events = [_event("e1"), _event("e2"), _event("e3")]

        result = record_events(session, events=events)

        assert result.recorded_count == 3
        assert result.duplicate_count == 0
        session.execute.assert_called_once()
        session.flush.assert_called_once()

    def test_partial_duplicates_are_reported_correctly(self) -> None:
        session = MagicMock()
        # 3 events submitted, only 1 actually inserted (2 already present,
        # so ON CONFLICT DO NOTHING skips them and RETURNING never lists them).
        session.execute.return_value.scalars.return_value.all.return_value = ["e1"]
        events = [_event("e1"), _event("e2"), _event("e3")]

        result = record_events(session, events=events)

        assert result.recorded_count == 1
        assert result.duplicate_count == 2

    def test_all_duplicates_reports_zero_recorded(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalars.return_value.all.return_value = []
        events = [_event("e1"), _event("e2")]

        result = record_events(session, events=events)

        assert result.recorded_count == 0
        assert result.duplicate_count == 2


class TestGetTrace:
    def test_returns_scalars_from_the_executed_query(self) -> None:
        session = MagicMock()
        fake_records = [MagicMock(), MagicMock()]
        session.execute.return_value.scalars.return_value = fake_records

        result = get_trace(session, trace_id="trace-1", tenant_id="navikenz-poc")

        assert result == fake_records
        session.execute.assert_called_once()

    def test_empty_trace_returns_empty_list(self) -> None:
        session = MagicMock()
        session.execute.return_value.scalars.return_value = []

        result = get_trace(session, trace_id="unknown-trace", tenant_id="navikenz-poc")

        assert result == []
