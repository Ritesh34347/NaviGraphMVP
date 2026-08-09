"""Integration test: real lineage search (`list_traces`) against a live
Postgres.

REQUIRES A LIVE, REACHABLE POSTGRES -- mirrors
`test_pipeline_chain.py`'s identical stance: this test does NOT skip
gracefully if Postgres is unreachable, since `tests/integration/` runs
against the real docker-compose stack in a separate CI job. Assumes the
lineage store's migrations are already applied (same assumption
`test_pipeline_chain.py` makes).

Writes real `LineageEvent`s directly via `record_events` (no agent
pipeline needed to prove search/aggregation behavior -- that's
`test_pipeline_chain.py`'s job), then proves `list_traces`' real
Postgres-side aggregation/filtering: per-trace grouping, `agent_name`
filtering, text search, and pagination/ordering, against actual rows in
the actual table.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from navigraph_lineage.api import list_traces, record_events
from navigraph_lineage.db import get_engine, get_session_factory, session_scope
from navigraph_lineage.settings import LineageSettings
from navigraph_shared.contracts import LineageEvent

pytestmark = pytest.mark.postgres_integration

_TENANT_ID = f"lineage-search-test-{uuid.uuid4().hex[:8]}"


def _event(
    *, trace_id: str, agent_name: str, minutes_ago: int, input_summary: str, output_summary: str
) -> LineageEvent:
    return LineageEvent(
        event_id=f"lineage_{uuid.uuid4().hex}",
        agent_name=agent_name,
        timestamp=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        input_summary=input_summary,
        output_summary=output_summary,
        tenant_id=_TENANT_ID,
        trace_id=trace_id,
    )


def test_list_traces_groups_filters_and_orders_real_rows() -> None:
    settings = LineageSettings()
    session_factory = get_session_factory(get_engine(settings))

    trace_revenue = f"trace-revenue-{uuid.uuid4().hex}"
    trace_churn = f"trace-churn-{uuid.uuid4().hex}"

    with session_scope(session_factory) as session:
        record_events(
            session,
            events=[
                _event(
                    trace_id=trace_revenue,
                    agent_name="understanding.conversation",
                    minutes_ago=10,
                    input_summary="question=what is our revenue",
                    output_summary="resolved_question=what is our revenue",
                ),
                _event(
                    trace_id=trace_revenue,
                    agent_name="query.sql_generation",
                    minutes_ago=9,
                    input_summary="tables=STAGING_TRANSACTIONS",
                    output_summary="sql=SELECT SUM(UNITS) FROM STAGING_TRANSACTIONS",
                ),
                _event(
                    trace_id=trace_churn,
                    agent_name="understanding.conversation",
                    minutes_ago=5,
                    input_summary="question=what is our churn rate",
                    output_summary="resolved_question=what is our churn rate",
                ),
            ],
        )

    with session_scope(session_factory) as session:
        # --- Unfiltered: both traces, most-recently-active first. ---
        all_traces = {
            summary.trace_id: summary
            for summary in list_traces(session, tenant_id=_TENANT_ID)
        }
        assert set(all_traces) == {trace_revenue, trace_churn}

        revenue_summary = all_traces[trace_revenue]
        assert revenue_summary.event_count == 2
        assert revenue_summary.agent_names == ["query.sql_generation", "understanding.conversation"]
        assert revenue_summary.first_event_at < revenue_summary.last_event_at

        churn_summary = all_traces[trace_churn]
        assert churn_summary.event_count == 1
        assert churn_summary.agent_names == ["understanding.conversation"]

        # churn is more recent (5 min ago) than revenue (10/9 min ago) ->
        # ordered first by list_traces' own "most-recently-active" order.
        ordered = list_traces(session, tenant_id=_TENANT_ID)
        assert [s.trace_id for s in ordered] == [trace_churn, trace_revenue]

        # --- agent_name filter: only the trace with a real
        # query.sql_generation event. ---
        sql_gen_traces = list_traces(
            session, tenant_id=_TENANT_ID, agent_name="query.sql_generation"
        )
        assert [s.trace_id for s in sql_gen_traces] == [trace_revenue]

        # --- text search: only the trace whose summaries mention "churn". ---
        churn_search = list_traces(session, tenant_id=_TENANT_ID, search_text="churn")
        assert [s.trace_id for s in churn_search] == [trace_churn]

        # --- pagination: limit=1 returns just the most recent. ---
        first_page = list_traces(session, tenant_id=_TENANT_ID, limit=1, offset=0)
        assert [s.trace_id for s in first_page] == [trace_churn]
        second_page = list_traces(session, tenant_id=_TENANT_ID, limit=1, offset=1)
        assert [s.trace_id for s in second_page] == [trace_revenue]

        # --- a different tenant sees nothing (real row-level isolation). ---
        other_tenant = list_traces(session, tenant_id="a-completely-different-tenant")
        assert all(s.trace_id not in (trace_revenue, trace_churn) for s in other_tenant)
