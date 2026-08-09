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

ROLES/CLAIMS VERIFICATION (LIMITATIONS.md item 23, resolved 2026-08-09):
when `AZURE_AD_TENANT_ID`/`AZURE_AD_AUDIENCE` are both configured, `/ask`
REQUIRES a real `Authorization: Bearer <token>` header, verifies it for
real against the tenant's live Azure AD JWKS endpoint (signature, issuer,
audience, expiry -- see `navigraph_shared.auth.AzureAdTokenVerifier`), and
builds `RequestContext.user_id`/`roles`/`claims` from the VERIFIED token
instead of the request body -- any `user_id`/`roles`/`claims` the caller
also supplies in the body are ignored outright, never merged with verified
data. When those env vars are NOT both set, this falls back to the
original caller-supplied-roles/claims trust model (a loud warning is
logged at startup) -- this is the only way to run against docker-compose/
CI without a live Entra tenant, and Guardrail's Policy Authorization agent
still fails closed on empty/mismatched roles/claims either way, so an
unauthenticated caller in this fallback mode gets a legitimate
`outcome="failed"`/`guardrail.policy_authorization` response, not a silent
bypass.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from navigraph_shared.auth import (
    AzureAdAuthSettings,
    AzureAdTokenVerifier,
    TokenVerificationError,
    TokenVerifier,
)
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


def _build_token_verifier() -> TokenVerifier | None:
    """Real Azure AD JWT verification (LIMITATIONS.md item 23) when both
    `AZURE_AD_TENANT_ID` and `AZURE_AD_AUDIENCE` are configured (the real
    Entra app registration's tenant + Application ID URI/client ID); `None`
    -- meaning "fall back to the caller-supplied roles/claims trust model"
    -- otherwise. Mirrors `agent_runtime.main._build_secrets_provider`'s
    identical real-if-configured/fake-otherwise pattern, logging which one
    was chosen rather than silently degrading."""

    settings = AzureAdAuthSettings()
    if settings.azure_ad_tenant_id and settings.azure_ad_audience:
        return AzureAdTokenVerifier(
            tenant_id=settings.azure_ad_tenant_id, audience=settings.azure_ad_audience
        )

    logger.warning(
        "AZURE_AD_TENANT_ID/AZURE_AD_AUDIENCE are not both set -- falling back to trusting "
        "caller-supplied user_id/roles/claims in the /ask request body, exactly like every "
        "NaviGraph deployment before this JWT-verification support existed. This is INSECURE "
        "and must never be used for a real deployment -- set both env vars to enable real "
        "Azure AD bearer-token verification."
    )
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_gateway_settings()
    app.state.settings = settings
    app.state.token_verifier = _build_token_verifier()
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
async def ask(request: AskRequest, authorization: str | None = Header(default=None)) -> dict:
    """Forward a question to the real Request Orchestrator agent (the full
    pipeline: Understanding -> Query -> Guardrail -> Query execution (with a
    real cache lookup/store around it) -> Insight, with lineage recorded at
    every stage and multi-turn session/clarification handling) and return
    its `RequestOrchestratorOutput` verbatim.

    See this module's docstring: `user_id`/`roles`/`claims` come from a
    cryptographically verified bearer token when Azure AD verification is
    configured, from the request body (the pre-Phase-11 trust model)
    otherwise. `tenant_id` (NaviGraph's own business tenant, distinct from
    any Azure AD tenant) always comes from the request body either way --
    Guardrail's real OPA policy is what actually checks a verified token's
    tenant claim against it (`infra/opa/policies/authz.rego`), once a real
    Entra app registration is configured to emit one.
    """

    trace_id = str(uuid.uuid4())
    bind_request_context(trace_id=trace_id, tenant_id=request.tenant_id)

    token_verifier: TokenVerifier | None = app.state.token_verifier
    if token_verifier is None:
        user_id = request.user_id
        roles = request.roles
        claims = request.claims
    else:
        if authorization is None or not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Authorization: Bearer <token> header is required",
            )
        token = authorization.removeprefix("Bearer ").strip()
        try:
            verified = token_verifier.verify(token)
        except TokenVerificationError as exc:
            logger.warning("bearer token verification failed: %s", exc)
            raise HTTPException(status_code=401, detail="invalid or expired bearer token") from exc

        # The verified token wins outright -- any user_id/roles/claims the
        # caller also put in the request body are ignored, never merged
        # with verified data.
        user_id = verified.subject
        roles = verified.roles
        claims = verified.raw_claims

    request_context = RequestContext(
        tenant_id=request.tenant_id,
        user_id=user_id,
        trace_id=trace_id,
        roles=roles,
        claims=claims,
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
