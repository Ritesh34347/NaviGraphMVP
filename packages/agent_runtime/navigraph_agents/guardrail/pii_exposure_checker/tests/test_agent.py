"""Real unit tests for the PII Exposure Checker agent, DB-free.

Mirrors `query/data_source_discovery/tests/test_agent.py`'s "mock the
session layer, assert on shape" convention: `navigraph_catalog.db.
session_scope` and `navigraph_agents.guardrail.pii_exposure_checker.agent.
find_column` are patched at the point they're imported into `agent.py`, fed
plain `SimpleNamespace` stand-ins for `CatalogColumn` rows (the agent only
ever reads `.is_pii` off them -- a real ORM instance isn't needed).

`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from navigraph_shared.contracts import RequestContext

from navigraph_agents.guardrail.pii_exposure_checker.agent import (
    PiiExposureCheckerAgent,
)
from navigraph_agents.guardrail.pii_exposure_checker.contracts import (
    GeneratedSql,
    PiiExposureCheckerInput,
    PiiExposureCheckerPayload,
)

_AGENT_MODULE = "navigraph_agents.guardrail.pii_exposure_checker.agent"


def _request_context(roles: list[str]) -> RequestContext:
    return RequestContext(
        tenant_id="tenant-acme",
        user_id="user-1",
        trace_id="trace-1",
        roles=roles,
    )


def _make_input(statements: list[GeneratedSql], roles: list[str]) -> PiiExposureCheckerInput:
    return PiiExposureCheckerInput(
        request_context=_request_context(roles),
        payload=PiiExposureCheckerPayload(statements=statements),
    )


def _statement(
    data_source_id: str | None = None,
    referenced_tables: list[str] | None = None,
    referenced_columns: list[str] | None = None,
) -> GeneratedSql:
    return GeneratedSql(
        data_source_id=data_source_id or str(uuid.uuid4()),
        sql="SELECT EMAIL FROM CUSTOMERS",
        params={},
        referenced_tables=referenced_tables or ["CUSTOMERS"],
        referenced_columns=referenced_columns or ["EMAIL"],
    )


def _column(is_pii: bool) -> SimpleNamespace:
    return SimpleNamespace(is_pii=is_pii)


@contextmanager
def _fake_session_scope(session_factory):
    yield MagicMock()


async def test_no_pii_columns_referenced_is_cleared() -> None:
    statement = _statement(referenced_columns=["MARKETID"])
    agent = PiiExposureCheckerAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", return_value=_column(is_pii=False)),
    ):
        output = await agent.run(_make_input([statement], roles=["analyst"]))

    assert output.result.cleared == [statement]
    assert output.result.rejected == []
    assert output.confidence == 1.0


async def test_real_qualified_table_dot_column_shape_resolves_correctly() -> None:
    """`GeneratedSql.referenced_columns` entries are real `"TABLE.COLUMN"`
    qualified strings in production (see
    `sql_generation.agent._qualified_col`), not bare column names -- a real
    bug (this agent's fail-open-on-unresolvable design silently `cleared`
    every real PII statement, since a qualified name never matched a bare
    lookup) was caught live via `tests/integration/guardrail_pipeline/`
    before this test existed. This test pins that real shape down at the
    unit level too."""

    statement = _statement(
        referenced_tables=["CUSTOMER_INFORMATION"],
        referenced_columns=["CUSTOMER_INFORMATION.CUSTOMERID"],
    )
    agent = PiiExposureCheckerAgent(session_factory=MagicMock())

    probed: list[tuple[str, str]] = []

    def fake_find_column(session, *, data_source_id, table_name, column_name):
        probed.append((table_name, column_name))
        if (table_name, column_name) == ("CUSTOMER_INFORMATION", "CUSTOMERID"):
            return _column(is_pii=True)
        return None

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", side_effect=fake_find_column),
    ):
        output = await agent.run(_make_input([statement], roles=["analyst"]))

    assert probed == [("CUSTOMER_INFORMATION", "CUSTOMERID")]
    assert output.result.cleared == []
    assert len(output.result.rejected) == 1
    assert output.result.rejected[0].code == "pii_column_access_denied"


async def test_pii_column_with_analyst_role_is_rejected() -> None:
    statement = _statement()
    agent = PiiExposureCheckerAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", return_value=_column(is_pii=True)),
    ):
        output = await agent.run(_make_input([statement], roles=["analyst"]))

    assert output.result.cleared == []
    assert len(output.result.rejected) == 1
    error = output.result.rejected[0]
    assert error.code == "pii_column_access_denied"
    assert error.recoverable is False
    assert output.confidence == 0.0


async def test_pii_column_with_pii_viewer_role_is_cleared() -> None:
    statement = _statement()
    agent = PiiExposureCheckerAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", return_value=_column(is_pii=True)),
    ):
        output = await agent.run(_make_input([statement], roles=["pii_viewer"]))

    assert output.result.cleared == [statement]
    assert output.result.rejected == []


async def test_pii_column_with_admin_role_is_cleared() -> None:
    statement = _statement()
    agent = PiiExposureCheckerAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", return_value=_column(is_pii=True)),
    ):
        output = await agent.run(_make_input([statement], roles=["admin"]))

    assert output.result.cleared == [statement]
    assert output.result.rejected == []


async def test_catalog_lookup_exception_is_rejected() -> None:
    statement = _statement()
    agent = PiiExposureCheckerAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", side_effect=RuntimeError("connection refused")),
    ):
        output = await agent.run(_make_input([statement], roles=["admin"]))

    assert output.result.cleared == []
    assert len(output.result.rejected) == 1
    error = output.result.rejected[0]
    assert error.code == "catalog_lookup_failed"
    assert error.recoverable is False


async def test_unresolvable_column_with_no_pii_match_is_cleared() -> None:
    """Not this agent's job to flag an unknown column -- that's Schema
    Constraint Validator's job. An unresolvable column simply fails open
    on the PII question."""

    statement = _statement(referenced_columns=["GHOST_COLUMN"])
    agent = PiiExposureCheckerAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", return_value=None),
    ):
        output = await agent.run(_make_input([statement], roles=["analyst"]))

    assert output.result.cleared == [statement]
    assert output.result.rejected == []


async def test_pii_column_checked_against_every_referenced_table() -> None:
    """Documents the table/column pairing judgment call: a column is
    tried against EVERY referenced table, not positionally paired -- so a
    PII match on the second table still blocks the statement."""

    statement = _statement(
        referenced_tables=["ORDERS", "CUSTOMERS"],
        referenced_columns=["EMAIL"],
    )
    agent = PiiExposureCheckerAgent(session_factory=MagicMock())

    def _fake_find_column(session, *, data_source_id, table_name, column_name):
        if table_name == "CUSTOMERS":
            return _column(is_pii=True)
        return None

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", side_effect=_fake_find_column),
    ):
        output = await agent.run(_make_input([statement], roles=["analyst"]))

    assert output.result.cleared == []
    assert output.result.rejected[0].code == "pii_column_access_denied"


async def test_multiple_statements_are_each_routed_independently() -> None:
    clean_statement = _statement(referenced_columns=["MARKETID"], data_source_id=str(uuid.uuid4()))
    pii_statement = _statement(referenced_columns=["EMAIL"], data_source_id=str(uuid.uuid4()))

    def _fake_find_column(session, *, data_source_id, table_name, column_name):
        return _column(is_pii=(column_name == "EMAIL"))

    agent = PiiExposureCheckerAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", side_effect=_fake_find_column),
    ):
        output = await agent.run(
            _make_input([clean_statement, pii_statement], roles=["analyst"])
        )

    assert output.result.cleared == [clean_statement]
    assert len(output.result.rejected) == 1


async def test_output_envelope_lineage_and_metadata() -> None:
    statement = _statement(referenced_columns=["MARKETID"])
    agent = PiiExposureCheckerAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", return_value=_column(is_pii=False)),
    ):
        output = await agent.run(_make_input([statement], roles=["analyst"]))

    assert output.errors == []
    assert len(output.lineage_events) == 1
    assert output.lineage_events[0].agent_name == "guardrail.pii_exposure_checker"
    assert output.lineage_events[0].tenant_id == "tenant-acme"
    assert output.lineage_events[0].trace_id == "trace-1"
    assert output.metadata.latency_ms >= 0
