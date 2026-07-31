"""Real unit tests for the Semantic Retrieval agent.

Uses `FakeLLMClient` exclusively -- no network access, no API key required.
`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

import json

from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient

from navigraph_agents.understanding.semantic_retrieval.agent import (
    SemanticRetrievalAgent,
)
from navigraph_agents.understanding.semantic_retrieval.contracts import (
    RetrievalCandidate,
    SemanticRetrievalInput,
    SemanticRetrievalPayload,
)

_CANDIDATES = [
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


def _make_input(
    unresolved_terms: list[str],
    candidates: list[RetrievalCandidate] | None = None,
    question: str = "What was total payment volume by merchant last month?",
) -> SemanticRetrievalInput:
    return SemanticRetrievalInput(
        request_context=RequestContext(
            tenant_id="tenant-acme",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
        ),
        payload=SemanticRetrievalPayload(
            question=question,
            unresolved_terms=unresolved_terms,
            candidates=candidates if candidates is not None else _CANDIDATES,
        ),
    )


async def test_empty_unresolved_terms_short_circuits_with_no_llm_call() -> None:
    """The empty-terms case must not call the LLM at all."""

    fake_llm = FakeLLMClient(response="should never be read")
    agent = SemanticRetrievalAgent(llm_client=fake_llm)

    output = await agent.run(_make_input(unresolved_terms=[]))

    assert fake_llm.calls == []
    assert output.result.matches == []
    assert output.confidence == 1.0
    assert output.errors == []

    assert len(output.lineage_events) == 1
    lineage = output.lineage_events[0]
    assert lineage.agent_name == "understanding.semantic_retrieval"

    assert output.metadata.model_version is None
    assert output.metadata.prompt_version is None
    assert output.metadata.tokens_input is None
    assert output.metadata.tokens_output is None
    assert output.metadata.latency_ms >= 0


async def test_valid_match_against_a_real_candidate_id_is_accepted() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "matches": [
                    {
                        "term": "payment volume",
                        "catalog_column_id": "col_txn_amount",
                        "rationale": "payment volume maps to the transaction amount column",
                    }
                ]
            }
        )
    )
    agent = SemanticRetrievalAgent(llm_client=fake_llm)

    output = await agent.run(_make_input(unresolved_terms=["payment volume"]))

    assert len(output.result.matches) == 1
    match = output.result.matches[0]
    assert match.term == "payment volume"
    assert match.matched is True
    assert match.catalog_column_id == "col_txn_amount"
    assert match.table_name == "transactions"
    assert match.column_name == "amount_usd"
    assert output.errors == []
    assert output.confidence == 1.0

    assert len(fake_llm.calls) == 1
    call = fake_llm.calls[0]
    assert "col_txn_amount" in call["messages"][0]["content"]


async def test_hallucinated_candidate_id_is_rejected_not_silently_trusted() -> None:
    """The single most important behavior in this agent: an LLM-returned
    catalog_column_id that is not in the caller-supplied candidate list must
    never be trusted, even though it's a plausible-looking string."""

    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "matches": [
                    {
                        "term": "payment volume",
                        "catalog_column_id": "col_totally_made_up",
                        "rationale": "this id does not exist in the candidate list",
                    }
                ]
            }
        )
    )
    agent = SemanticRetrievalAgent(llm_client=fake_llm)

    output = await agent.run(_make_input(unresolved_terms=["payment volume"]))

    assert len(output.result.matches) == 1
    match = output.result.matches[0]
    assert match.term == "payment volume"
    assert match.matched is False
    assert match.catalog_column_id is None

    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_returned_invalid_candidate"
    assert output.errors[0].recoverable is True
    assert "col_totally_made_up" in output.errors[0].message
    assert output.confidence == 0.0


async def test_llm_correctly_returns_null_for_term_with_no_good_candidate() -> None:
    """A legitimate no-match outcome must NOT be treated as an error."""

    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "matches": [
                    {
                        "term": "customer loyalty score",
                        "catalog_column_id": None,
                        "rationale": "no candidate describes a loyalty score",
                    }
                ]
            }
        )
    )
    agent = SemanticRetrievalAgent(llm_client=fake_llm)

    output = await agent.run(_make_input(unresolved_terms=["customer loyalty score"]))

    assert len(output.result.matches) == 1
    match = output.result.matches[0]
    assert match.term == "customer loyalty score"
    assert match.matched is False
    assert match.catalog_column_id is None
    assert match.rationale == "no candidate describes a loyalty score"

    # No error -- this is a legitimate outcome, not a failure.
    assert output.errors == []
    assert output.confidence == 1.0


async def test_malformed_json_falls_back_gracefully_for_all_terms() -> None:
    fake_llm = FakeLLMClient(response="this is not json at all")
    agent = SemanticRetrievalAgent(llm_client=fake_llm)

    # Must not raise.
    output = await agent.run(
        _make_input(unresolved_terms=["payment volume", "customer loyalty score"])
    )

    assert len(output.result.matches) == 2
    assert all(not m.matched for m in output.result.matches)
    assert [m.term for m in output.result.matches] == ["payment volume", "customer loyalty score"]

    assert len(output.errors) == 1
    assert output.errors[0].code == "llm_response_not_json"
    assert output.errors[0].recoverable is True
    assert output.confidence == 0.0


async def test_missing_term_entry_falls_back_gracefully() -> None:
    fake_llm = FakeLLMClient(
        response=json.dumps(
            {
                "matches": [
                    {"term": "payment volume", "catalog_column_id": "col_txn_amount"},
                ]
            }
        )
    )
    agent = SemanticRetrievalAgent(llm_client=fake_llm)

    output = await agent.run(
        _make_input(unresolved_terms=["payment volume", "customer loyalty score"])
    )

    assert len(output.result.matches) == 2
    assert output.result.matches[0].matched is True
    assert output.result.matches[1].matched is False
    assert output.result.matches[1].term == "customer loyalty score"

    assert any(e.code == "llm_response_missing_term_match" for e in output.errors)


async def test_llm_call_failure_falls_back_gracefully() -> None:
    def _raise(system, messages, max_tokens):
        raise RuntimeError("simulated network failure")

    fake_llm = FakeLLMClient(response_fn=_raise)
    agent = SemanticRetrievalAgent(llm_client=fake_llm)

    output = await agent.run(_make_input(unresolved_terms=["payment volume"]))

    assert len(output.result.matches) == 1
    assert output.result.matches[0].matched is False
    assert output.errors[0].code == "llm_call_failed"
    assert output.errors[0].recoverable is False
    assert output.metadata.model_version is None
    assert output.metadata.tokens_input is None


async def test_max_tokens_budget_is_large_enough_for_a_real_size_candidate_list() -> None:
    """Regression guard for a real bug found live: with a real-size (114
    column) candidate list and several unresolved terms in one batch, a
    1536-token budget let the real model's response come back truncated --
    `tokens_output` exactly equal to `max_tokens` with an EMPTY `text` --
    even though the same terms matched correctly in isolation against the
    identical candidate list. This just asserts the budget the agent
    actually requests is comfortably above that failure point, so a future
    edit can't silently shrink it back down without this test noticing.
    """

    fake_llm = FakeLLMClient(response=json.dumps({"matches": []}))
    agent = SemanticRetrievalAgent(llm_client=fake_llm)

    await agent.run(_make_input(unresolved_terms=["payment volume"]))

    assert fake_llm.calls[0]["max_tokens"] >= 4096
