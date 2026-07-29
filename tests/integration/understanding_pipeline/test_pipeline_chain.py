"""Integration test: chains all six Understanding-domain agents for real.

REQUIRES A LIVE, REACHABLE POSTGRES AND NEO4J -- mirrors
`tests/integration/metadata_catalog/test_migrations.py` and
`tests/integration/knowledge_graph/test_ingestion_integration.py`'s stance
exactly: this test does NOT skip gracefully if either is unreachable, since
`tests/integration/` is documented as running against the actual
docker-compose stack in a separate CI job.

Point this at the real services via the same env-var convention every
other NaviGraph service uses: `POSTGRES_HOST`/`POSTGRES_PORT`,
`NEO4J_URI`. Defaults are the docker-compose in-network hostnames; when
running from the host against `infra/docker-compose.yml`'s published
ports, set `POSTGRES_HOST=localhost POSTGRES_PORT=5433
NEO4J_URI=bolt://localhost:7687` first.

Uses the real, already-crawled+ingested `FIDELITY_POC` data source (from
Phase 2/3 verification) rather than crawling a fresh one -- Metadata
Discovery and Ontology only need already-populated Postgres/Neo4j, not a
live Snowflake connection, so this test has no Snowflake dependency at all.
If that data source doesn't exist (e.g. a clean environment that never ran
Phase 2/3's live-Snowflake verification), the test fails with a clear
message telling you how to (re)create it, rather than silently skipping.

Worked example from Phase 4 planning: "What is the total transaction
volume by market?" -- traced through Conversation (no-op, first turn) ->
Intent Understanding (canned: intent=comparison,
entities=["units traded", "market"]) -> Metadata Discovery (real catalog
read) -> Ontology (real graph resolution: "units traded" resolves via the
real glossary, "market" does not -- it's a Tier-1 reference entity, not a
glossaried business term) -> Semantic Retrieval (canned: resolves "market"
against the real candidate list, specifically validated to be a real
`catalog_column_id` from Metadata Discovery's output, not a hardcoded
guess) -> Schema Mapping (pure assembly). Asserts the final
`SchemaMappingResult` identifies `TRANSACTIONS.UNITS` as a measure and a
market-dimension column, with no unmapped terms.
"""

from __future__ import annotations

import pytest
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
from navigraph_agents.understanding.ontology.agent import OntologyAgent
from navigraph_agents.understanding.ontology.contracts import (
    OntologyInput,
    OntologyPayload,
)
from navigraph_agents.understanding.schema_mapping.agent import SchemaMappingAgent
from navigraph_agents.understanding.schema_mapping.contracts import (
    CatalogInventoryEntry,
    ConceptResolution,
    RelationshipResolution,
    SchemaMappingInput,
    SchemaMappingPayload,
    TermMatch,
)
from navigraph_agents.understanding.semantic_retrieval.agent import (
    SemanticRetrievalAgent,
)
from navigraph_agents.understanding.semantic_retrieval.contracts import (
    RetrievalCandidate,
    SemanticRetrievalInput,
    SemanticRetrievalPayload,
)
from navigraph_catalog.api import list_data_sources
from navigraph_catalog.db import get_engine, get_session_factory, session_scope
from navigraph_catalog.settings import MetadataCatalogSettings
from navigraph_kg.client import Neo4jClient
from navigraph_kg.settings import KnowledgeGraphSettings
from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient

pytestmark = pytest.mark.postgres_integration

_TENANT_ID = "navikenz-poc"
_DATA_SOURCE_NAME = "fidelity_poc_snowflake_v2"
_QUESTION = "What is the total transaction volume by market?"


def _request_context() -> RequestContext:
    return RequestContext(
        tenant_id=_TENANT_ID,
        user_id="integration-test-user",
        trace_id="understanding-pipeline-chain-test",
        roles=["analyst"],
    )


