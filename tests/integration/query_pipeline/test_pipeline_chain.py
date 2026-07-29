"""Integration test: chains all six Query-domain agents for real, against
the live docker-compose Postgres, Neo4j, Redis, and a real Snowflake
account -- the first integration test in this repo that actually executes
generated SQL against a live external system.

REQUIRES LIVE, REACHABLE POSTGRES, NEO4J, REDIS, AND A REAL SNOWFLAKE
ACCOUNT -- mirrors `tests/integration/understanding_pipeline/test_pipeline_chain.py`'s
stance exactly: this test does NOT skip gracefully if any of these are
unreachable, since `tests/integration/` is documented as running against the
actual docker-compose stack (plus real Snowflake credentials) in a separate
CI job.

Point this at the real services via the same env-var convention every other
NaviGraph integration test uses: `POSTGRES_HOST`/`POSTGRES_PORT`,
`NEO4J_URI`/`NEO4J_PASSWORD`, `REDIS_URL`, and the real `SNOWFLAKE_*`
credentials. Defaults are the docker-compose in-network hostnames; when
running from the host against `infra/docker-compose.yml`'s published ports,
set `POSTGRES_HOST=localhost POSTGRES_PORT=5433 NEO4J_URI=bolt://localhost:7687
REDIS_URL=redis://localhost:6379` plus the real `SNOWFLAKE_*` values first.

Worked example, same as the Understanding-domain pipeline-chain test (this
test picks up exactly where that one leaves off): "What is the total
transaction volume by market?" -- runs the real Understanding-domain chain
first (Conversation -> Intent Understanding -> Metadata Discovery -> Ontology
-> Semantic Retrieval -> Schema Mapping) to get a real `SchemaMappingResult`
identifying `STAGING.STAGING_TRANSACTIONS.UNITS` as a measure and
`STAGING.STAGING_TRANSACTIONS.MARKETID` as a dimension (see that test's own
docstring for the STAGING vs FAR_TRANS note -- inherited here unchanged, not
re-litigated), then feeds that real result through all six Query agents:

  Data Source Discovery (real `test_connection()` against the real Snowflake
  account) -> SQL Generation (deterministic skeleton, no LLM call needed --
  the question has no relative-date/comparison trigger phrase) -> SQL
  Optimization (LIMIT injection + audit comment) -> Execution Planning (the
  real read-only-SELECT safety gate) -> Data Federation (real execution via
  the direct-connector route) -> Caching (real Redis lookup/store/lookup).

Also proves the safety gate for real: a second, deliberately malicious
statement containing a stacked `; DROP TABLE ...` is run through the SAME
Execution Planning agent invocation and asserted to land in `rejected`, never
in `plans` -- i.e. it structurally never reaches Data Federation at all.
"""

from __future__ import annotations

