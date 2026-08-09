"""NaviGraph gateway FastAPI application.

Exposes:
  - GET  /healthz    -- liveness probe
  - GET  /readyz     -- readiness probe (see NOTE below)
  - GET  /metrics    -- Prometheus metrics (via prometheus-fastapi-instrumentator)
  - POST /ask        -- accepts a question (plus optional session_id/
                          data_source_id/roles/claims), builds a
                          RequestContext, calls the agent-runtime's real
                          Request Orchestrator agent over HTTP, and returns
                          its `RequestOrchestratorOutput` verbatim.

Gateway and agent-runtime are two separate containers/services (see
infra/docker-compose.yml) -- this call is a real HTTP hop, not an in-process
call, even though both share the "modular monolith" agent-runtime process
internally.

ROLES/CLAIMS ARE CALLER-SUPPLIED, NOT YET CRYPTOGRAPHICALLY VERIFIED: no
real Azure AD JWT validation exists yet in this codebase (see
LIMITATIONS.md's Azure AD token verification item) -- Guardrail's Policy
Authorization agent fails closed on empty/mismatched roles/claims exactly as
designed, so a caller that omits them will legitimately get an
`outcome="failed"`/`guardrail.policy_authorization` response rather than a
silent bypass. This is the same trust model every other real HTTP smoke test
against agent-runtime has used since Phase 6, just now reachable through the
gateway too.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from navigraph_shared.contracts import RequestContext
from navigraph_shared.telemetry import (
    bind_request_context,
    configure_logging,
    get_tracer,
)
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from navigraph_gateway.settings import get_gateway_settings

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
except Exception:
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
    session_id: str | None = None
    data_source_id: str | None = None
    roles: list[str] = []
    claims: dict[str, Any] = {}


@app.post("/ask")
async def ask(request: AskRequest) -> dict:
    """Forward a question to the real Request Orchestrator agent (the full
    pipeline: Understanding -> Query -> Guardrail -> Query execution (with a
    real cache lookup/store around it) -> Insight, with lineage recorded at
    every stage and multi-turn session/clarification handling) and return
    its `RequestOrchestratorOutput` verbatim.
    """

    trace_id = str(uuid.uuid4())
    bind_request_context(trace_id=trace_id, tenant_id=request.tenant_id)

    request_context = RequestContext(
        tenant_id=request.tenant_id,
        user_id=request.user_id,
        trace_id=trace_id,
        roles=request.roles,
        claims=request.claims,
    )

    agent_payload = {
        "request_context": request_context.model_dump(mode="json"),
        "payload": {
            "question": request.question,
            "session_id": request.session_id,
            "data_source_id": request.data_source_id,
        },
    }

    http_client: httpx.AsyncClient = app.state.http_client

    with tracer.start_as_current_span("gateway.ask") as span:
        span.set_attribute("navigraph.tenant_id", request.tenant_id)
        span.set_attribute("navigraph.trace_id", trace_id)
        try:
            response = await http_client.post(
                "/agents/orchestrator/request_orchestrator/invoke",
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
