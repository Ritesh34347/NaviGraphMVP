"""Integration test: real Lineage Recorder persistence + retrieval against
live Postgres.

REQUIRES A LIVE, REACHABLE POSTGRES -- mirrors every other integration
test's stance exactly: this test does NOT skip gracefully if it's
unreachable, since `tests/integration/` is documented as running against
the actual docker-compose stack in a separate CI job.

Chains three real Understanding-domain agent calls (Conversation, Intent
Understanding -- both via `FakeLLMClient`, no real LLM call needed to prove
lineage persistence -- and Metadata Discovery, a real Postgres read against
the already-crawled `fidelity_poc_snowflake_v2` catalog), records each
one's real `lineage_events` via the real Lineage Recorder agent against
live Postgres, then real-queries `get_trace()` and asserts the full,
correctly-ordered real chain comes back -- plus a real idempotency proof:
re-recording the same events a second time is a no-op, never a duplicate
row.

Point this at the real service via the same env-var convention every other
NaviGraph integration test uses: `POSTGRES_HOST`/`POSTGRES_PORT`. Defaults
are the docker-compose in-network hostname; when running from the host
against `infra/docker-compose.yml`'s published ports, set
`POSTGRES_HOST=localhost POSTGRES_PORT=5433` first.
"""

from __future__ import annotations

import uuid

