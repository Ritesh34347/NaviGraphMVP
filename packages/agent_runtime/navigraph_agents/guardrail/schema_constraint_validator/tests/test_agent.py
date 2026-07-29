"""Real unit tests for the Schema Constraint Validator agent, DB-free.

Mirrors `query/data_source_discovery/tests/test_agent.py`'s "mock the
session layer, assert on shape" convention: `navigraph_catalog.db.
session_scope` is patched at the point it's imported into `agent.py`
(yielding a bare `MagicMock()` session, since this agent never touches the
session directly -- every real lookup goes through `find_column`), and
`navigraph_catalog.api.find_column` is patched directly (it's a simple
module-level function import) so tests can assert on exactly which
`(data_source_id, table_name, column_name)` combinations were probed
without a real Postgres.

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

from navigraph_agents.guardrail.schema_constraint_validator.agent import (
    SchemaConstraintValidatorAgent,
)
from navigraph_agents.guardrail.schema_constraint_validator.contracts import (
    GeneratedSql,
    SchemaConstraintValidatorInput,
    SchemaConstraintValidatorPayload,
)

_AGENT_MODULE = "navigraph_agents.guardrail.schema_constraint_validator.agent"


def _make_input(statements: list[GeneratedSql]) -> SchemaConstraintValidatorInput:
    return SchemaConstraintValidatorInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=SchemaConstraintValidatorPayload(statements=statements),
    )


def _statement(
    data_source_id: str,
    *,
    referenced_tables: list[str],
    referenced_columns: list[str],
) -> GeneratedSql:
    return GeneratedSql(
        data_source_id=data_source_id,
        sql="SELECT 1",
        params={},
        referenced_tables=referenced_tables,
        referenced_columns=referenced_columns,
    )


@contextmanager
def _fake_session_scope(session_factory):
    yield MagicMock()


async def test_all_valid_statement_lands_in_validated_with_no_rejections() -> None:
    ds_id = str(uuid.uuid4())
    statement = _statement(
        ds_id,
        referenced_tables=["ORDERS", "CUSTOMERS"],
        referenced_columns=["ORDER_ID", "CUSTOMER_NAME"],
    )

    def fake_find_column(session, *, data_source_id, table_name, column_name):
        known = {("ORDERS", "ORDER_ID"), ("CUSTOMERS", "CUSTOMER_NAME")}
        if (table_name, column_name) in known:
            return SimpleNamespace(name=column_name)
        return None

    agent = SchemaConstraintValidatorAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", side_effect=fake_find_column),
    ):
        output = await agent.run(_make_input([statement]))

    assert output.result.rejected == []
    assert len(output.result.validated) == 1
    assert output.result.validated[0].data_source_id == ds_id
    assert output.confidence == 1.0
    assert output.errors == []

    assert len(output.lineage_events) == 1
    lineage = output.lineage_events[0]
    assert lineage.agent_name == "guardrail.schema_constraint_validator"
    assert lineage.tenant_id == "tenant-acme"
    assert lineage.trace_id == "trace-1"

    assert output.metadata.latency_ms >= 0
    assert output.metadata.model_version is None


async def test_real_qualified_table_dot_column_shape_resolves_correctly() -> None:
    """`GeneratedSql.referenced_columns` entries are real `"TABLE.COLUMN"`
    qualified strings in production (see
    `sql_generation.agent._qualified_col`), not bare column names -- a real
    bug (every real statement was rejected as `unknown_column`) was caught
    live via `tests/integration/guardrail_pipeline/` before this test
    existed. This test pins that real shape down at the unit level too."""

    ds_id = str(uuid.uuid4())
    statement = _statement(
        ds_id,
        referenced_tables=["STAGING_TRANSACTIONS"],
        referenced_columns=["STAGING_TRANSACTIONS.MARKETID", "STAGING_TRANSACTIONS.UNITS"],
    )

    probed: list[tuple[str, str]] = []

    def fake_find_column(session, *, data_source_id, table_name, column_name):
        probed.append((table_name, column_name))
        known = {("STAGING_TRANSACTIONS", "MARKETID"), ("STAGING_TRANSACTIONS", "UNITS")}
        if (table_name, column_name) in known:
            return SimpleNamespace(name=column_name)
        return None

    agent = SchemaConstraintValidatorAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", side_effect=fake_find_column),
    ):
        output = await agent.run(_make_input([statement]))

    assert output.result.rejected == []
    assert len(output.result.validated) == 1
    # The qualified table name is what got probed -- never the bare
    # referenced_tables entry alone with the still-qualified column string.
    assert probed == [
        ("STAGING_TRANSACTIONS", "MARKETID"),
        ("STAGING_TRANSACTIONS", "UNITS"),
    ]


async def test_unknown_column_across_every_table_is_rejected() -> None:
    ds_id = str(uuid.uuid4())
    statement = _statement(
        ds_id,
        referenced_tables=["ORDERS", "CUSTOMERS"],
        referenced_columns=["GHOST_COLUMN"],
    )

    agent = SchemaConstraintValidatorAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", return_value=None),
    ):
        output = await agent.run(_make_input([statement]))

    assert output.result.validated == []
    assert len(output.result.rejected) == 1
    assert output.result.rejected[0].code == "unknown_column"
    assert output.result.rejected[0].recoverable is False
    assert "GHOST_COLUMN" in output.result.rejected[0].message
    assert output.confidence == 0.0


async def test_catalog_lookup_exception_is_rejected_not_raised() -> None:
    ds_id = str(uuid.uuid4())
    statement = _statement(
        ds_id,
        referenced_tables=["ORDERS"],
        referenced_columns=["ORDER_ID"],
    )

    agent = SchemaConstraintValidatorAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(
            f"{_AGENT_MODULE}.find_column",
            side_effect=RuntimeError("connection refused"),
        ),
    ):
        output = await agent.run(_make_input([statement]))

    assert output.result.validated == []
    assert len(output.result.rejected) == 1
    assert output.result.rejected[0].code == "catalog_lookup_failed"
    assert output.result.rejected[0].recoverable is False
    assert output.confidence == 0.0

    # Must not raise -- lineage/metadata are still produced on this path.
    assert len(output.lineage_events) == 1
    assert output.metadata.latency_ms >= 0


async def test_mixed_batch_splits_validated_and_rejected_correctly() -> None:
    good_ds_id = str(uuid.uuid4())
    bad_ds_id = str(uuid.uuid4())
    good_statement = _statement(
        good_ds_id,
        referenced_tables=["ORDERS"],
        referenced_columns=["ORDER_ID"],
    )
    bad_statement = _statement(
        bad_ds_id,
        referenced_tables=["ORDERS"],
        referenced_columns=["GHOST_COLUMN"],
    )

    def fake_find_column(session, *, data_source_id, table_name, column_name):
        if data_source_id == uuid.UUID(good_ds_id) and column_name == "ORDER_ID":
            return SimpleNamespace(name=column_name)
        return None

    agent = SchemaConstraintValidatorAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column", side_effect=fake_find_column),
    ):
        output = await agent.run(_make_input([good_statement, bad_statement]))

    assert len(output.result.validated) == 1
    assert output.result.validated[0].data_source_id == good_ds_id

    assert len(output.result.rejected) == 1
    assert output.result.rejected[0].code == "unknown_column"
    assert bad_ds_id in output.result.rejected[0].message

    assert output.confidence == 0.0


async def test_invalid_data_source_id_is_rejected() -> None:
    """`data_source_id` must be a parseable UUID -- a malformed one is
    rejected with its own distinct code, never confused with a genuine
    catalog-lookup failure or unknown-column finding."""

    statement = _statement(
        "not-a-uuid",
        referenced_tables=["ORDERS"],
        referenced_columns=["ORDER_ID"],
    )

    agent = SchemaConstraintValidatorAgent(session_factory=MagicMock())

    with (
        patch(f"{_AGENT_MODULE}.session_scope", _fake_session_scope),
        patch(f"{_AGENT_MODULE}.find_column") as mock_find_column,
    ):
        output = await agent.run(_make_input([statement]))

    assert output.result.validated == []
    assert len(output.result.rejected) == 1
    assert output.result.rejected[0].code == "invalid_data_source_id"
    assert output.result.rejected[0].recoverable is False
    mock_find_column.assert_not_called()
