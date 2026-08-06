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
  - /mcp             -- MCP (Model Context Protocol) tool server (see
                          `mcp_tools.py`) -- exposes NaviGraph's real
                          capabilities (ask_navigraph, resolve_business_term,
                          list_data_sources, list_business_glossary,
                          get_lineage) to any MCP-speaking AI agent, headless.

Gateway and agent-runtime are two separate containers/services (see
infra/docker-compose.yml) -- this call is a real HTTP hop, not an in-process
call, even though both share the "modular monolith" agent-runtime process
internally.

ROLES/CLAIMS VERIFICATION: a real, generic Azure AD JWT/JWKS verifier
exists (`navigraph_shared.auth.azure_ad`) but is FEATURE-FLAGGED OFF by
default (`AzureADSettings.azure_ad_enabled=False`) pending a real Azure AD
app registration -- see that module's docstring and LIMITATIONS.md's
Azure AD item. While disabled (today), `roles`/`claims` remain exactly
what they always were: caller-supplied, not cryptographically verified --
Guardrail's Policy Authorization agent fails closed on empty/mismatched
roles/claims exactly as designed, so a caller that omits them will
legitimately get an `outcome="failed"`/`guardrail.policy_authorization`
response rather than a silent bypass. Once enabled, `_verify_identity`'s
FastAPI dependency requires and verifies a real `Authorization: Bearer`
header and the VERIFIED identity's roles/tenant_id override whatever the
request body self-declared.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from navigraph_shared.auth import (
    AzureADSettings,
    AzureADTokenError,
    AzureADTokenVerifier,
    HttpAzureADTokenVerifier,
    VerifiedIdentity,
)
from navigraph_shared.contracts import RequestContext
from navigraph_shared.telemetry import (
    bind_request_context,
    configure_logging,
    get_tracer,
)
from prometheus_fastapi_instrumentator import Instrumentator
from pydantic import BaseModel

from navigraph_gateway.mcp_tools import build_mcp_server
from navigraph_gateway.settings import get_gateway_settings

logger = configure_logging("navigraph-gateway")
tracer = get_tracer("navigraph-gateway")


_settings = get_gateway_settings()
# See `navigraph_shared.auth.azure_ad`'s module docstring: a real, generic
# JWT/JWKS verifier, feature-flagged OFF (`AzureADSettings.azure_ad_enabled
# = False`) until a real Azure AD app registration exists -- `_verify_identity`
# below is a no-op passthrough while disabled, so `/ask`'s behavior is
# completely unchanged today.
_azure_ad_settings = AzureADSettings()
_azure_ad_verifier: AzureADTokenVerifier = HttpAzureADTokenVerifier(_azure_ad_settings)

# Constructed at MODULE load, not inside `lifespan()`, and shared between
# `/ask` (via `app.state.http_client`, assigned in `lifespan()` below) and
# the MCP tools -- one connection pool to agent-runtime, not two. This has
# to happen before `lifespan()` ever runs because `app.mount()` below needs
# the MCP ASGI app synchronously at import time (mirrors how
# `mcp_server.streamable_http_app()` must be called before
# `mcp_server.session_manager` even exists -- see `mcp_tools.py`'s module
# docstring), which in turn needs `build_mcp_server()` to already have a
# client for its tool closures to capture.
_http_client = httpx.AsyncClient(base_url=_settings.agent_runtime_base_url, timeout=120.0)
_mcp_server = build_mcp_server(http_client=_http_client, settings=_settings)
_mcp_asgi_app = _mcp_server.streamable_http_app()
# See `lifespan()`'s "SECOND REAL GOTCHA" comment below.
_mcp_session_manager_started = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = _settings
    app.state.settings = settings
    # REAL BUG, found live via the first real end-to-end /ask call ever made
    # through the actual public gateway path: 30s was shorter than the real
    # Request Orchestrator's actual end-to-end latency for a non-trivial
    # question (confirmed live: agent-runtime was still genuinely
    # processing -- real Anthropic/Snowflake/OPA calls in its own logs --
    # 45+ seconds after this client had already given up and returned a 502
    # to the caller). Bumped to 120s; the gateway/gateway-canary Ingress
    # objects' own `proxy-read-timeout`/`proxy-send-timeout` annotations
    # were bumped to match (see infra/k8s/overlays/dev/ingress-patch.yaml)
    # -- raising only one of the two layers just moves the bottleneck to
    # the other. Same module-level `_http_client` the MCP tools already
    # capture -- assigned here (not constructed here) so `/ask` and `/mcp`
    # share one real connection pool.
    app.state.http_client = _http_client
    # REAL GOTCHA, found live while integration-testing the MCP mount:
    # Starlette does NOT run a mounted sub-app's own lifespan, so
    # `FastMCP`'s internal `session_manager` never starts unless entered
    # explicitly here -- every real request otherwise fails with
    # `RuntimeError: Task group is not initialized. Make sure to use run().`
    #
    # SECOND REAL GOTCHA, found live running this file's own pre-existing
    # test suite (`test_healthz.py`/`test_cors.py`, which use
    # `with TestClient(app):` per test function -- each entry/exit is a
    # real lifespan cycle): `StreamableHTTPSessionManager.run()` can only
    # ever be entered ONCE per instance -- a second `with TestClient(app):`
    # block in the same process re-runs `lifespan()` and raises
    # `RuntimeError: .run() can only be called once per instance`. In a
    # real deployment `lifespan()` only ever runs once per process, so this
    # is purely a repeated-TestClient-in-one-process artifact -- guarded
    # with a module-level flag so only the FIRST lifespan cycle in a
    # process actually starts the session manager; later cycles (test-only)
    # just skip it, since none of this service's OTHER tests exercise
    # `/mcp` through this shared `app` object (the MCP tool tests in
    # `test_mcp_tools.py` build their own, independent `FastMCP` instances
    # instead, precisely to avoid this).
    global _mcp_session_manager_started
    if not _mcp_session_manager_started:
        _mcp_session_manager_started = True
        async with _mcp_server.session_manager.run():
            try:
                yield
            finally:
                await _http_client.aclose()
    else:
        try:
            yield
        finally:
            await _http_client.aclose()