import pytest
from navigraph_agents.ops.lineage_recorder.agent import LineageRecorderAgent
from navigraph_agents.ops.lineage_recorder.contracts import (
    LineageRecorderInput,
    LineageRecorderPayload,
)
from navigraph_agents.understanding.conversation.agent import ConversationAgent
from navigraph_agents.understanding.conversation.contracts import (
    ConversationInput,
    ConversationPayload,
)
from navigraph_agents.understanding.intent_understanding.agent import (
    IntentUnderstandingAgent,
)
from navigraph_agents.understanding.intent_understanding.contracts import (
    IntentUnderstandingInput,
    IntentUnderstandingPayload,
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
from navigraph_lineage.api import get_trace
from navigraph_lineage.db import get_engine as get_lineage_engine
from navigraph_lineage.db import get_session_factory as get_lineage_session_factory
from navigraph_lineage.db import session_scope as lineage_session_scope
from navigraph_lineage.settings import LineageSettings
from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient

pytestmark = pytest.mark.postgres_integration

_TENANT_ID = "navikenz-poc"
_DATA_SOURCE_NAME = "fidelity_poc_snowflake_v2"
_QUESTION = "What is the total transaction volume by market?"


@pytest.mark.asyncio
async def test_lineage_recorder_persists_and_reassembles_a_real_trace() -> None:
    catalog_settings = MetadataCatalogSettings()
    catalog_engine = get_engine(catalog_settings)
    catalog_session_factory = get_session_factory(catalog_engine)

    with session_scope(catalog_session_factory) as session:
        data_sources = list_data_sources(session, tenant_id=_TENANT_ID)
        matching = [ds for ds in data_sources if ds.name == _DATA_SOURCE_NAME]
        assert matching, f"No data source named {_DATA_SOURCE_NAME!r} for tenant {_TENANT_ID!r}"
        data_source_id = matching[0].id

    lineage_settings = LineageSettings(
        postgres_host=catalog_settings.postgres_host,
        postgres_port=catalog_settings.postgres_port,
        postgres_user=catalog_settings.postgres_user,
        postgres_password=catalog_settings.postgres_password,
        postgres_db=catalog_settings.postgres_db,
    )
    lineage_engine = get_lineage_engine(lineage_settings)
    lineage_session_factory = get_lineage_session_factory(lineage_engine)

    # Fresh, real trace_id per test run -- LineageEvent.event_id is a real,
    # random uuid4-derived string generated fresh by each agent call, so
    # collisions with prior runs are already impossible, but a fresh
    # trace_id also keeps get_trace()'s result set to exactly this run's
    # events, not accumulated across repeated local test runs.
    trace_id = f"lineage-pipeline-test-{uuid.uuid4().hex}"
    request_context = RequestContext(
        tenant_id=_TENANT_ID,
        user_id="integration-test-user",
        trace_id=trace_id,
        roles=["analyst"],
    )

    lineage_recorder_agent = LineageRecorderAgent(session_factory=lineage_session_factory)

    # --- 1. Conversation: first turn, no history -> deterministic short-circuit ---
    conversation_agent = ConversationAgent(llm_client=FakeLLMClient())
    conversation_output = await conversation_agent.run(
        ConversationInput(
            request_context=request_context,
            payload=ConversationPayload(question=_QUESTION, conversation_history=[]),
        )
    )
    resolved_question = conversation_output.result.resolved_question

    record_output_1 = await lineage_recorder_agent.run(
        LineageRecorderInput(
            request_context=request_context,
            payload=LineageRecorderPayload(events=conversation_output.lineage_events),
        )
    )
    assert not record_output_1.errors
    assert record_output_1.result.recorded_count == len(conversation_output.lineage_events)
    assert record_output_1.result.duplicate_count == 0

    # --- 2. Intent Understanding: canned classification ---
    intent_llm = FakeLLMClient(
        response='{"intent": "comparison", "entities": ["units traded", "market"]}'
    )
    intent_agent = IntentUnderstandingAgent(llm_client=intent_llm)
    intent_output = await intent_agent.run(
        IntentUnderstandingInput(
            request_context=request_context,
            payload=IntentUnderstandingPayload(question=resolved_question),
        )
    )

    record_output_2 = await lineage_recorder_agent.run(
        LineageRecorderInput(
            request_context=request_context,
            payload=LineageRecorderPayload(events=intent_output.lineage_events),
        )
    )
    assert not record_output_2.errors
    assert record_output_2.result.recorded_count == len(intent_output.lineage_events)

    # --- 3. Metadata Discovery: real Postgres catalog read ---
    metadata_discovery_agent = MetadataDiscoveryAgent(session_factory=catalog_session_factory)
    metadata_output = await metadata_discovery_agent.run(
        MetadataDiscoveryInput(
            request_context=request_context,
            payload=MetadataDiscoveryPayload(data_source_id=str(data_source_id)),
        )
    )

    record_output_3 = await lineage_recorder_agent.run(
        LineageRecorderInput(
            request_context=request_context,
            payload=LineageRecorderPayload(events=metadata_output.lineage_events),
        )
    )
    assert not record_output_3.errors
    assert record_output_3.result.recorded_count == len(metadata_output.lineage_events)

    # --- Real read: the full, assembled, correctly-ordered chain ---
    with lineage_session_scope(lineage_session_factory) as session:
        trace_records = get_trace(session, trace_id=trace_id, tenant_id=_TENANT_ID)

    real_agent_names_in_order = [record.agent_name for record in trace_records]
    assert real_agent_names_in_order == [
        "understanding.conversation",
        "understanding.intent_understanding",
        "understanding.metadata_discovery",
    ]

    expected_event_ids = {
        event.event_id
        for output in (conversation_output, intent_output, metadata_output)
        for event in output.lineage_events
    }
    real_event_ids = {record.event_id for record in trace_records}
    assert real_event_ids == expected_event_ids

    print(
        f"\nReal assembled lineage trace ({trace_id}): "
        f"{[r.agent_name for r in trace_records]}"
    )

    # --- Real idempotency proof: re-recording the same events is a no-op ---
    reconciliation_output = await lineage_recorder_agent.run(
        LineageRecorderInput(
            request_context=request_context,
            payload=LineageRecorderPayload(events=conversation_output.lineage_events),
        )
    )
    assert not reconciliation_output.errors
    assert reconciliation_output.result.recorded_count == 0
    assert reconciliation_output.result.duplicate_count == len(
        conversation_output.lineage_events
    )

    with lineage_session_scope(lineage_session_factory) as session:
        trace_records_after_replay = get_trace(session, trace_id=trace_id, tenant_id=_TENANT_ID)
    assert len(trace_records_after_replay) == len(trace_records), (
        "re-recording the same events must never duplicate rows"
    )
    print(
        f"\nReal idempotency proof: re-recording the same {len(conversation_output.lineage_events)} "
        f"event(s) yielded recorded_count=0, duplicate_count="
        f"{reconciliation_output.result.duplicate_count}, and the trace's row count "
        f"({len(trace_records_after_replay)}) is unchanged."
    )
