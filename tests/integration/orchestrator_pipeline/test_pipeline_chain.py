"""Integration test: the real Request Orchestrator agent -- one
`RequestOrchestratorAgent.run()` call replacing every other pipeline
integration test's hand-threaded chain of ~15-19 individual agent calls --
against the live docker-compose Postgres, Neo4j, OPA, Redis, and a real
Snowflake account.

REQUIRES LIVE, REACHABLE POSTGRES, NEO4J, OPA, REDIS, AND A REAL SNOWFLAKE
ACCOUNT -- mirrors every other `tests/integration/*_pipeline/` directory's
stance exactly: this test does NOT skip gracefully if any of these are
unreachable. Point it at the real services via the same env-var convention
every other NaviGraph integration test uses: `POSTGRES_HOST=localhost
POSTGRES_PORT=5433 NEO4J_URI=bolt://localhost:7687 OPA_URL=http://localhost:8181
REDIS_URL=redis://localhost:6379` plus the real `SNOWFLAKE_*` values, before
running this file.

`FakeLLMClient(response_fn=...)` is used throughout -- consistent with every
other pytest-tier pipeline test (only `eval/run_harness.py` uses a real
model). Since `RequestOrchestratorAgent` constructs ALL of its own LLM-backed
sub-agents (Conversation, Intent Understanding, Semantic Retrieval,
Clarification Coordinator, Grounded Narrative Generation, Follow-up
Suggestion) from ONE shared `llm_client`, `_make_response_fn` below
dispatches a different canned JSON response per call based on a unique
substring of each agent's own `prompts/*.md` system-prompt title -- the one
thing that reliably differs per agent. An unrecognized system prompt raises
immediately rather than silently returning an empty response, so a future
agent added to the pipeline that this test doesn't yet know about fails
loudly instead of producing a confusing downstream parse error.

Worked example, chosen DELIBERATELY to also be a real regression check for
the join-inference bug found live during this same phase's gateway smoke
test (see LIMITATIONS.md item 15's "Real gap found and fixed in Phase 9"
section): "What is the total transaction volume by market?", with the fake
Semantic Retrieval response resolving "market" to `MARKETS.NAME` and
"transaction volume" to `TRANSACTIONS.TOTALVALUE` -- two different tables,
which only produces a correct (non-cross-joined) per-market breakdown if
Schema Mapping's `_build_joins` successfully uses the `RelationshipConcept`
this phase added (`"Transaction happens in Market"`).
"""

from __future__ import annotations

import json
from typing import cast

import navigraph_connectors.snowflake  # noqa: F401 -- registers "snowflake" for real find_column/etc
import pytest
import redis
from navigraph_agents.orchestrator.request_orchestrator.agent import (
    RequestOrchestratorAgent,
)
from navigraph_agents.orchestrator.request_orchestrator.contracts import (
    RequestOrchestratorInput,
    RequestOrchestratorPayload,
)
from navigraph_agents.orchestrator.session_context_manager.agent import (
    CacheClientProtocol,
    SessionContextManagerAgent,
    build_cache_key,
)
from navigraph_agents.orchestrator.session_context_manager.contracts import (
    SessionContextManagerInput,
    SessionContextManagerPayload,
)
from navigraph_agents.understanding.metadata_discovery.agent import (
    MetadataDiscoveryAgent,
)
from navigraph_agents.understanding.metadata_discovery.contracts import (
    MetadataDiscoveryInput,
    MetadataDiscoveryPayload,
)
from navigraph_catalog.api import list_data_sources
from navigraph_catalog.db import get_engine, get_session_factory, session_scope
from navigraph_catalog.settings import MetadataCatalogSettings
from navigraph_kg.client import Neo4jClient
from navigraph_kg.settings import KnowledgeGraphSettings
from navigraph_lineage.db import get_engine as get_lineage_engine
from navigraph_lineage.db import get_session_factory as get_lineage_session_factory
from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient, LLMResponse
from navigraph_shared.opa import HttpOpaClient, OpaSettings

pytestmark = pytest.mark.postgres_integration

_TENANT_ID = "navikenz-poc"
_DATA_SOURCE_NAME = "fidelity_poc_snowflake_v2"
_QUESTION = "What is the total transaction volume by market?"

_NARRATIVE_JSON = json.dumps(
    {
        "narrative": "Transaction volume varies across markets in this result set.",
        "citations": [],
    }
)
_FOLLOW_UP_JSON = json.dumps(
    {
        "suggestions": [
            {
                "question": "Which market had the highest transaction volume?",
                "rationale": "drill down into the top market",
            }
        ]
    }
)