app = FastAPI(title="NaviGraph Gateway", version="0.1.0", lifespan=lifespan)

# Real bug's fix, found while wiring up the first actual browser-facing
# chat UI: the `web` app's real client component calls this gateway's
# `/ask` directly from the browser (a different origin,
# app.navigraph.* vs api.navigraph.*), and no browser allows that without
# an explicit CORS allow-origin -- every prior real verification of `/ask`
# used `curl`/`httpx`, which aren't subject to the browser's
# same-origin policy, so this was never exercised before. `localhost:3000`
# is included too so `next dev` against the real deployed gateway works
# without a separate CORS config for local iteration.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_settings.web_origin, "http://localhost:3000"],
    allow_methods=["POST"],
    allow_headers=["Content-Type"],
)

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


async def _verify_identity(
    authorization: str | None = Header(default=None),
) -> VerifiedIdentity | None:
    """FastAPI dependency gating `/ask` behind Azure AD, once enabled.

    Returns `None` (today's default, `azure_ad_enabled=False`) so `/ask`
    keeps trusting the request body's self-declared `roles`/`claims`
    exactly as it always has -- zero behavior change. Once flipped on,
    requires a well-formed `Authorization: Bearer <token>` header and a
    real, verified identity; any failure is a 401, never a silent
    fallback to the unverified body fields.
    """

    if not _azure_ad_settings.azure_ad_enabled:
        return None

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")

    token = authorization.removeprefix("Bearer ").strip()
    try:
        return await _azure_ad_verifier.verify(token)
    except AzureADTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.post("/ask")
async def ask(
    request: AskRequest,
    verified_identity: VerifiedIdentity | None = Depends(_verify_identity),
) -> dict:
    """Forward a question to the real Request Orchestrator agent (the full
    ~19-stage pipeline: Understanding -> Query -> Guardrail -> Insight, with
    lineage recorded at every stage and multi-turn session/clarification
    handling) and return its `RequestOrchestratorOutput` verbatim.
    """

    trace_id = str(uuid.uuid4())
    bind_request_context(
        trace_id=trace_id,
        tenant_id=verified_identity.tenant_id if verified_identity is not None else request.tenant_id,
    )

    if verified_identity is not None:
        # Azure AD enabled and the caller's token verified for real -- the
        # VERIFIED identity's roles/tenant_id win over whatever the request
        # body self-declared, closing the self-declared-role gap.
        request_context = RequestContext(
            tenant_id=verified_identity.tenant_id,
            user_id=request.user_id,
            trace_id=trace_id,
            roles=verified_identity.roles,
            claims={**request.claims, "tenant_id": verified_identity.tenant_id},
        )
    else:
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
        span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
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


# MUST be mounted LAST, after every other route above -- REAL BUG, found
# live: Starlette matches routes in registration order, and a `Mount("/",
# ...)` matches every path (everything starts with "/"), so mounting it
# before `/healthz`/`/readyz`/`/ask`/`/metrics` silently 404s all of them
# (confirmed with a real `TestClient` reproduction). Must also mount at the
# ROOT, not at "/mcp" -- `_mcp_asgi_app` already defines its own internal
# route at `/mcp` (confirmed live: `FastMCP.streamable_http_app()`'s only
# route is `/mcp`, per its `streamable_http_path` setting), so mounting
# THIS app at "/mcp" here would produce the real path `/mcp/mcp`. Mounting
# at "/", last, makes the effective, correct path exactly `/mcp` without
# shadowing anything defined above.
app.mount("/", _mcp_asgi_app)
