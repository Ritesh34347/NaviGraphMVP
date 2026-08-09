"""NaviGraph agent-runtime FastAPI application.

Exposes:
  - GET  /healthz    -- liveness probe
  - GET  /readyz     -- readiness probe (see NOTE below)
  - GET  /metrics    -- Prometheus metrics (via prometheus-fastapi-instrumentator)
  - POST /agents/understanding/intent_understanding/invoke
  - POST /agents/understanding/conversation/invoke
  - POST /agents/understanding/metadata_discovery/invoke
  - POST /agents/understanding/ontology/invoke
  - POST /agents/understanding/semantic_retrieval/invoke
  - POST /agents/understanding/schema_mapping/invoke
  - POST /agents/query/data_source_discovery/invoke
  - POST /agents/query/sql_generation/invoke
  - POST /agents/query/sql_optimization/invoke
  - POST /agents/query/execution_planning/invoke
  - POST /agents/query/data_federation/invoke
  - POST /agents/query/caching/invoke
  - POST /agents/guardrail/schema_constraint_validator/invoke
  - POST /agents/guardrail/policy_authorization/invoke
  - POST /agents/guardrail/query_cost_estimator/invoke
  - POST /agents/guardrail/pii_exposure_checker/invoke
  - POST /agents/insight/chart_selection/invoke
  - POST /agents/insight/anomaly_outlier_highlighter/invoke
  - POST /agents/insight/grounded_narrative_generation/invoke
  - POST /agents/insight/follow_up_suggestion/invoke
  - POST /agents/ops/lineage_recorder/invoke
  - POST /agents/ops/evaluation_judge/invoke
  - POST /agents/orchestrator/session_context_manager/invoke
  - POST /agents/orchestrator/clarification_coordinator/invoke
  - POST /agents/orchestrator/request_orchestrator/invoke
  - GET  /lineage/{trace_id}  -- real, live retrieval of one trace's full
                                  assembled lineage chain (tenant-scoped via
                                  a required `tenant_id` query param)
                     -- invokes the six Understanding-domain, six Query-domain,
                        four Guardrail-domain, four Insight-domain, two
                        Ops-domain, and three Orchestrator-domain agents

At startup, constructs a real `AnthropicLLMClient` if `ANTHROPIC_API_KEY` is
set, or falls back to a `FakeLLMClient` (logging a warning) so this service
still boots and answers requests locally without a real API key -- useful
for local dev and for the smoke test in tools/scripts/smoke-test.sh. Also
constructs a `navigraph_catalog` session factory, a `navigraph_kg`
`Neo4jClient`, a `navigraph_federation` `TrinoClient`, a real `redis.Redis`
client, and a real `navigraph_shared.opa.HttpOpaClient` for the agents that
need them.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import cast

# Import side effect only: registers "snowflake" in
# `navigraph_connectors.registry` (see
# `navigraph_connectors/snowflake/__init__.py`'s `register_connector(...)`
# call). Without this import somewhere in the process, the registry is
# empty and every connector-dependent agent (Data Source Discovery, Data
# Federation) fails at runtime with "No connector registered for
# source_type='snowflake'" -- a real bug caught live via a direct HTTP call
# against this service after Phase 5's rebuild (unit tests never caught it
# because they inject a fake connector directly, and the pytest-based
# integration test imports this module itself).
import navigraph_connectors.snowflake  # noqa: F401
import redis
from fastapi import FastAPI, HTTPException
from navigraph_catalog.db import get_engine, get_session_factory
from navigraph_federation.trino_client import TrinoClient
from navigraph_kg.client import Neo4jClient
from navigraph_lineage.api import get_trace
from navigraph_lineage.db import get_engine as get_lineage_engine
from navigraph_lineage.db import get_session_factory as get_lineage_session_factory
from navigraph_lineage.db import session_scope as lineage_session_scope
from navigraph_shared.config import get_settings
from navigraph_shared.contracts import AgentInput
from navigraph_shared.llm import AnthropicLLMClient, FakeLLMClient, LLMClient
from navigraph_shared.opa import HttpOpaClient
from navigraph_shared.telemetry import (
    bind_request_context,
    configure_logging,
    get_tracer,
)
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import ValidationError

from navigraph_agents.guardrail.pii_exposure_checker.agent import (
    AGENT_NAME as PII_EXPOSURE_CHECKER_AGENT_NAME,
)
from navigraph_agents.guardrail.pii_exposure_checker.agent import (
    PiiExposureCheckerAgent,
)
from navigraph_agents.guardrail.pii_exposure_checker.contracts import (
    PiiExposureCheckerInput,
)
from navigraph_agents.guardrail.policy_authorization.agent import (
    AGENT_NAME as POLICY_AUTHORIZATION_AGENT_NAME,
)
from navigraph_agents.guardrail.policy_authorization.agent import (
    PolicyAuthorizationAgent,
)
from navigraph_agents.guardrail.policy_authorization.contracts import (
    PolicyAuthorizationInput,
)
from navigraph_agents.guardrail.query_cost_estimator.agent import (
    AGENT_NAME as QUERY_COST_ESTIMATOR_AGENT_NAME,
)
from navigraph_agents.guardrail.query_cost_estimator.agent import (
    QueryCostEstimatorAgent,
)
from navigraph_agents.guardrail.query_cost_estimator.contracts import (
    QueryCostEstimatorInput,
)
from navigraph_agents.guardrail.schema_constraint_validator.agent import (
    AGENT_NAME as SCHEMA_CONSTRAINT_VALIDATOR_AGENT_NAME,
)
from navigraph_agents.guardrail.schema_constraint_validator.agent import (
    SchemaConstraintValidatorAgent,
)
from navigraph_agents.guardrail.schema_constraint_validator.contracts import (
    SchemaConstraintValidatorInput,
)
from navigraph_agents.insight.anomaly_outlier_highlighter.agent import (
    AGENT_NAME as ANOMALY_OUTLIER_HIGHLIGHTER_AGENT_NAME,
)
from navigraph_agents.insight.anomaly_outlier_highlighter.agent import (
    AnomalyOutlierHighlighterAgent,
)
from navigraph_agents.insight.anomaly_outlier_highlighter.contracts import (
    AnomalyDetectionInput,
)
from navigraph_agents.insight.chart_selection.agent import (
    AGENT_NAME as CHART_SELECTION_AGENT_NAME,
)
from navigraph_agents.insight.chart_selection.agent import ChartSelectionAgent
from navigraph_agents.insight.chart_selection.contracts import ChartSelectionInput
from navigraph_agents.insight.follow_up_suggestion.agent import (
    AGENT_NAME as FOLLOW_UP_SUGGESTION_AGENT_NAME,
)
from navigraph_agents.insight.follow_up_suggestion.agent import FollowUpSuggestionAgent
from navigraph_agents.insight.follow_up_suggestion.contracts import (
    FollowUpSuggestionInput,
)
from navigraph_agents.insight.grounded_narrative_generation.agent import (
    AGENT_NAME as GROUNDED_NARRATIVE_GENERATION_AGENT_NAME,
)
from navigraph_agents.insight.grounded_narrative_generation.agent import (
    GroundedNarrativeGenerationAgent,
)
from navigraph_agents.insight.grounded_narrative_generation.contracts import (
    NarrativeGenerationInput,
)
from navigraph_agents.ops.evaluation_judge.agent import (
    AGENT_NAME as EVALUATION_JUDGE_AGENT_NAME,
)
from navigraph_agents.ops.evaluation_judge.agent import EvaluationJudgeAgent
from navigraph_agents.ops.evaluation_judge.contracts import EvaluationJudgeInput
from navigraph_agents.ops.lineage_recorder.agent import (
    AGENT_NAME as LINEAGE_RECORDER_AGENT_NAME,
)
from navigraph_agents.ops.lineage_recorder.agent import LineageRecorderAgent
from navigraph_agents.ops.lineage_recorder.contracts import LineageRecorderInput
from navigraph_agents.orchestrator.clarification_coordinator.agent import (
    AGENT_NAME as CLARIFICATION_COORDINATOR_AGENT_NAME,
)
from navigraph_agents.orchestrator.clarification_coordinator.agent import (
    ClarificationCoordinatorAgent,
)
from navigraph_agents.orchestrator.clarification_coordinator.contracts import (
    ClarificationCoordinatorInput,
)
from navigraph_agents.orchestrator.request_orchestrator.agent import (
    AGENT_NAME as REQUEST_ORCHESTRATOR_AGENT_NAME,
)
from navigraph_agents.orchestrator.request_orchestrator.agent import (
    RequestOrchestratorAgent,
)
from navigraph_agents.orchestrator.request_orchestrator.contracts import (
    RequestOrchestratorInput,
)
from navigraph_agents.orchestrator.session_context_manager.agent import (
    AGENT_NAME as SESSION_CONTEXT_MANAGER_AGENT_NAME,
)
from navigraph_agents.orchestrator.session_context_manager.agent import (
    CacheClientProtocol as SessionCacheClientProtocol,
)
from navigraph_agents.orchestrator.session_context_manager.agent import (
    SessionContextManagerAgent,
)
from navigraph_agents.orchestrator.session_context_manager.contracts import (
    SessionContextManagerInput,
)
from navigraph_agents.query.caching.agent import AGENT_NAME as CACHING_AGENT_NAME
from navigraph_agents.query.caching.agent import CacheClientProtocol, CachingAgent
from navigraph_agents.query.caching.contracts import CachingInput
from navigraph_agents.query.data_federation.agent import (
    AGENT_NAME as DATA_FEDERATION_AGENT_NAME,
)
from navigraph_agents.query.data_federation.agent import DataFederationAgent
from navigraph_agents.query.data_federation.contracts import DataFederationInput
from navigraph_agents.query.data_source_discovery.agent import (
    AGENT_NAME as DATA_SOURCE_DISCOVERY_AGENT_NAME,
)
from navigraph_agents.query.data_source_discovery.agent import DataSourceDiscoveryAgent
from navigraph_agents.query.data_source_discovery.contracts import (
    DataSourceDiscoveryInput,
)
from navigraph_agents.query.execution_planning.agent import (
    AGENT_NAME as EXECUTION_PLANNING_AGENT_NAME,
)
from navigraph_agents.query.execution_planning.agent import ExecutionPlanningAgent
from navigraph_agents.query.execution_planning.contracts import ExecutionPlanningInput
from navigraph_agents.query.sql_generation.agent import (
    AGENT_NAME as SQL_GENERATION_AGENT_NAME,
)
from navigraph_agents.query.sql_generation.agent import SqlGenerationAgent
from navigraph_agents.query.sql_generation.contracts import SqlGenerationInput
from navigraph_agents.query.sql_optimization.agent import (
    AGENT_NAME as SQL_OPTIMIZATION_AGENT_NAME,
)
from navigraph_agents.query.sql_optimization.agent import SqlOptimizationAgent
from navigraph_agents.query.sql_optimization.contracts import SqlOptimizationInput
from navigraph_agents.registry import AGENT_REGISTRY, get_agent, register
from navigraph_agents.understanding.conversation.agent import (
    AGENT_NAME as CONVERSATION_AGENT_NAME,
)
from navigraph_agents.understanding.conversation.agent import ConversationAgent
from navigraph_agents.understanding.conversation.contracts import ConversationInput
from navigraph_agents.understanding.intent_understanding.agent import (
    AGENT_NAME as INTENT_UNDERSTANDING_AGENT_NAME,
)
from navigraph_agents.understanding.intent_understanding.agent import (
    IntentUnderstandingAgent,
)
from navigraph_agents.understanding.intent_understanding.contracts import (
    IntentUnderstandingInput,
)
from navigraph_agents.understanding.metadata_discovery.agent import (
    AGENT_NAME as METADATA_DISCOVERY_AGENT_NAME,
)
from navigraph_agents.understanding.metadata_discovery.agent import (
    MetadataDiscoveryAgent,
)
from navigraph_agents.understanding.metadata_discovery.contracts import (
    MetadataDiscoveryInput,
)
from navigraph_agents.understanding.ontology.agent import (
    AGENT_NAME as ONTOLOGY_AGENT_NAME,
)
from navigraph_agents.understanding.ontology.agent import OntologyAgent
from navigraph_agents.understanding.ontology.contracts import OntologyInput
from navigraph_agents.understanding.schema_mapping.agent import (
    AGENT_NAME as SCHEMA_MAPPING_AGENT_NAME,
)
from navigraph_agents.understanding.schema_mapping.agent import SchemaMappingAgent
from navigraph_agents.understanding.schema_mapping.contracts import SchemaMappingInput
from navigraph_agents.understanding.semantic_retrieval.agent import (
    AGENT_NAME as SEMANTIC_RETRIEVAL_AGENT_NAME,
)
from navigraph_agents.understanding.semantic_retrieval.agent import (
    SemanticRetrievalAgent,
)
from navigraph_agents.understanding.semantic_retrieval.contracts import (
    SemanticRetrievalInput,
)

logger = configure_logging("navigraph-agent-runtime")
tracer = get_tracer("navigraph-agent-runtime")


def _build_llm_client() -> LLMClient:
    settings = get_settings()
    if settings.anthropic_api_key:
        return AnthropicLLMClient(api_key=settings.anthropic_api_key, model=settings.anthropic_model)

    logger.warning(
        "ANTHROPIC_API_KEY is not set -- falling back to FakeLLMClient. "
        "The agent-runtime will boot and answer requests, but every agent "
        "invocation will use canned/empty LLM responses instead of a real model. "
        "Set ANTHROPIC_API_KEY to use the real Anthropic API."
    )
    return FakeLLMClient()


def _redis_url() -> str:
    """`REDIS_URL` env var (set in `infra/docker-compose.yml`), defaulting
    to the docker-compose service DNS name so this also works with no env
    var set at all inside the compose network."""

    return os.environ.get("REDIS_URL", "redis://redis:6379")


@asynccontextmanager
async def lifespan(app: FastAPI):
    llm_client = _build_llm_client()

    intent_understanding_agent = IntentUnderstandingAgent(llm_client=llm_client, tracer=tracer)
    register(INTENT_UNDERSTANDING_AGENT_NAME, intent_understanding_agent.run)

    conversation_agent = ConversationAgent(llm_client=llm_client, tracer=tracer)
    register(CONVERSATION_AGENT_NAME, conversation_agent.run)

    semantic_retrieval_agent = SemanticRetrievalAgent(llm_client=llm_client, tracer=tracer)
    register(SEMANTIC_RETRIEVAL_AGENT_NAME, semantic_retrieval_agent.run)

    # Deterministic agents (no LLM): Metadata Discovery needs a Postgres
    # catalog session factory, Ontology needs a Neo4j client. Both default
    # to the docker-compose service DNS names (postgres:5432, neo4j:7687)
    # via their own settings classes' defaults -- no host/port override
    # needed when this service runs inside the compose network.
    catalog_session_factory = get_session_factory(get_engine())
    metadata_discovery_agent = MetadataDiscoveryAgent(
        session_factory=catalog_session_factory, tracer=tracer
    )
    register(METADATA_DISCOVERY_AGENT_NAME, metadata_discovery_agent.run)

    neo4j_client = Neo4jClient()
    ontology_agent = OntologyAgent(client=neo4j_client, tracer=tracer)
    register(ONTOLOGY_AGENT_NAME, ontology_agent.run)

    schema_mapping_agent = SchemaMappingAgent(tracer=tracer)
    register(SCHEMA_MAPPING_AGENT_NAME, schema_mapping_agent.run)

    # Query-domain agents (Phase 5).
    data_source_discovery_agent = DataSourceDiscoveryAgent(
        session_factory=catalog_session_factory, tracer=tracer
    )
    register(DATA_SOURCE_DISCOVERY_AGENT_NAME, data_source_discovery_agent.run)

    sql_generation_agent = SqlGenerationAgent(llm_client=llm_client, tracer=tracer)
    register(SQL_GENERATION_AGENT_NAME, sql_generation_agent.run)

    # Guardrail-domain agents (Phase 6). Three of the four consume
    # `GeneratedSql` directly -- the only shape upstream carrying
    # `referenced_tables`/`referenced_columns` -- so they sit here, right
    # after SQL Generation and before SQL Optimization strips that
    # structure away. `HttpOpaClient` is lazy (mirrors `Neo4jClient()`/
    # `TrinoClient()`) -- constructing it here never requires OPA to be
    # reachable at startup; `OPA_URL` defaults to the docker-compose
    # in-network DNS name (see `OpaSettings`).
    schema_constraint_validator_agent = SchemaConstraintValidatorAgent(
        session_factory=catalog_session_factory, tracer=tracer
    )
    register(SCHEMA_CONSTRAINT_VALIDATOR_AGENT_NAME, schema_constraint_validator_agent.run)

    opa_client = HttpOpaClient()
    policy_authorization_agent = PolicyAuthorizationAgent(opa_client=opa_client, tracer=tracer)
    register(POLICY_AUTHORIZATION_AGENT_NAME, policy_authorization_agent.run)

    pii_exposure_checker_agent = PiiExposureCheckerAgent(
        session_factory=catalog_session_factory, tracer=tracer
    )
    register(PII_EXPOSURE_CHECKER_AGENT_NAME, pii_exposure_checker_agent.run)

    sql_optimization_agent = SqlOptimizationAgent(tracer=tracer)
    register(SQL_OPTIMIZATION_AGENT_NAME, sql_optimization_agent.run)

    # Query Cost/Row-Limit Estimator needs `OptimizedSql.estimated_row_count`,
    # which SQL Optimization populates -- so it sits here, after SQL
    # Optimization and before Execution Planning's independent SELECT-only
    # safety gate.
    query_cost_estimator_agent = QueryCostEstimatorAgent(tracer=tracer)
    register(QUERY_COST_ESTIMATOR_AGENT_NAME, query_cost_estimator_agent.run)

    execution_planning_agent = ExecutionPlanningAgent(tracer=tracer)
    register(EXECUTION_PLANNING_AGENT_NAME, execution_planning_agent.run)

    # TrinoClient is lazy (mirrors Neo4jClient) -- constructing it here never
    # requires Trino to be reachable at startup.
    trino_client = TrinoClient()
    data_federation_agent = DataFederationAgent(
        catalog_session_factory=catalog_session_factory,
        trino_client=trino_client,
        tracer=tracer,
    )
    register(DATA_FEDERATION_AGENT_NAME, data_federation_agent.run)

    # Real redis.Redis client, satisfying CachingAgent's minimal
    # get/set(ex=...) protocol exactly -- decode_responses=False (the
    # default) so `get` returns bytes, matching the protocol CachingAgent
    # was built against. redis-py's own stubs type `get`/`set` more loosely
    # (str|bytes|memoryview in, bytes|str|None out) to cover the
    # decode_responses=True case we don't use here, so mypy can't verify
    # the narrower protocol structurally -- the cast documents that the
    # decode_responses=False runtime contract actually matches.
    redis_client = redis.Redis.from_url(_redis_url())
    caching_agent = CachingAgent(cache_client=cast(CacheClientProtocol, redis_client), tracer=tracer)
    register(CACHING_AGENT_NAME, caching_agent.run)

    # Insight-domain agents (Phase 7). Chart Selection and Anomaly/Outlier
    # Highlighter are pure functions, no external client dependency, same
    # as the Guardrail domain's own deterministic agents. Grounded
    # Narrative Generation and Follow-up Suggestion take the same shared
    # `llm_client` every other LLM-backed agent uses.
    chart_selection_agent = ChartSelectionAgent(tracer=tracer)
    register(CHART_SELECTION_AGENT_NAME, chart_selection_agent.run)

    anomaly_outlier_highlighter_agent = AnomalyOutlierHighlighterAgent(tracer=tracer)
    register(ANOMALY_OUTLIER_HIGHLIGHTER_AGENT_NAME, anomaly_outlier_highlighter_agent.run)

    grounded_narrative_generation_agent = GroundedNarrativeGenerationAgent(
        llm_client=llm_client, tracer=tracer
    )
    register(
        GROUNDED_NARRATIVE_GENERATION_AGENT_NAME, grounded_narrative_generation_agent.run
    )

    follow_up_suggestion_agent = FollowUpSuggestionAgent(llm_client=llm_client, tracer=tracer)
    register(FOLLOW_UP_SUGGESTION_AGENT_NAME, follow_up_suggestion_agent.run)

    # Ops-domain agents (Phase 8). Lineage Recorder needs its own,
    # SEPARATE session factory -- `navigraph_lineage`'s `get_engine()`/
    # `get_session_factory()` are independent module-level caches from
    # `navigraph_catalog.db`'s (a real, deliberate design point: both
    # packages connect to the same physical Postgres instance, but each
    # owns its own engine/session-factory cache and its own Alembic
    # revision chain -- see navigraph_lineage/migrations/env.py's
    # `alembic_version_lineage` docstring for why).
    lineage_session_factory = get_lineage_session_factory(get_lineage_engine())
    lineage_recorder_agent = LineageRecorderAgent(
        session_factory=lineage_session_factory, tracer=tracer
    )
    register(LINEAGE_RECORDER_AGENT_NAME, lineage_recorder_agent.run)

    evaluation_judge_agent = EvaluationJudgeAgent(llm_client=llm_client, tracer=tracer)
    register(EVALUATION_JUDGE_AGENT_NAME, evaluation_judge_agent.run)

    # Orchestrator-domain agents (Phase 9). Session/Context Manager reuses
    # the same real `redis_client` Caching already constructed above --
    # same protocol, different key namespace (`...:session:...` vs
    # `...:query_cache:...`), see `session_context_manager/agent.py`'s
    # module docstring.
    session_context_manager_agent = SessionContextManagerAgent(
        cache_client=cast(SessionCacheClientProtocol, redis_client), tracer=tracer
    )
    register(SESSION_CONTEXT_MANAGER_AGENT_NAME, session_context_manager_agent.run)

    clarification_coordinator_agent = ClarificationCoordinatorAgent(
        llm_client=llm_client, tracer=tracer
    )
    register(CLARIFICATION_COORDINATOR_AGENT_NAME, clarification_coordinator_agent.run)

    # Request Orchestrator constructs all 20 of its own sub-agents
    # (19 in the linear sequence, plus Caching around Data Federation --
    # see agent.py's module docstring for the LIMITATIONS.md item 59 fix)
    # in __init__ (its own real, independent instances -- not the ones
    # registered above, matching every prior agent's own construct-your-
    # own-dependencies convention). It needs `catalog_session_factory` and
    # `opa_client`, both of which existed only as local variables until now.
    request_orchestrator_agent = RequestOrchestratorAgent(
        llm_client=llm_client,
        catalog_session_factory=catalog_session_factory,
        lineage_session_factory=lineage_session_factory,
        neo4j_client=neo4j_client,
        opa_client=opa_client,
        cache_client=cast(SessionCacheClientProtocol, redis_client),
        trino_client=trino_client,
        tracer=tracer,
    )
    register(REQUEST_ORCHESTRATOR_AGENT_NAME, request_orchestrator_agent.run)

    app.state.llm_client = llm_client
    app.state.neo4j_client = neo4j_client
    app.state.trino_client = trino_client
    app.state.redis_client = redis_client
    app.state.lineage_session_factory = lineage_session_factory
    app.state.catalog_session_factory = catalog_session_factory
    app.state.opa_client = opa_client
    yield

    neo4j_client.close()
    trino_client.close()
    redis_client.close()


app = FastAPI(title="NaviGraph Agent Runtime", version="0.1.0", lifespan=lifespan)

# Prometheus /metrics endpoint. Exposed on the same port (8001) per the
# infra workstream's Prometheus scrape config (agent-runtime:8001/metrics).
Instrumentator().instrument(app).expose(app, endpoint="/metrics")

# OTel FastAPI auto-instrumentation: one span per HTTP request, in addition
# to the per-agent-invocation span created inside each agent's `run()`.
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
except Exception:
    logger.warning("FastAPI OTel auto-instrumentation could not be enabled", exc_info=True)


@app.get("/healthz")
async def healthz() -> dict:
    """Liveness probe. Always returns ok if the process is running."""

    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    """Readiness probe.

    NOTE: currently identical to /healthz. In a later phase this should
    verify the configured LLM client is actually reachable (or at minimum
    that one is registered) and that the agent registry is non-empty.
    """

    return {"status": "ok", "registered_agents": list(AGENT_REGISTRY.keys())}


async def _invoke_agent(agent_name: str, input_model: type[AgentInput], payload: dict) -> dict:
    """Shared implementation behind every `/agents/.../invoke` route:
    validate `payload` against `input_model`, bind request-context fields
    for correlated logging, look up the registered agent by name, run it,
    and return its output as JSON. Factored out once all six Understanding
    agents needed the identical wiring (was written inline, one-off, for
    Intent Understanding only, before this phase added five more).
    """

    try:
        agent_input = input_model.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    bind_request_context(
        trace_id=agent_input.request_context.trace_id,
        tenant_id=agent_input.request_context.tenant_id,
    )

    try:
        run = get_agent(agent_name)
    except KeyError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{agent_name} agent is not registered (startup may still be running)",
        ) from exc

    output = await run(agent_input)
    return output.model_dump(mode="json")


@app.post("/agents/understanding/intent_understanding/invoke")
async def invoke_intent_understanding(payload: dict) -> dict:
    """Parse the request body into `IntentUnderstandingInput`, run the real
    Intent Understanding agent, and return its `IntentUnderstandingOutput`.
    """

    return await _invoke_agent(INTENT_UNDERSTANDING_AGENT_NAME, IntentUnderstandingInput, payload)


@app.post("/agents/understanding/conversation/invoke")
async def invoke_conversation(payload: dict) -> dict:
    """Parse the request body into `ConversationInput`, run the real
    Conversation agent, and return its `ConversationOutput`.
    """

    return await _invoke_agent(CONVERSATION_AGENT_NAME, ConversationInput, payload)


@app.post("/agents/understanding/metadata_discovery/invoke")
async def invoke_metadata_discovery(payload: dict) -> dict:
    """Parse the request body into `MetadataDiscoveryInput`, run the real
    Metadata Discovery agent, and return its `MetadataDiscoveryOutput`.
    """

    return await _invoke_agent(METADATA_DISCOVERY_AGENT_NAME, MetadataDiscoveryInput, payload)


@app.post("/agents/understanding/ontology/invoke")
async def invoke_ontology(payload: dict) -> dict:
    """Parse the request body into `OntologyInput`, run the real Ontology
    agent, and return its `OntologyOutput`.
    """

    return await _invoke_agent(ONTOLOGY_AGENT_NAME, OntologyInput, payload)


@app.post("/agents/understanding/semantic_retrieval/invoke")
async def invoke_semantic_retrieval(payload: dict) -> dict:
    """Parse the request body into `SemanticRetrievalInput`, run the real
    Semantic Retrieval agent, and return its `SemanticRetrievalOutput`.
    """

    return await _invoke_agent(SEMANTIC_RETRIEVAL_AGENT_NAME, SemanticRetrievalInput, payload)


@app.post("/agents/understanding/schema_mapping/invoke")
async def invoke_schema_mapping(payload: dict) -> dict:
    """Parse the request body into `SchemaMappingInput`, run the real Schema
    Mapping agent, and return its `SchemaMappingOutput`.
    """

    return await _invoke_agent(SCHEMA_MAPPING_AGENT_NAME, SchemaMappingInput, payload)


@app.post("/agents/query/data_source_discovery/invoke")
async def invoke_data_source_discovery(payload: dict) -> dict:
    """Parse the request body into `DataSourceDiscoveryInput`, run the real
    Data Source Discovery agent, and return its `DataSourceDiscoveryOutput`.
    """

    return await _invoke_agent(
        DATA_SOURCE_DISCOVERY_AGENT_NAME, DataSourceDiscoveryInput, payload
    )


@app.post("/agents/query/sql_generation/invoke")
async def invoke_sql_generation(payload: dict) -> dict:
    """Parse the request body into `SqlGenerationInput`, run the real SQL
    Generation agent, and return its `SqlGenerationOutput`.
    """

    return await _invoke_agent(SQL_GENERATION_AGENT_NAME, SqlGenerationInput, payload)


@app.post("/agents/query/sql_optimization/invoke")
async def invoke_sql_optimization(payload: dict) -> dict:
    """Parse the request body into `SqlOptimizationInput`, run the real SQL
    Optimization agent, and return its `SqlOptimizationOutput`.
    """

    return await _invoke_agent(SQL_OPTIMIZATION_AGENT_NAME, SqlOptimizationInput, payload)


@app.post("/agents/query/execution_planning/invoke")
async def invoke_execution_planning(payload: dict) -> dict:
    """Parse the request body into `ExecutionPlanningInput`, run the real
    Execution Planning agent (the hard SELECT-only safety gate), and return
    its `ExecutionPlanningOutput`.
    """

    return await _invoke_agent(
        EXECUTION_PLANNING_AGENT_NAME, ExecutionPlanningInput, payload
    )


@app.post("/agents/query/data_federation/invoke")
async def invoke_data_federation(payload: dict) -> dict:
    """Parse the request body into `DataFederationInput`, run the real Data
    Federation agent (the only agent that actually executes against a live
    source), and return its `DataFederationOutput`.
    """

    return await _invoke_agent(DATA_FEDERATION_AGENT_NAME, DataFederationInput, payload)


@app.post("/agents/query/caching/invoke")
async def invoke_caching(payload: dict) -> dict:
    """Parse the request body into `CachingInput`, run the real Caching
    agent, and return its `CachingOutput`.
    """

    return await _invoke_agent(CACHING_AGENT_NAME, CachingInput, payload)


@app.post("/agents/guardrail/schema_constraint_validator/invoke")
async def invoke_schema_constraint_validator(payload: dict) -> dict:
    """Parse the request body into `SchemaConstraintValidatorInput`, run the
    real Schema Constraint Validator agent, and return its
    `SchemaConstraintValidatorOutput`.
    """

    return await _invoke_agent(
        SCHEMA_CONSTRAINT_VALIDATOR_AGENT_NAME, SchemaConstraintValidatorInput, payload
    )


@app.post("/agents/guardrail/policy_authorization/invoke")
async def invoke_policy_authorization(payload: dict) -> dict:
    """Parse the request body into `PolicyAuthorizationInput`, run the real
    Policy Authorization agent (calls the real OPA policy engine), and
    return its `PolicyAuthorizationOutput`.
    """

    return await _invoke_agent(
        POLICY_AUTHORIZATION_AGENT_NAME, PolicyAuthorizationInput, payload
    )


@app.post("/agents/guardrail/query_cost_estimator/invoke")
async def invoke_query_cost_estimator(payload: dict) -> dict:
    """Parse the request body into `QueryCostEstimatorInput`, run the real
    Query Cost/Row-Limit Estimator agent, and return its
    `QueryCostEstimatorOutput`.
    """

    return await _invoke_agent(
        QUERY_COST_ESTIMATOR_AGENT_NAME, QueryCostEstimatorInput, payload
    )


@app.post("/agents/guardrail/pii_exposure_checker/invoke")
async def invoke_pii_exposure_checker(payload: dict) -> dict:
    """Parse the request body into `PiiExposureCheckerInput`, run the real
    PII Exposure Checker agent, and return its `PiiExposureCheckerOutput`.
    """

    return await _invoke_agent(
        PII_EXPOSURE_CHECKER_AGENT_NAME, PiiExposureCheckerInput, payload
    )


@app.post("/agents/insight/chart_selection/invoke")
async def invoke_chart_selection(payload: dict) -> dict:
    """Parse the request body into `ChartSelectionInput`, run the real
    Chart Selection agent, and return its `ChartSelectionOutput`.
    """

    return await _invoke_agent(CHART_SELECTION_AGENT_NAME, ChartSelectionInput, payload)


@app.post("/agents/insight/anomaly_outlier_highlighter/invoke")
async def invoke_anomaly_outlier_highlighter(payload: dict) -> dict:
    """Parse the request body into `AnomalyDetectionInput`, run the real
    Anomaly/Outlier Highlighter agent, and return its
    `AnomalyDetectionOutput`.
    """

    return await _invoke_agent(
        ANOMALY_OUTLIER_HIGHLIGHTER_AGENT_NAME, AnomalyDetectionInput, payload
    )


@app.post("/agents/insight/grounded_narrative_generation/invoke")
async def invoke_grounded_narrative_generation(payload: dict) -> dict:
    """Parse the request body into `NarrativeGenerationInput`, run the real
    Grounded Narrative Generation agent, and return its
    `NarrativeGenerationOutput`.
    """

    return await _invoke_agent(
        GROUNDED_NARRATIVE_GENERATION_AGENT_NAME, NarrativeGenerationInput, payload
    )


@app.post("/agents/insight/follow_up_suggestion/invoke")
async def invoke_follow_up_suggestion(payload: dict) -> dict:
    """Parse the request body into `FollowUpSuggestionInput`, run the real
    Follow-up Suggestion agent, and return its `FollowUpSuggestionOutput`.
    """

    return await _invoke_agent(
        FOLLOW_UP_SUGGESTION_AGENT_NAME, FollowUpSuggestionInput, payload
    )


@app.post("/agents/ops/lineage_recorder/invoke")
async def invoke_lineage_recorder(payload: dict) -> dict:
    """Parse the request body into `LineageRecorderInput`, run the real
    Lineage Recorder agent, and return its `LineageRecorderOutput`.
    """

    return await _invoke_agent(LINEAGE_RECORDER_AGENT_NAME, LineageRecorderInput, payload)


@app.post("/agents/ops/evaluation_judge/invoke")
async def invoke_evaluation_judge(payload: dict) -> dict:
    """Parse the request body into `EvaluationJudgeInput`, run the real
    Evaluation Judge agent, and return its `EvaluationJudgeOutput`.
    """

    return await _invoke_agent(EVALUATION_JUDGE_AGENT_NAME, EvaluationJudgeInput, payload)


@app.post("/agents/orchestrator/session_context_manager/invoke")
async def invoke_session_context_manager(payload: dict) -> dict:
    """Parse the request body into `SessionContextManagerInput`, run the
    real Session/Context Manager agent, and return its
    `SessionContextManagerOutput`.
    """

    return await _invoke_agent(
        SESSION_CONTEXT_MANAGER_AGENT_NAME, SessionContextManagerInput, payload
    )


@app.post("/agents/orchestrator/clarification_coordinator/invoke")
async def invoke_clarification_coordinator(payload: dict) -> dict:
    """Parse the request body into `ClarificationCoordinatorInput`, run the
    real Clarification Coordinator agent, and return its
    `ClarificationCoordinatorOutput`.
    """

    return await _invoke_agent(
        CLARIFICATION_COORDINATOR_AGENT_NAME, ClarificationCoordinatorInput, payload
    )


@app.post("/agents/orchestrator/request_orchestrator/invoke")
async def invoke_request_orchestrator(payload: dict) -> dict:
    """Parse the request body into `RequestOrchestratorInput`, run the real
    Request Orchestrator agent (the full ~19-stage pipeline), and return
    its `RequestOrchestratorOutput`.
    """

    return await _invoke_agent(
        REQUEST_ORCHESTRATOR_AGENT_NAME, RequestOrchestratorInput, payload
    )


@app.get("/lineage/{trace_id}")
async def get_lineage_trace(trace_id: str, tenant_id: str) -> dict:
    """Real, live retrieval of the full assembled lineage chain for one
    trace. A plain FastAPI route, not an `_invoke_agent`-wrapped POST --
    mirrors `/healthz`/`/readyz`'s direct-route shape, since reading a
    trace isn't agent-shaped in this codebase's existing convention (see
    `navigraph_lineage.api.get_trace`'s docstring).

    `tenant_id` is a REQUIRED query param, not inferred or optional --
    mirrors `RequestContext.tenant_id`'s own non-optional discipline. No
    new auth mechanism exists yet in this codebase (see LIMITATIONS.md item
    23), so this is the simplest tenant-scoping mechanism available; a
    caller who knows a `trace_id` and a `tenant_id` string can read that
    trace, which is acceptable for an internal debugging/audit route today
    but is a real, logged limitation, not a full access-control boundary.
    """

    with lineage_session_scope(app.state.lineage_session_factory) as session:
        records = get_trace(session, trace_id=trace_id, tenant_id=tenant_id)

    return {
        "trace_id": trace_id,
        "tenant_id": tenant_id,
        "events": [
            {
                "event_id": record.event_id,
                "agent_name": record.agent_name,
                "timestamp": record.timestamp.isoformat(),
                "input_summary": record.input_summary,
                "output_summary": record.output_summary,
                "tenant_id": record.tenant_id,
                "trace_id": record.trace_id,
            }
            for record in records
        ],
    }
