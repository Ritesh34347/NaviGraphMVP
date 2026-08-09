"""Real unit tests for the Data Source Discovery agent, DB-free and
connector-free.

Mirrors `understanding/metadata_discovery/tests/test_agent.py`'s "mock the
session layer, assert on shape" convention: `navigraph_catalog.api.
list_data_sources` / `list_tables` and `navigraph_catalog.db.session_scope`
are patched at the point they're imported into `agent.py`, fed plain
`SimpleNamespace` stand-ins for `DataSource` / `CatalogTable` rows (the
agent only ever reads `.id`, `.source_type`, `.name` off them -- a real ORM
instance isn't needed). `build_connector` is patched the same way,
returning a fake `Connector` instance whose `test_connection()` call count
is asserted directly, so "one real connectivity probe per distinct data
source, not per resolved table" is provably exercised rather than merely
believed.

`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from navigraph_connectors.base import ConnectionTestResult
from navigraph_shared.contracts import RequestContext

from navigraph_agents.query.data_source_discovery.agent import DataSourceDiscoveryAgent
from navigraph_agents.query.data_source_discovery.contracts import (
    DataSourceDiscoveryInput,
    DataSourceDiscoveryPayload,
)

_AGENT_MODULE = "navigraph_agents.query.data_source_discovery.agent"


def _make_input(tables: list[str]) -> DataSourceDiscoveryInput:
    return DataSourceDiscoveryInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=DataSourceDiscoveryPayload(tables=tables),
    )


def _data_source(
    source_id: uuid.UUID, source_type: str = "snowflake", *, is_default: bool = False
) -> SimpleNamespace:
    return SimpleNamespace(
        id=source_id,
        source_type=source_type,
        tenant_id="tenant-acme",
        connection_ref={"secret_scope": f"tenant-acme-{source_id}"},
        is_default=is_default,
    )


def _table(name: str) -> SimpleNamespace:
    return SimpleNamespace(name=name)


@contextmanager
def _fake_session_scope(session_factory):
    yield MagicMock()


class _FakeConnector:
    """Fake `Connector`: records every `test_connection()` call so tests can
    assert the connectivity probe was deduped per distinct data source
    rather than run once per resolved table."""

    call_count = 0
    succeed = True
    message = "ok"

    def test_connection(self) -> ConnectionTestResult:
        type(self).call_count += 1
        return ConnectionTestResult(
            success=type(self).succeed, message=type(self).message, latency_ms=12.5
        )

    def introspect_schema(self):  # pragma: no cover - unused by this agent
        raise NotImplementedError

    def execute_query(self, sql, params=None):  # pragma: no cover - unused by this agent
        raise NotImplementedError

    def capabilities(self):  # pragma: no cover - unused by this agent
        raise NotImplementedError


def _reset_fake_connector(*, succeed: bool = True, message: str = "ok") -> type[_FakeConnector]:
    _FakeConnector.call_count = 0
    _FakeConnector.succeed = succeed
    _FakeConnector.message = message
    return _FakeConnector


async def test_single_table_resolves_to_single_reachable_source() -> None:
    ds_id = uuid.uuid4()
    data_source = _data_source(ds_id)
    connector_cls = _reset_fake_connector(succeed=True)

    agent = DataSourceDiscoveryAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[data_source]),
        patch(f"{_AGENT_MODULE}.list_tables", return_value=[_table("STAGING_TRANSACTIONS")]),
        patch(f"{_AGENT_MODULE}.build_connector", return_value=connector_cls()),
    ):
        # Deliberately lowercase input vs. the uppercase crawled table name,
        # to exercise the case-insensitive match at the same time.
        output = await agent.run(_make_input(["staging_transactions"]))

    assert output.errors == []
    assert output.confidence == 1.0
    assert output.result.unresolved_tables == []
    assert output.result.is_multi_source is False
    assert len(output.result.resolved) == 1

    resolved = output.result.resolved[0]
    assert resolved.table_name == "staging_transactions"
    assert resolved.data_source_id == str(ds_id)
    assert resolved.source_type == "snowflake"
    assert resolved.reachable is True
    assert resolved.connection_test_latency_ms == 12.5
    assert resolved.connection_test_message == "ok"

    assert connector_cls.call_count == 1

    assert len(output.lineage_events) == 1
    lineage = output.lineage_events[0]
    assert lineage.agent_name == "query.data_source_discovery"
    assert lineage.tenant_id == "tenant-acme"
    assert lineage.trace_id == "trace-1"

    assert output.metadata.latency_ms >= 0
    assert output.metadata.model_version is None
    assert output.metadata.prompt_version is None
    assert output.metadata.tokens_input is None
    assert output.metadata.tokens_output is None


async def test_unresolved_table_lands_in_unresolved_tables() -> None:
    ds_id = uuid.uuid4()
    data_source = _data_source(ds_id)
    connector_cls = _reset_fake_connector(succeed=True)

    agent = DataSourceDiscoveryAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[data_source]),
        patch(f"{_AGENT_MODULE}.list_tables", return_value=[_table("ORDERS")]),
        patch(f"{_AGENT_MODULE}.build_connector", return_value=connector_cls()),
    ):
        output = await agent.run(_make_input(["orders", "ghost_table"]))

    assert output.result.unresolved_tables == ["ghost_table"]
    assert len(output.result.resolved) == 1
    assert output.result.resolved[0].table_name == "orders"
    assert output.confidence == 0.5
    assert output.errors == []
    # Only the resolved table's data source should ever be probed.
    assert connector_cls.call_count == 1


async def test_unreachable_data_source_is_non_recoverable_error() -> None:
    """The one deliberately non-recoverable error path in this agent: a
    real, resolved data source that fails its connectivity probe must
    surface as `recoverable=False` and drive `confidence` to 0.0, so a
    caller is forced to notice and halt rather than silently proceeding."""

    ds_id = uuid.uuid4()
    data_source = _data_source(ds_id)
    connector_cls = _reset_fake_connector(succeed=False, message="connection refused")

    agent = DataSourceDiscoveryAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[data_source]),
        patch(f"{_AGENT_MODULE}.list_tables", return_value=[_table("ORDERS")]),
        patch(f"{_AGENT_MODULE}.build_connector", return_value=connector_cls()),
    ):
        output = await agent.run(_make_input(["orders"]))

    assert output.confidence == 0.0
    assert len(output.errors) == 1
    assert output.errors[0].code == "data_source_unreachable"
    assert output.errors[0].recoverable is False

    assert output.result.resolved[0].reachable is False
    assert output.result.resolved[0].connection_test_message == "connection refused"

    # Must not raise -- lineage/metadata are still produced on this path.
    assert len(output.lineage_events) == 1
    assert output.metadata.latency_ms >= 0


async def test_two_tables_two_sources_is_multi_source_with_one_check_each() -> None:
    ds1_id, ds2_id = uuid.uuid4(), uuid.uuid4()
    ds1 = _data_source(ds1_id, source_type="snowflake")
    ds2 = _data_source(ds2_id, source_type="snowflake")

    connector_cls = _reset_fake_connector(succeed=True)

    tables_by_source = {
        ds1_id: [_table("ORDERS")],
        ds2_id: [_table("CUSTOMERS")],
    }

    agent = DataSourceDiscoveryAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[ds1, ds2]),
        patch(
            f"{_AGENT_MODULE}.list_tables",
            side_effect=lambda session, *, data_source_id: tables_by_source[data_source_id],
        ),
        patch(f"{_AGENT_MODULE}.build_connector", return_value=connector_cls()),
    ):
        output = await agent.run(_make_input(["orders", "customers"]))

    assert output.result.is_multi_source is True
    assert output.result.unresolved_tables == []
    assert len(output.result.resolved) == 2
    assert {r.data_source_id for r in output.result.resolved} == {str(ds1_id), str(ds2_id)}

    # Exactly one real connectivity probe per distinct data source, not one
    # per requested table -- proves the per-`run()` cache actually dedupes.
    assert connector_cls.call_count == 2

    assert output.errors == []
    assert output.confidence == 1.0


async def test_catalog_lookup_failure_is_handled_gracefully() -> None:
    """Must not raise: a DB-side failure becomes a non-recoverable error and
    every requested table is treated as unresolved."""

    agent = DataSourceDiscoveryAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(
            f"{_AGENT_MODULE}.list_data_sources",
            side_effect=RuntimeError("connection refused"),
        ),
    ):
        output = await agent.run(_make_input(["orders"]))

    assert output.result.resolved == []
    assert output.result.unresolved_tables == ["orders"]
    assert output.result.is_multi_source is False
    assert output.confidence == 0.0
    assert len(output.errors) == 1
    assert output.errors[0].code == "catalog_lookup_failed"
    assert output.errors[0].recoverable is False

    assert len(output.lineage_events) == 1
    assert output.metadata.latency_ms >= 0


async def test_tie_break_picks_first_data_source_when_table_name_collides() -> None:
    """Documents the deliberate tie-break: when the same table name exists
    in more than one of the tenant's data sources, the first one returned
    by `list_data_sources` wins and the connectivity check runs against
    that one only."""

    ds1_id, ds2_id = uuid.uuid4(), uuid.uuid4()
    ds1 = _data_source(ds1_id, source_type="snowflake")
    ds2 = _data_source(ds2_id, source_type="snowflake")
    connector_cls = _reset_fake_connector(succeed=True)

    tables_by_source = {
        ds1_id: [_table("SHARED_TABLE")],
        ds2_id: [_table("SHARED_TABLE")],
    }

    agent = DataSourceDiscoveryAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[ds1, ds2]),
        patch(
            f"{_AGENT_MODULE}.list_tables",
            side_effect=lambda session, *, data_source_id: tables_by_source[data_source_id],
        ),
        patch(f"{_AGENT_MODULE}.build_connector", return_value=connector_cls()),
    ):
        output = await agent.run(_make_input(["shared_table"]))

    assert len(output.result.resolved) == 1
    assert output.result.resolved[0].data_source_id == str(ds1_id)
    assert output.result.is_multi_source is False
    assert connector_cls.call_count == 1


async def test_tie_break_prefers_the_tenant_default_over_encounter_order() -> None:
    """LIMITATIONS.md item 26's real navikenz-poc case: when one of the
    colliding data sources is marked `is_default`, it wins regardless of
    which one `list_data_sources` happened to return first."""

    ds1_id, ds2_id = uuid.uuid4(), uuid.uuid4()
    ds1 = _data_source(ds1_id, source_type="snowflake", is_default=False)
    ds2 = _data_source(ds2_id, source_type="snowflake", is_default=True)
    connector_cls = _reset_fake_connector(succeed=True)

    tables_by_source = {
        ds1_id: [_table("SHARED_TABLE")],
        ds2_id: [_table("SHARED_TABLE")],
    }

    agent = DataSourceDiscoveryAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        # ds1 (non-default) is still returned FIRST -- proving the default
        # wins on its own merit, not because it happened to be encountered
        # first too.
        patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[ds1, ds2]),
        patch(
            f"{_AGENT_MODULE}.list_tables",
            side_effect=lambda session, *, data_source_id: tables_by_source[data_source_id],
        ),
        patch(f"{_AGENT_MODULE}.build_connector", return_value=connector_cls()),
    ):
        output = await agent.run(_make_input(["shared_table"]))

    assert len(output.result.resolved) == 1
    assert output.result.resolved[0].data_source_id == str(ds2_id)
    assert output.result.is_multi_source is False
    assert connector_cls.call_count == 1