def _request_context(*, trace_id: str) -> RequestContext:
    return RequestContext(
        tenant_id=_TENANT_ID,
        user_id="integration-test-user",
        trace_id=trace_id,
        roles=["analyst"],
        claims={"tenant_id": _TENANT_ID},
    )


def _make_response_fn(
    *,
    intent_json: str,
    semantic_retrieval_json: str,
    clarification_json: str | None = None,
):
    """Build a `FakeLLMClient(response_fn=...)` callable that dispatches a
    different canned JSON response per real sub-agent, keyed off each
    agent's own `prompts/*.md` title (the one thing that reliably
    differs). See this module's docstring for why an unrecognized prompt
    raises rather than returning something silently wrong.
    """

    def _response_fn(system: str, messages: list, max_tokens: int) -> LLMResponse:
        # Match each agent's own `# <Title> — System Prompt` H1 line, not a
        # bare substring anywhere in the body -- Follow-Up Suggestion's own
        # prompt body says "Unlike the Grounded Narrative Generation agent,
        # you are explicitly..." to explain its deliberately different
        # grounding discipline, so a bare `"Grounded Narrative Generation"
        # in system` check (checked before Follow-Up Suggestion's own,
        # more specific check) would silently misroute Follow-Up
        # Suggestion's real call to the narrative branch instead -- caught
        # live when this test's `follow_up_suggestions` came back
        # unexpectedly empty on its first real run.
        if system.startswith("# Intent Understanding"):
            text = intent_json
        elif system.startswith("# Semantic Retrieval"):
            text = semantic_retrieval_json
        elif system.startswith("# Grounded Narrative Generation"):
            text = _NARRATIVE_JSON
        elif system.startswith("# Follow-Up Suggestion"):
            text = _FOLLOW_UP_JSON
        elif system.startswith("# Clarification Coordinator"):
            assert clarification_json is not None, (
                "Clarification Coordinator was called but this scenario didn't "
                "expect it -- schema mapping must have unexpectedly failed to "
                "resolve tables"
            )
            text = clarification_json
        elif system.startswith("# Conversation"):
            # Empty conversation_history short-circuits deterministically (no
            # LLM call) on a session's first turn -- this branch only fires
            # on a second, same-session call in the session round-trip test.
            text = '{"is_follow_up": false}'
        else:
            raise AssertionError(f"unexpected system prompt in fake LLM dispatch: {system[:80]!r}")
        return LLMResponse(text=text, tokens_input=0, tokens_output=0, model="fake-model")

    return _response_fn


def _real_data_source_id(session_factory) -> str:
    with session_scope(session_factory) as session:
        data_sources = list_data_sources(session, tenant_id=_TENANT_ID)
        matching = [ds for ds in data_sources if ds.name == _DATA_SOURCE_NAME]
        assert matching, f"No data source named {_DATA_SOURCE_NAME!r} for tenant {_TENANT_ID!r}"
        return str(matching[0].id)


async def _catalog_column_id(
    session_factory, data_source_id: str, *, table: str, column: str
) -> str:
    """Real column-ID lookup via the real Metadata Discovery agent, exactly
    mirroring `tests/integration/insight_pipeline/test_pipeline_chain.py`'s
    convention of never hardcoding a catalog UUID directly in a test."""

    agent = MetadataDiscoveryAgent(session_factory=session_factory)
    output = await agent.run(
        MetadataDiscoveryInput(
            request_context=_request_context(trace_id="column-lookup"),
            payload=MetadataDiscoveryPayload(data_source_id=data_source_id),
        )
    )
    entry = next(
        (
            c
            for c in output.result.columns
            if c.table_name.upper() == table.upper() and c.column_name.upper() == column.upper()
        ),
        None,
    )
    assert entry is not None, f"{table}.{column} not found in catalog inventory"
    return entry.catalog_column_id


def _build_agent(*, response_fn, redis_client) -> RequestOrchestratorAgent:
    catalog_session_factory = get_session_factory(get_engine(MetadataCatalogSettings()))
    lineage_session_factory = get_lineage_session_factory(get_lineage_engine())
    neo4j_client = Neo4jClient(KnowledgeGraphSettings())

    connectivity = neo4j_client.test_connection()
    assert connectivity.success, f"Neo4j unreachable: {connectivity.message}"

    return RequestOrchestratorAgent(
        llm_client=FakeLLMClient(response_fn=response_fn),
        catalog_session_factory=catalog_session_factory,
        lineage_session_factory=lineage_session_factory,
        neo4j_client=neo4j_client,
        opa_client=HttpOpaClient(OpaSettings()),
        cache_client=cast(CacheClientProtocol, redis_client),
    )