@pytest.mark.neo4j_integration
@pytest.mark.asyncio
async def test_understanding_pipeline_chain_answers_a_real_business_question() -> None:
    catalog_settings = MetadataCatalogSettings()
    engine = get_engine(catalog_settings)
    session_factory = get_session_factory(engine)

    with session_scope(session_factory) as session:
        data_sources = list_data_sources(session, tenant_id=_TENANT_ID)
        matching = [ds for ds in data_sources if ds.name == _DATA_SOURCE_NAME]
        assert matching, (
            f"No data source named {_DATA_SOURCE_NAME!r} for tenant {_TENANT_ID!r} -- "
            "this test reuses the real FIDELITY_POC data source registered during "
            "Phase 2/3 verification rather than crawling a fresh one. Re-run the "
            "Phase 2 crawler and Phase 3 knowledge-graph ingestion against a real "
            "Snowflake account first (see BUILD_LOG.md's 2026-07-29 entries)."
        )
        data_source_id = matching[0].id

    neo4j_client = Neo4jClient(KnowledgeGraphSettings())
    connectivity = neo4j_client.test_connection()
    assert connectivity.success, f"Neo4j unreachable: {connectivity.message}"

    request_context = _request_context()

    # --- 1. Conversation: first turn, no history -> deterministic short-circuit ---
    conversation_agent = ConversationAgent(llm_client=FakeLLMClient())
    conversation_output = await conversation_agent.run(
        ConversationInput(
            request_context=request_context,
            payload=ConversationPayload(question=_QUESTION, conversation_history=[]),
        )
    )
    assert conversation_output.result.is_follow_up is False
    assert conversation_output.result.resolved_question == _QUESTION
    resolved_question = conversation_output.result.resolved_question

    # --- 2. Intent Understanding: canned classification (real LLM behavior is
    # exercised separately by its own llm_integration test) ---
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
    assert intent_output.result.intent == "comparison"
    assert intent_output.result.entities == ["units traded", "market"]

    # --- 3. Metadata Discovery: real Postgres catalog read ---
    metadata_discovery_agent = MetadataDiscoveryAgent(session_factory=session_factory)
    metadata_output = await metadata_discovery_agent.run(
        MetadataDiscoveryInput(
            request_context=request_context,
            payload=MetadataDiscoveryPayload(data_source_id=str(data_source_id)),
        )
    )
    catalog_columns = metadata_output.result.columns
    assert len(catalog_columns) > 0, "expected real crawled columns from the FIDELITY_POC catalog"

    # NOTE on STAGING vs FAR_TRANS: the real STAGING.SCHEMA_ENRICHMENT glossary
    # (Phase 3) only references staging_-prefixed table names
    # (e.g. `staging_transactions`), so every real BusinessConcept -> Column
    # mapping in the graph is anchored to the STAGING schema's copies, not
    # FAR_TRANS's -- confirmed live while writing this test (Ontology
    # resolved "units traded" to STAGING.STAGING_TRANSACTIONS.UNITS, not
    # FAR_TRANS.TRANSACTIONS.UNITS, even though both real columns exist).
    # This test's expectations are written against that real, current
    # behavior. Whether FAR_TRANS should be the canonical resolution target
    # instead (STAGING sounds like an ETL staging area, not the intended
    # long-term query target) is a real, worth-revisiting question for
    # whichever later phase owns SQL generation -- logged in LIMITATIONS.md,
    # not silently worked around here.
    units_entry = next(
        (
            c
            for c in catalog_columns
            if c.table_name.upper() == "STAGING_TRANSACTIONS" and c.column_name.upper() == "UNITS"
        ),
        None,
    )
    market_id_entry = next(
        (
            c
            for c in catalog_columns
            if c.table_name.upper() == "STAGING_TRANSACTIONS"
            and c.column_name.upper() == "MARKETID"
        ),
        None,
    )
    assert units_entry is not None, (
        "expected STAGING_TRANSACTIONS.UNITS in the real catalog inventory"
    )
    assert market_id_entry is not None, (
        "expected STAGING_TRANSACTIONS.MARKETID in the real catalog inventory"
    )

    # --- 4. Ontology: real Neo4j graph resolution ---
    ontology_agent = OntologyAgent(client=neo4j_client)
    ontology_output = await ontology_agent.run(
        OntologyInput(
            request_context=request_context,
            payload=OntologyPayload(entities=intent_output.result.entities, intent="comparison"),
        )
    )
    ontology_result = ontology_output.result

    units_resolution = next(
        (r for r in ontology_result.concept_resolutions if r.term == "units traded"), None
    )
    assert units_resolution is not None
    assert units_resolution.resolved is True, (
        "'units traded' should resolve via the real SCHEMA_ENRICHMENT-sourced glossary "
        "('Units Traded' business concept over TRANSACTIONS.UNITS)"
    )
    assert units_resolution.catalog_column_id == units_entry.catalog_column_id

    assert "market" in ontology_result.unresolved_terms, (
        "'market' is a Tier-1 reference entity, not a glossaried business concept -- "
        "it should NOT resolve via Ontology, and must fall through to Semantic Retrieval"
    )

    # --- 5. Semantic Retrieval: canned LLM match against the REAL candidate list ---
    retrieval_candidates = [
        RetrievalCandidate(
            catalog_column_id=c.catalog_column_id,
            table_name=c.table_name,
            column_name=c.column_name,
            business_name=c.business_name,
            synonyms=c.synonyms,
            description=c.description,
        )
        for c in catalog_columns
    ]
    retrieval_llm = FakeLLMClient(
        response=(
            '{"matches": [{"term": "market", '
            f'"catalog_column_id": "{market_id_entry.catalog_column_id}", '
            '"rationale": "MARKETID is the transaction-level market dimension"}]}'
        )
    )
    semantic_retrieval_agent = SemanticRetrievalAgent(llm_client=retrieval_llm)
    retrieval_output = await semantic_retrieval_agent.run(
        SemanticRetrievalInput(
            request_context=request_context,
            payload=SemanticRetrievalPayload(
                question=resolved_question,
                unresolved_terms=ontology_result.unresolved_terms,
                candidates=retrieval_candidates,
            ),
        )
    )
    assert not retrieval_output.errors, f"unexpected errors: {retrieval_output.errors}"
    market_match = next((m for m in retrieval_output.result.matches if m.term == "market"), None)
    assert market_match is not None
    assert market_match.matched is True
    assert market_match.catalog_column_id == market_id_entry.catalog_column_id

    # --- 6. Schema Mapping: pure assembly, no external calls ---
    schema_mapping_agent = SchemaMappingAgent()
    schema_mapping_output = await schema_mapping_agent.run(
        SchemaMappingInput(
            request_context=request_context,
            payload=SchemaMappingPayload(
                intent="comparison",
                concept_resolutions=[
                    ConceptResolution(**r.model_dump()) for r in ontology_result.concept_resolutions
                ],
                relationship_resolutions=[
                    RelationshipResolution(**r.model_dump())
                    for r in ontology_result.relationship_resolutions
                ],
                semantic_matches=[
                    TermMatch(**m.model_dump()) for m in retrieval_output.result.matches
                ],
                catalog_inventory=[
                    CatalogInventoryEntry(**c.model_dump()) for c in catalog_columns
                ],
            ),
        )
    )
    result = schema_mapping_output.result

    assert result.unmapped_terms == [], f"expected no unmapped terms, got {result.unmapped_terms}"
    assert "STAGING_TRANSACTIONS" in result.tables

    units_ref = next((c for c in result.columns if c.column_name.upper() == "UNITS"), None)
    market_ref = next((c for c in result.columns if c.column_name.upper() == "MARKETID"), None)
    assert units_ref is not None
    assert market_ref is not None
    assert units_ref.role == "measure", (
        f"expected UNITS (numeric, comparison intent) to be a measure, got {units_ref.role}"
    )
    assert market_ref.role == "dimension", (
        f"expected MARKETID (non-numeric) to be a dimension, got {market_ref.role}"
    )

    neo4j_client.close()
