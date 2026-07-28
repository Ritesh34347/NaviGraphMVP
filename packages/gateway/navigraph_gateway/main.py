"""NaviGraph gateway FastAPI application.

Exposes:
  - GET  /healthz    -- liveness probe
  - GET  /readyz     -- readiness probe (see NOTE below)
  - GET  /metrics    -- Prometheus metrics (via prometheus-fastapi-instrumentator)
  - POST /ask        -- Phase 1.5 minimal wiring: accepts a question, builds a
                          RequestContext, calls the agent-runtime's Intent
                          Understanding agent, and returns its output.

PHASE 1.5 NOTE: `/ask` currently calls exactly one agent
(understanding.intent_understanding) and returns its raw output. This is
deliberately minimal wiring to prove the gateway -> agent-runtime path works
end to end. The full pipeline (Understanding -> Query -> Guardrail -> Trino
execution -> Insight, with lineage assembled across every hop) lands in
later phases once those agents exist -- see LIMITATIONS.md and
docs/architecture/overview.md at the repo root.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from prometheus_fastapi_instrumentator import Instrumentator

from navigraph_gateway.settings import GatewaySettings, get_gateway_settings
from navigraph_shared.contracts import RequestContext
from navigraph_shared.telemetry import bind_request_context, configure_logging, get_tracer

logger = configure_logging("navigraph-gateway")
tracer = get_tracer("navigraph-gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_gateway_settings()
    app.state.settings = settings
    app.state.http_client = httpx.AsyncClient(base_url=settings.agent_runtime_base_url, timeout=30.0)
    try:
        yield
    finally:
        await app.state.http_client.aclose()


app = FastAPI(title="NaviGraph Gateway", version="0.1.0", lifespan=lifespan)

# Prometheus /metrics endpoint. Exposed on the same port (8000) per the
# infra workstream's Prometheus scrape config (gateway:8000/metrics).
Instrumentator().instrument(app).expose(app, endpoint="/metrics")

# OTel FastAPI auto-instrumentation: creates a span per HTTP request,
# in addition to the manual span we open around the agent-runtime call below.
try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)
except Exception:  # noqa: BLE001 - instrumentation must never block startup
    logger.warning("FastAPI OTel auto-instrumentation could not be enabled", exc_info=True)


@app.get("/healthz")
async def healthz() -> dict:
    """Liveness probe. Always returns ok if the process is running."""

    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    """Readiness probe.

    NOTE: this is currently identical to /healthz. A real implementation
    should check connectivity to everything this service depends on to
    serve traffic correctly -- e.g. the agent-runtime base URL, and (once
    they're wired up in a later phase) Redis/Postgres connection pools. Left
    as a placeholder here because those dependencies don't exist yet for the
    gateway in Phase 1.
    """

    return {"status": "ok"}


class AskRequest(BaseModel):
    question: str
    tenant_id: str
    user_id: str


@app.post("/ask")
async def ask(request: AskRequest) -> dict:
    """Phase 1.5 minimal wiring: forward a question to the Intent
    Understanding agent and return its output verbatim.
    """

    trace_id = str(uuid.uuid4())
    bind_request_context(trace_id=trace_id, tenant_id=request.tenant_id)

    request_context = RequestContext(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        trace_id=trace_id,
        roles=[],
        claims={},
    )

    agent_payload = {
        "request_context": request_context.model_dump(mode="json"),
        "payload": {"question": request.question},
    }

    http_client: httpx.AsyncClient = app.state.http_client

    with tracer.start_as_current_span("gateway.ask") as span:
        span.set_attribute("navigraph.tenant_id", request.tenant_id)
        span.set_attribute("navigraph.trace_id", trace_id)
        try:
            response = await http_client.post(
                "/agents/understanding/intent_understanding/invoke",
                json=agent_payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("agent-runtime call failed: %s", exc)
            raise HTTPException(
                status_code=502,
                detail="agent-runtime is unavailable or returned an error",
            ) from exc

    return response.json()