@pytest.mark.neo4j_integration
@pytest.mark.snowflake_integration
@pytest.mark.opa_integration
@pytest.mark.redis_integration
@pytest.mark.asyncio
async def test_happy_path_answers_the_worked_example_with_a_real_join() -> None:
    catalog_session_factory = get_session_factory(get_engine(MetadataCatalogSettings()))
    data_source_id = _real_data_source_id(catalog_session_factory)

    market_name_id = await _catalog_column_id(
        catalog_session_factory, data_source_id, table="MARKETS", column="NAME"
    )
    total_value_id = await _catalog_column_id(
        catalog_session_factory, data_source_id, table="TRANSACTIONS", column="TOTALVALUE"
    )

    response_fn = _make_response_fn(
        intent_json=json.dumps(
            {"intent": "comparison", "entities": ["transaction volume", "market"]}
        ),
        semantic_retrieval_json=json.dumps(
            {
                "matches": [
                    {
                        "term": "market",
                        "catalog_column_id": market_name_id,
                        "rationale": "MARKETS.NAME is the market dimension",
                    },
                    {
                        "term": "transaction volume",
                        "catalog_column_id": total_value_id,
                        "rationale": "TOTALVALUE is the transaction-value measure",
                    },
                ]
            }
        ),
    )

    redis_client = redis.Redis.from_url("redis://localhost:6379")
    agent = _build_agent(response_fn=response_fn, redis_client=redis_client)

    try:
        output = await agent.run(
            RequestOrchestratorInput(
                request_context=_request_context(trace_id="orchestrator-happy-path"),
                payload=RequestOrchestratorPayload(
                    question=_QUESTION, data_source_id=data_source_id
                ),
            )
        )
        result = output.result

        assert result.outcome == "answered", (
            f"expected answered, got {result.outcome}: {result.failure_stage} "
            f"{result.failure_reason} {result.clarifying_question}"
        )
        assert result.unmapped_terms == []
        assert result.final_row_count > 0
        assert result.chart is not None
        assert result.narrative is not None
        assert 1 <= len(result.follow_up_suggestions) <= 3
        assert output.confidence == 1.0

        # The real regression proof for the join-inference bug: with the
        # cross-table resolution above (MARKETS.NAME + TRANSACTIONS.TOTALVALUE),
        # a repeat of the original bug would cross-join one ungrouped grand
        # total against every market name, producing the SAME value on every
        # row. A correct join produces a genuine per-market breakdown --
        # more than one distinct total value across the real result set
        # (unless every real market coincidentally transacted the exact same
        # total, which the live data does not).
        distinct_totals = {row.get("TOTALVALUE_TOTAL") for row in result.final_rows}
        assert len(distinct_totals) > 1, (
            "every row reported the identical total -- the cross-table join "
            "regression is back: " + repr(result.final_rows[:3])
        )
        print(f"\nReal Request Orchestrator happy path: outcome={result.outcome}, "
              f"{result.final_row_count} distinct markets, chart={result.chart}")
    finally:
        redis_client.close()


