"""Real integration test for the Semantic Retrieval agent against the actual
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

from navigraph_agents.understanding.semantic_retrieval.agent import (
    SemanticRetrievalAgent,
)
from navigraph_agents.understanding.semantic_retrieval.contracts import (
    RetrievalCandidate,
    SemanticRetrievalInput,
    SemanticRetrievalPayload,
)

pytestmark = pytest.mark.llm_integration


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY is not set; skipping real Anthropic API call",
)
async def test_agent_matches_a_real_term_via_the_real_anthropic_api() -> None:
    llm_client = AnthropicLLMClient()
    agent = SemanticRetrievalAgent(llm_client=llm_client)

    candidates = [
        RetrievalCandidate(
            catalog_column_id="col_txn_amount",
            table_name="transactions",
            column_name="amount_usd",
            business_name="Transaction Amount",
            synonyms=["txn amount", "payment amount"],
            description="The USD value of a single transaction.",
        ),
        RetrievalCandidate(
            catalog_column_id="col_merchant_name",
            table_name="merchants",
            column_name="display_name",
            business_name="Merchant Name",
            synonyms=["merchant", "seller name"],
            description="The merchant's customer-facing display name.",
        ),
    ]

    agent_input = SemanticRetrievalInput(
        request_context=RequestContext(
            tenant_id="tenant-integration-test",
            user_id="user-1",
            trace_id="trace-1",
        ),
        payload=SemanticRetrievalPayload(
            question="What was total payment volume by merchant last month?",
            unresolved_terms=["payment volume"],
            candidates=candidates,
        ),
    )

    output = await agent.run(agent_input)

    # We don't hard-assert which candidate wins (model output can vary
    # slightly), but the call must have actually reached the real API
    # (non-zero token usage), and whatever it returned must be a real,
    # non-hallucinated candidate ID -- the agent's own validation guarantees
    # this, but we assert it here too as an end-to-end sanity check.
    assert output.metadata.tokens_input is not None
    assert output.metadata.tokens_input > 0
    assert len(output.result.matches) == 1
    match = output.result.matches[0]
    if match.matched:
        assert match.catalog_column_id in {c.catalog_column_id for c in candidates}
