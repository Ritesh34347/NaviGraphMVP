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
                     -- invokes the six real, registered Understanding-domain agents

At startup, constructs a real `AnthropicLLMClient` if `ANTHROPIC_API_KEY` is
set, or falls back to a `FakeLLMClient` (logging a warning) so this service
still boots and answers requests locally without a real API key -- useful
for local dev and for the smoke test in tools/scripts/smoke-test.sh. Also
constructs a `navigraph_catalog` session factory and a `navigraph_kg`
`Neo4jClient` for the agents that need them (Metadata Discovery, Ontology).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from navigraph_catalog.db import get_engine, get_session_factory
from navigraph_kg.client import Neo4jClient
from navigraph_shared.config import get_settings
from navigraph_shared.contracts import AgentInput
from navigraph_shared.llm import AnthropicLLMClient, FakeLLMClient, LLMClient
from navigraph_shared.telemetry import (
    bind_request_context,
    configure_logging,
    get_tracer,
)
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import ValidationError

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

    app.state.llm_client = llm_client
    app.state.neo4j_client = neo4j_client
    yield

    neo4j_client.close()


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