@pytest.mark.neo4j_integration
@pytest.mark.snowflake_integration
@pytest.mark.opa_integration
@pytest.mark.redis_integration
@pytest.mark.asyncio
async def test_session_round_trip_persists_and_is_visible_to_a_second_call() -> None:
    catalog_session_factory = get_session_factory(get_engine(MetadataCatalogSettings()))
    data_source_id = _real_data_source_id(catalog_session_factory)

    units_id = await _catalog_column_id(
        catalog_session_factory, data_source_id, table="TRANSACTIONS", column="UNITS"
    )
    market_id_id = await _catalog_column_id(
        catalog_session_factory, data_source_id, table="TRANSACTIONS", column="MARKETID"
    )

    # Single-table resolution (both columns native to TRANSACTIONS) -- this
    # test is about session persistence, not the join fix, so keep the rest
    # of the pipeline as simple as possible.
    response_fn = _make_response_fn(
        intent_json=json.dumps({"intent": "comparison", "entities": ["units", "market"]}),
        semantic_retrieval_json=json.dumps(
            {
                "matches": [
                    {"term": "market", "catalog_column_id": market_id_id, "rationale": "x"},
                    {"term": "units", "catalog_column_id": units_id, "rationale": "x"},
                ]
            }
        ),
    )

    redis_client = redis.Redis.from_url("redis://localhost:6379")
    agent = _build_agent(response_fn=response_fn, redis_client=redis_client)
    session_id: str | None = None

    try:
        first_output = await agent.run(
            RequestOrchestratorInput(
                request_context=_request_context(trace_id="orchestrator-session-1"),
                payload=RequestOrchestratorPayload(
                    question="What is the total units traded by market?",
                    data_source_id=data_source_id,
                    session_id=None,
                ),
            )
        )
        session_id = first_output.result.session_id
        assert session_id.startswith("sess_")

        # Inspect the real Redis key directly -- not just trusting the
        # agent's own report that it persisted something.
        cache_key = build_cache_key(tenant_id=_TENANT_ID, session_id=session_id)
        raw = redis_client.get(cache_key)
        assert raw is not None, f"no Redis key found at {cache_key!r} after the first call"
        stored_history = json.loads(raw)
        assert len(stored_history) == 1
        assert stored_history[0]["raw_question"] == "What is the total units traded by market?"

        second_output = await agent.run(
            RequestOrchestratorInput(
                request_context=_request_context(trace_id="orchestrator-session-2"),
                payload=RequestOrchestratorPayload(
                    question="And what about last quarter?",
                    data_source_id=data_source_id,
                    session_id=session_id,
                ),
            )
        )
        assert second_output.result.session_id == session_id

        # A direct, real Session/Context Manager "get" confirms the second
        # call's turn was appended on top of the first, not overwritten.
        session_agent = SessionContextManagerAgent(cast(CacheClientProtocol, redis_client))
        get_output = await session_agent.run(
            SessionContextManagerInput(
                request_context=_request_context(trace_id="orchestrator-session-verify"),
                payload=SessionContextManagerPayload(session_id=session_id, operation="get"),
            )
        )
        assert get_output.result.turn_count == 2
        print(
            f"\nReal session round-trip: session_id={session_id}, "
            f"turn_count after two real calls={get_output.result.turn_count}"
        )
    finally:
        if session_id is not None:
            redis_client.delete(build_cache_key(tenant_id=_TENANT_ID, session_id=session_id))
        redis_client.close()


@pytest.mark.neo4j_integration
@pytest.mark.snowflake_integration
@pytest.mark.opa_integration
@pytest.mark.redis_integration
@pytest.mark.asyncio
async def test_unresolvable_schema_mapping_triggers_a_real_clarification() -> None:
    catalog_session_factory = get_session_factory(get_engine(MetadataCatalogSettings()))
    data_source_id = _real_data_source_id(catalog_session_factory)

    # A gq_007/gq_010-shaped scenario (see LIMITATIONS.md item 38): entities
    # that resolve to nothing anywhere -- Ontology finds no BusinessConcept
    # or RelationshipConcept match, and the fake Semantic Retrieval response
    # below (unlike the other two tests) deliberately returns zero matches,
    # forcing Schema Mapping's real `tables == []` outcome.
    response_fn = _make_response_fn(
        intent_json=json.dumps(
            {"intent": "comparison", "entities": ["zorbnak coefficient", "flibbertigibbet index"]}
        ),
        semantic_retrieval_json=json.dumps({"matches": []}),
        clarification_json=json.dumps(
            {
                "needs_clarification": True,
                "clarifying_question": (
                    "I couldn't find data matching 'zorbnak coefficient' -- could "
                    "you tell me which real column or metric you mean?"
                ),
            }
        ),
    )

    redis_client = redis.Redis.from_url("redis://localhost:6379")
    agent = _build_agent(response_fn=response_fn, redis_client=redis_client)

    try:
        output = await agent.run(
            RequestOrchestratorInput(
                request_context=_request_context(trace_id="orchestrator-clarification"),
                payload=RequestOrchestratorPayload(
                    question="What is the zorbnak coefficient by flibbertigibbet index?",
                    data_source_id=data_source_id,
                ),
            )
        )
        result = output.result

        assert result.outcome == "needs_clarification"
        assert result.clarifying_question is not None
        assert result.clarifying_question.strip() != ""
        assert result.failure_stage is None
        assert output.confidence == 0.5
        print(f"\nReal clarification trigger: {result.clarifying_question!r}")
    finally:
        redis_client.close()
