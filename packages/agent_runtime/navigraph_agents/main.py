"""NaviGraph agent-runtime FastAPI application.

Exposes:
  - GET  /healthz    -- liveness probe
  - GET  /readyz     -- readiness probe (see NOTE below)
  - GET  /metrics    -- Prometheus metrics (via prometheus-fastapi-instrumentator)
  - POST /agents/understanding/intent_understanding/invoke
                     -- invokes the (only) real registered agent

At startup, constructs a real `AnthropicLLMClient` if `ANTHROPIC_API_KEY` is
set, or falls back to a `FakeLLMClient` (logging a warning) so this service
still boots and answers requests locally without a real API key -- useful
for local dev and for the smoke test in tools/scripts/smoke-test.sh.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from navigraph_shared.config import get_settings
from navigraph_shared.llm import AnthropicLLMClient, FakeLLMClient, LLMClient
from navigraph_shared.telemetry import (
    bind_request_context,
    configure_logging,
    get_tracer,
)
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import ValidationError

from navigraph_agents.registry import AGENT_REGISTRY, get_agent, register
from navigraph_agents.understanding.intent_understanding.agent import (
    AGENT_NAME as INTENT_UNDERSTANDING_AGENT_NAME,
)
from navigraph_agents.understanding.intent_understanding.agent import (
    IntentUnderstandingAgent,
)
from navigraph_agents.understanding.intent_understanding.contracts import (
    IntentUnderstandingInput,
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

    app.state.llm_client = llm_client
    yield


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


@app.post("/agents/understanding/intent_understanding/invoke")
async def invoke_intent_understanding(payload: dict) -> dict:
    """Parse the request body into `IntentUnderstandingInput`, run the real
    Intent Understanding agent, and return its `IntentUnderstandingOutput`.
    """

    try:
        agent_input = IntentUnderstandingInput.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    bind_request_context(
        trace_id=agent_input.request_context.trace_id,
        tenant_id=agent_input.request_context.tenant_id,
    )

    try:
        run = get_agent(INTENT_UNDERSTANDING_AGENT_NAME)
    except KeyError as exc:
        raise HTTPException(
            status_code=503,
            detail="intent_understanding agent is not registered (startup may still be running)",
        ) from exc

    output = await run(agent_input)
    return output.model_dump(mode="json")
