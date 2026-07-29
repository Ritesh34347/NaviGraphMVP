"""Real integration test for the SQL Generation agent against the actual
Anthropic API.

Marked `llm_integration` (registered in packages/agent_runtime/pyproject.toml
under `[tool.pytest.ini_options].markers`). A plain `pytest` run never
executes this file's assertions against the real API: the test is guarded by
`@pytest.mark.skipif` on `ANTHROPIC_API_KEY` being unset, so it *skips*
cleanly (not an error, not a failure) when no key is present. To actually
exercise it against the real API:

    ANTHROPIC_API_KEY=sk-... pytest -m llm_integration
"""

from __future__ import annotations

import os

import pytest
from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import AnthropicLLMClient

from navigraph_agents.query.sql_generation.agent import SqlGenerationAgent
from navigraph_agents.query.sql_generation.contracts import (
    ResolvedColumnRef,
    ResolvedDataSource,
    SchemaMappingResult,
    SqlGenerationInput,
    SqlGenerationPayload,
)

pytestmark = pytest.mark.llm_integration


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY is not set; skipping real Anthropic API call",
)
async def test_agent_resolves_a_relative_date_predicate_via_the_real_anthropic_api() -> None:
    llm_client = AnthropicLLMClient()
    agent = SqlGenerationAgent(llm_client=llm_client)

    columns = [
        ResolvedColumnRef(
            term="units traded",
            catalog_column_id="col_units",
            table_name="STAGING_TRANSACTIONS",
            schema_name="STAGING",
            column_name="UNITS",
            data_type="NUMBER",
            role="measure",
        ),
        ResolvedColumnRef(
            term="market",
            catalog_column_id="col_market",
            table_name="STAGING_TRANSACTIONS",
            schema_name="STAGING",
            column_name="MARKETID",
            data_type="TEXT",
            role="dimension",
        ),
        ResolvedColumnRef(
            term="transaction date",
            catalog_column_id="col_txn_date",
            table_name="STAGING_TRANSACTIONS",
            schema_name="STAGING",
            column_name="TRANSACTIONDATE",
            data_type="DATE",
            role="dimension",
        ),
    ]

    agent_input = SqlGenerationInput(
        request_context=RequestContext(
            tenant_id="tenant-integration-test",
            user_id="user-1",
            trace_id="trace-1",
        ),
        payload=SqlGenerationPayload(
            original_question="What was total transaction volume by market last quarter?",
            intent="metric_lookup",
            schema_mapping=SchemaMappingResult(
                tables=["STAGING_TRANSACTIONS"],
                columns=columns,
            ),
            resolved_data_sources=[
                ResolvedDataSource(
                    table_name="STAGING_TRANSACTIONS",
                    data_source_id="ds_snowflake_prod",
                    source_type="snowflake",
                    reachable=True,
                )
            ],
        ),
    )

    output = await agent.run(agent_input)

    # We don't hard-assert exactly which phrase/column the model resolved
    # (output can vary slightly), but the call must have actually reached
    # the real API (non-zero token usage), and whatever it returned must be
    # a real, non-hallucinated column -- the agent's own validation
    # guarantees this, but we assert it here too as an end-to-end sanity
    # check. It must also never have leaked a literal predicate value into
    # the SQL text itself.
    assert output.metadata.tokens_input is not None
    assert output.metadata.tokens_input > 0
    assert len(output.result.statements) == 1

    statement = output.result.statements[0]
    for predicate in output.result.predicate_resolutions:
        assert predicate.column in {c.column_name for c in columns}
        values = (
            predicate.resolved_value
            if isinstance(predicate.resolved_value, list)
            else [predicate.resolved_value]
        )
        for value in values:
            assert value not in statement.sql
