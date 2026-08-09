"""Real unit tests for the Data Federation agent, DB-free and network-free.

Mirrors `navigraph_agents.understanding.metadata_discovery.tests.test_agent`'s
"mock the session/lookup layer, assert on shape" convention:
`navigraph_catalog.api.list_data_sources` and `navigraph_catalog.db
.session_scope` are patched at the point they're imported into `agent.py`,
and `DataFederationAgent._get_data_source` / `build_connector` are
patched directly so tests never need a real Postgres row or a real
connector.

`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from navigraph_connectors.base import QueryResult
from navigraph_shared.contracts import RequestContext

from navigraph_agents.query.data_federation.agent import DataFederationAgent
from navigraph_agents.query.data_federation.contracts import (
    DataFederationInput,
    DataFederationPayload,
    ExecutionPlan,
)

_AGENT_MODULE = "navigraph_agents.query.data_federation.agent"

_SOURCE_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_SOURCE_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _request_context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-acme",
        user_id="user-1",
        trace_id="trace-1",
        roles=["analyst"],
    )


def _plan(
    data_source_id: str,
    *,
    route: str = "direct_connector",
    sql: str = "SELECT customer_id, amount FROM SALES.REVENUE",
) -> ExecutionPlan:
    return ExecutionPlan(
        data_source_id=data_source_id,
        route=route,  # type: ignore[arg-type]
        sql=sql,
        params={},
        timeout_seconds=30,
        max_rows=1000,
        read_only_verified=True,
    )


@contextmanager
def _fake_session_scope(session_factory):
    yield MagicMock()


class _FakeConnector:
    """A fake `Connector`-shaped object exposing only `execute_query`, the
    one method `DataFederationAgent` actually calls."""

    def __init__(self, query_result: QueryResult | None = None, error: Exception | None = None):
        self._query_result = query_result
        self._error = error

    def execute_query(self, sql: str, params: dict | None = None) -> QueryResult:
        if self._error is not None:
            raise self._error
        assert self._query_result is not None
        return self._query_result


def _agent() -> DataFederationAgent:
    return DataFederationAgent(catalog_session_factory=MagicMock())


async def test_single_source_direct_connector_success() -> None:
    query_result = QueryResult(
        columns=["customer_id", "amount"],
        rows=[{"customer_id": 1, "amount": 100}, {"customer_id": 2, "amount": 200}],
        row_count=2,
    )

    agent = _agent()
    input_ = DataFederationInput(
        request_context=_request_context(),
        payload=DataFederationPayload(plans=[_plan(_SOURCE_A)]),
    )

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch.object(
            DataFederationAgent,
            "_get_data_source",
            staticmethod(lambda session, *, data_source_id, tenant_id: SimpleNamespace(
                source_type="fake_source", connection_ref={"secret_scope": "fake"}
            )),
        ),
        patch(
            f"{_AGENT_MODULE}.build_connector",
            return_value=_FakeConnector(query_result=query_result),
        ),
    ):
        output = await agent.run(input_)

    assert output.errors == []
    assert output.confidence == 1.0
    assert output.result.federated is False
    assert len(output.result.per_source_results) == 1

    source_result = output.result.per_source_results[0]
    assert source_result.data_source_id == _SOURCE_A
    assert source_result.route_used == "direct_connector"
    assert source_result.columns == ["customer_id", "amount"]
    assert source_result.row_count == 2
    assert source_result.execution_latency_ms >= 0

    assert output.result.final_columns == ["customer_id", "amount"]
    assert output.result.final_rows == [
        {"customer_id": 1, "amount": 100},
        {"customer_id": 2, "amount": 200},
    ]
    assert output.result.final_row_count == 2

    assert len(output.lineage_events) == 1
    assert output.lineage_events[0].agent_name == "query.data_federation"
    assert output.metadata.latency_ms >= 0


async def test_execute_query_failure_becomes_non_recoverable_error_not_a_crash() -> None:
    """The only plan's `execute_query` raises -- must not propagate as a
    Python exception out of `run()`; instead a non-recoverable `AgentError`
    is recorded and the result is empty."""

    agent = _agent()
    input_ = DataFederationInput(
        request_context=_request_context(),
        payload=DataFederationPayload(plans=[_plan(_SOURCE_A)]),
    )

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch.object(
            DataFederationAgent,
            "_get_data_source",
            staticmethod(lambda session, *, data_source_id, tenant_id: SimpleNamespace(
                source_type="fake_source", connection_ref={"secret_scope": "fake"}
            )),
        ),
        patch(
            f"{_AGENT_MODULE}.build_connector",
            return_value=_FakeConnector(error=RuntimeError("warehouse suspended")),
        ),
    ):
        output = await agent.run(input_)

    assert output.result.per_source_results == []
    assert output.result.final_rows == []
    assert output.result.final_row_count == 0
    assert output.result.federated is False
    assert output.confidence == 0.0

    assert len(output.errors) == 1
    assert output.errors[0].code == "query_execution_failed"
    assert output.errors[0].recoverable is False
    assert "warehouse suspended" in output.errors[0].message

    # Lineage and metadata are still produced even on the all-failed path.
    assert len(output.lineage_events) == 1
    assert output.metadata.latency_ms >= 0


async def test_partial_failure_across_multiple_plans_yields_confidence_half() -> None:
    ok_result = QueryResult(columns=["id"], rows=[{"id": 1}], row_count=1)

    def _connector_for(source_type: str, *, connection_ref, secrets):
        if source_type == "fake_ok":
            return _FakeConnector(query_result=ok_result)
        return _FakeConnector(error=RuntimeError("network unreachable"))

    def _fake_get_data_source(session, *, data_source_id, tenant_id):
        source_type = "fake_ok" if data_source_id == _SOURCE_A else "fake_broken"
        return SimpleNamespace(
            source_type=source_type, connection_ref={"secret_scope": source_type}
        )

    agent = _agent()
    input_ = DataFederationInput(
        request_context=_request_context(),
        payload=DataFederationPayload(plans=[_plan(_SOURCE_A), _plan(_SOURCE_B)]),
    )

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch.object(
            DataFederationAgent, "_get_data_source", staticmethod(_fake_get_data_source)
        ),
        patch(f"{_AGENT_MODULE}.build_connector", side_effect=_connector_for),
    ):
        output = await agent.run(input_)

    assert output.confidence == 0.5
    assert len(output.result.per_source_results) == 1
    assert output.result.per_source_results[0].data_source_id == _SOURCE_A
    assert len(output.errors) == 1
    assert output.errors[0].code == "query_execution_failed"


async def test_multiple_sources_combined_via_shared_join_key() -> None:
    """Two distinct, successfully-queried sources get combined -- a real
    exercise of `_combine_results`'s in-memory join path, using two FAKE
    per-source results (see that method's docstring for what is genuinely
    real here vs. structurally-present-but-unexercised against a live
    second source)."""

    result_a = QueryResult(
        columns=["customer_id", "amount_a"],
        rows=[{"customer_id": 1, "amount_a": 100}],
        row_count=1,
    )
    result_b = QueryResult(
        columns=["customer_id", "amount_b"],
        rows=[
            {"customer_id": 1, "amount_b": 50},
            {"customer_id": 2, "amount_b": 75},
        ],
        row_count=2,
    )

    def _connector_for(source_type: str, *, connection_ref, secrets):
        if source_type == "fake_a":
            return _FakeConnector(query_result=result_a)
        return _FakeConnector(query_result=result_b)

    def _fake_get_data_source(session, *, data_source_id, tenant_id):
        source_type = "fake_a" if data_source_id == _SOURCE_A else "fake_b"
        return SimpleNamespace(
            source_type=source_type, connection_ref={"secret_scope": source_type}
        )

    agent = _agent()
    input_ = DataFederationInput(
        request_context=_request_context(),
        payload=DataFederationPayload(plans=[_plan(_SOURCE_A), _plan(_SOURCE_B)]),
    )

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch.object(
            DataFederationAgent, "_get_data_source", staticmethod(_fake_get_data_source)
        ),
        patch(f"{_AGENT_MODULE}.build_connector", side_effect=_connector_for),
    ):
        output = await agent.run(input_)

    assert output.errors == []
    assert output.confidence == 1.0
    assert output.result.federated is True
    assert len(output.result.per_source_results) == 2

    assert set(output.result.final_columns) == {
        "customer_id",
        f"{_SOURCE_A}.amount_a",
        f"{_SOURCE_B}.amount_b",
    }

    rows_by_customer = {row["customer_id"]: row for row in output.result.final_rows}
    assert rows_by_customer[1][f"{_SOURCE_A}.amount_a"] == 100
    assert rows_by_customer[1][f"{_SOURCE_B}.amount_b"] == 50
    # customer_id=2 only ever appeared in source B's result -- its merged
    # row correctly has no `amount_a` entry at all (there is nothing to
    # merge it with), rather than a fabricated null.
    assert f"{_SOURCE_A}.amount_a" not in rows_by_customer[2]
    assert rows_by_customer[2][f"{_SOURCE_B}.amount_b"] == 75


async def test_combine_results_falls_back_to_union_when_no_shared_columns() -> None:
    result_a = QueryResult(columns=["a_col"], rows=[{"a_col": 1}], row_count=1)
    result_b = QueryResult(columns=["b_col"], rows=[{"b_col": 2}], row_count=1)

    def _connector_for(source_type: str, *, connection_ref, secrets):
        if source_type == "fake_a":
            return _FakeConnector(query_result=result_a)
        return _FakeConnector(query_result=result_b)

    def _fake_get_data_source(session, *, data_source_id, tenant_id):
        source_type = "fake_a" if data_source_id == _SOURCE_A else "fake_b"
        return SimpleNamespace(
            source_type=source_type, connection_ref={"secret_scope": source_type}
        )

    agent = _agent()
    input_ = DataFederationInput(
        request_context=_request_context(),
        payload=DataFederationPayload(plans=[_plan(_SOURCE_A), _plan(_SOURCE_B)]),
    )

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch.object(
            DataFederationAgent, "_get_data_source", staticmethod(_fake_get_data_source)
        ),
        patch(f"{_AGENT_MODULE}.build_connector", side_effect=_connector_for),
    ):
        output = await agent.run(input_)

    assert output.result.federated is True
    assert set(output.result.final_columns) == {"a_col", "b_col"}
    assert output.result.final_rows == [{"a_col": 1}, {"b_col": 2}]