# Import side effect only: registers "snowflake" in
# `navigraph_connectors.registry` (see `navigraph_connectors/snowflake/__init__.py`'s
# `register_connector("snowflake", SnowflakeConnector)` call). Nothing in
# this test's own code references `SnowflakeConnector` directly -- Data
# Source Discovery and Data Federation resolve it themselves via
# `get_connector_class(data_source.source_type)`, but only if this
# registration has actually happened somewhere in the process first.
import navigraph_connectors.snowflake  # noqa: F401
import pytest
from navigraph_agents.query.caching.agent import CacheClientProtocol, CachingAgent
from navigraph_agents.query.caching.contracts import CachingInput, CachingPayload
from navigraph_agents.query.data_federation.agent import DataFederationAgent
from navigraph_agents.query.data_federation.contracts import (
    DataFederationInput,
    DataFederationPayload,
)
from navigraph_agents.query.data_federation.contracts import (
    ExecutionPlan as FederationExecutionPlan,
)
from navigraph_agents.query.data_source_discovery.agent import DataSourceDiscoveryAgent
from navigraph_agents.query.data_source_discovery.contracts import (
    DataSourceDiscoveryInput,
    DataSourceDiscoveryPayload,
)
from navigraph_agents.query.execution_planning.agent import ExecutionPlanningAgent
from navigraph_agents.query.execution_planning.contracts import (
    ExecutionPlanningInput,
    ExecutionPlanningPayload,
)
from navigraph_agents.query.execution_planning.contracts import (
    OptimizedSql as PlanningOptimizedSql,
)
from navigraph_agents.query.sql_generation.agent import SqlGenerationAgent
from navigraph_agents.query.sql_generation.contracts import (
    JoinSpec as GenerationJoinSpec,
)
from navigraph_agents.query.sql_generation.contracts import (
    ResolvedColumnRef as GenerationResolvedColumnRef,
)
from navigraph_agents.query.sql_generation.contracts import (
    ResolvedDataSource as GenerationResolvedDataSource,
)
from navigraph_agents.query.sql_generation.contracts import (
    SchemaMappingResult as GenerationSchemaMappingResult,
)
from navigraph_agents.query.sql_generation.contracts import (
    SqlGenerationInput,
    SqlGenerationPayload,
)
from navigraph_agents.query.sql_optimization.agent import SqlOptimizationAgent
from navigraph_agents.query.sql_optimization.contracts import (
    GeneratedSql as OptimizationGeneratedSql,
)
from navigraph_agents.query.sql_optimization.contracts import (
    SqlOptimizationInput,
    SqlOptimizationPayload,
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
        trace_id="query-pipeline-chain-test",
        roles=["analyst"],
    )


@pytest.mark.neo4j_integration
@pytest.mark.snowflake_integration
@pytest.mark.redis_integration
@pytest.mark.asyncio
async def test_query_pipeline_chain_answers_a_real_business_question() -> None:
    import redis

    catalog_settings = MetadataCatalogSettings()
    engine = get_engine(catalog_settings)
    session_factory = get_session_factory(engine)

    with session_scope(session_factory) as session:
        data_sources = list_data_sources(session, tenant_id=_TENANT_ID)
        matching = [ds for ds in data_sources if ds.name == _DATA_SOURCE_NAME]
        assert matching, (
            f"No data source named {_DATA_SOURCE_NAME!r} for tenant {_TENANT_ID!r} -- "
            "see tests/integration/understanding_pipeline/test_pipeline_chain.py's "
            "identical assertion for how to (re)create it."
        )
        data_source_id = matching[0].id

    neo4j_client = Neo4jClient(KnowledgeGraphSettings())
    connectivity = neo4j_client.test_connection()
    assert connectivity.success, f"Neo4j unreachable: {connectivity.message}"

    request_context = _request_context()

    # ================== Understanding domain (real, same as Phase 4) ==================

    conversation_agent = ConversationAgent(llm_client=FakeLLMClient())
    conversation_output = await conversation_agent.run(
        ConversationInput(
            request_context=request_context,
            payload=ConversationPayload(question=_QUESTION, conversation_history=[]),
        )
    )
    resolved_question = conversation_output.result.resolved_question

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
    intent = intent_output.result.intent
    assert intent == "comparison"

    metadata_discovery_agent = MetadataDiscoveryAgent(session_factory=session_factory)
    metadata_output = await metadata_discovery_agent.run(
        MetadataDiscoveryInput(
            request_context=request_context,
            payload=MetadataDiscoveryPayload(data_source_id=str(data_source_id)),
        )
    )
    catalog_columns = metadata_output.result.columns
    assert len(catalog_columns) > 0

    market_id_entry = next(
        (
            c
            for c in catalog_columns
            if c.table_name.upper() == "STAGING_TRANSACTIONS"
            and c.column_name.upper() == "MARKETID"
        ),
        None,
    )
    assert market_id_entry is not None

    ontology_agent = OntologyAgent(client=neo4j_client)
    ontology_output = await ontology_agent.run(
        OntologyInput(
            request_context=request_context,
            payload=OntologyPayload(entities=intent_output.result.entities, intent="comparison"),
        )
    )
    ontology_result = ontology_output.result

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
    assert not retrieval_output.errors

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
    schema_mapping_result = schema_mapping_output.result
    assert schema_mapping_result.unmapped_terms == []
    assert "STAGING_TRANSACTIONS" in schema_mapping_result.tables

    # ================== Query domain (Phase 5, real for the first time) ==================

    # --- 1. Data Source Discovery: real connectivity probe against the real
    # Snowflake account ---
    data_source_discovery_agent = DataSourceDiscoveryAgent(session_factory=session_factory)
    discovery_output = await data_source_discovery_agent.run(
        DataSourceDiscoveryInput(
            request_context=request_context,
            payload=DataSourceDiscoveryPayload(tables=schema_mapping_result.tables),
        )
    )
    assert not discovery_output.errors, f"unexpected errors: {discovery_output.errors}"
    assert discovery_output.result.unresolved_tables == []
    assert all(r.reachable for r in discovery_output.result.resolved), (
        "expected the real Snowflake account to be reachable via test_connection()"
    )
    resolved_data_sources = [
        GenerationResolvedDataSource(
            table_name=r.table_name,
            data_source_id=r.data_source_id,
            source_type=r.source_type,
            reachable=r.reachable,
        )
        for r in discovery_output.result.resolved
    ]
    real_data_source_id = resolved_data_sources[0].data_source_id

    # --- 2. SQL Generation: deterministic skeleton, no LLM call needed (the
    # worked-example question has no relative-date/comparison trigger phrase) ---
    generation_schema_mapping = GenerationSchemaMappingResult(
        tables=schema_mapping_result.tables,
        columns=[
            GenerationResolvedColumnRef(**c.model_dump()) for c in schema_mapping_result.columns
        ],
        joins=[GenerationJoinSpec(**j.model_dump()) for j in schema_mapping_result.joins],
        unmapped_terms=schema_mapping_result.unmapped_terms,
    )
    sql_generation_agent = SqlGenerationAgent(llm_client=FakeLLMClient())
    generation_output = await sql_generation_agent.run(
        SqlGenerationInput(
            request_context=request_context,
            payload=SqlGenerationPayload(
                original_question=resolved_question,
                intent=intent,
                schema_mapping=generation_schema_mapping,
                resolved_data_sources=resolved_data_sources,
            ),
        )
    )
    assert not generation_output.errors, f"unexpected errors: {generation_output.errors}"
    assert generation_output.result.unresolved_predicates == []
    assert len(generation_output.result.statements) == 1
    generated_statement = generation_output.result.statements[0]
    assert generated_statement.data_source_id == real_data_source_id
    assert "STAGING_TRANSACTIONS" in generated_statement.sql
    assert "MARKETID" in generated_statement.sql
    assert "UNITS" in generated_statement.sql

    # --- 3. SQL Optimization: LIMIT injection + audit comment ---
    sql_optimization_agent = SqlOptimizationAgent()
    optimization_output = await sql_optimization_agent.run(
        SqlOptimizationInput(
            request_context=request_context,
            payload=SqlOptimizationPayload(
                statements=[
                    OptimizationGeneratedSql(**generated_statement.model_dump())
                ],
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            ),
        )
    )
    assert len(optimization_output.result.statements) == 1
    optimized_statement = optimization_output.result.statements[0]
    assert "navigraph trace_id=" in optimized_statement.sql
    assert request_context.trace_id in optimized_statement.sql
    assert "LIMIT" in optimized_statement.sql.upper()

    # --- 4. Execution Planning: the real read-only-SELECT safety gate,
    # exercised against BOTH a legitimate statement and a deliberately
    # malicious stacked-query statement in the SAME call ---
    malicious_statement = PlanningOptimizedSql(
        data_source_id=real_data_source_id,
        sql="SELECT 1; DROP TABLE STAGING.STAGING_TRANSACTIONS",
        params={},
        applied_rules=[],
        estimated_row_count=None,
    )
    execution_planning_agent = ExecutionPlanningAgent()
    planning_output = await execution_planning_agent.run(
        ExecutionPlanningInput(
            request_context=request_context,
            payload=ExecutionPlanningPayload(
                statements=[
                    PlanningOptimizedSql(**optimized_statement.model_dump()),
                    malicious_statement,
                ],
            ),
        )
    )
    assert len(planning_output.result.plans) == 1, (
        "expected exactly one real ExecutionPlan (the legitimate statement) -- "
        "the malicious statement must never become a plan"
    )
    real_plan = planning_output.result.plans[0]
    assert real_plan.data_source_id == real_data_source_id
    assert real_plan.route == "direct_connector"
    assert real_plan.read_only_verified is True

    assert len(planning_output.result.rejected) == 1, (
        "expected exactly one rejection -- the malicious stacked-query statement"
    )
    rejection = planning_output.result.rejected[0]
    assert rejection.code == "rejected_unsafe_statement"
    assert "stacked" in rejection.message.lower() or "multiple sql statements" in (
        rejection.message.lower()
    )
    print(f"\nReal Execution Planning rejection of the malicious statement: {rejection.message}")

    # --- 5. Data Federation: real execution against the real Snowflake
    # account via the direct-connector route -- ONLY the real plan is ever
    # handed to this agent, proving the malicious statement never reaches it ---
    data_federation_agent = DataFederationAgent(catalog_session_factory=session_factory)
    federation_output = await data_federation_agent.run(
        DataFederationInput(
            request_context=request_context,
            payload=DataFederationPayload(
                plans=[FederationExecutionPlan(**real_plan.model_dump())],
            ),
        )
    )
    assert not federation_output.errors, f"unexpected errors: {federation_output.errors}"
    federation_result = federation_output.result
    assert federation_result.federated is False, "only one real data source is registered"
    assert federation_result.final_row_count > 0, "expected real rows back from Snowflake"
    assert "MARKETID" in federation_result.final_columns
    assert federation_result.per_source_results[0].route_used == "direct_connector"
    print(
        f"\nReal Data Federation result: {federation_result.final_row_count} rows, "
        f"columns={federation_result.final_columns}"
    )

    # --- 6. Caching: real Redis lookup (miss) -> store -> lookup (hit) ---
    redis_client = redis.Redis.from_url("redis://localhost:6379")
    cache_key: str | None = None
    try:
        cache_client: CacheClientProtocol = redis_client  # type: ignore[assignment]
        caching_agent = CachingAgent(cache_client=cache_client)

        cache_payload_common = {
            "sql": real_plan.sql,
            "params": real_plan.params,
            "data_source_id": real_plan.data_source_id,
        }

        first_lookup = await caching_agent.run(
            CachingInput(
                request_context=request_context,
                payload=CachingPayload(operation="lookup", **cache_payload_common),
            )
        )
        assert not first_lookup.errors
        assert first_lookup.result.hit is False, (
            "expected a cache miss on first lookup -- if this fails, a prior "
            "test run left a stale entry; the cache key is deterministic per "
            "(tenant, sql, params, data_source_id)"
        )
        cache_key = first_lookup.result.cache_key

        store_output = await caching_agent.run(
            CachingInput(
                request_context=request_context,
                payload=CachingPayload(
                    operation="store",
                    value=federation_result.model_dump(mode="json"),
                    **cache_payload_common,
                ),
            )
        )
        assert not store_output.errors
        assert store_output.result.stored is True
        assert store_output.result.cache_key == cache_key

        second_lookup = await caching_agent.run(
            CachingInput(
                request_context=request_context,
                payload=CachingPayload(operation="lookup", **cache_payload_common),
            )
        )
        assert not second_lookup.errors
        assert second_lookup.result.hit is True, "expected a real cache hit on the second lookup"
        assert second_lookup.result.cached_value is not None
        assert second_lookup.result.cached_value["final_row_count"] == (
            federation_result.final_row_count
        )
    finally:
        # Clean up this test's own cache entry so re-running it doesn't see
        # a stale hit on the first lookup next time.
        if cache_key is not None:
            redis_client.delete(cache_key)
        redis_client.close()

    neo4j_client.close()
