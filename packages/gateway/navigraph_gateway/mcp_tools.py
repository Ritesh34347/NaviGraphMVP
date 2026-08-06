"""MCP (Model Context Protocol) tool server for NaviGraph.

Exposes NaviGraph's real capabilities as MCP tools so external AI agents
(not just the existing chat UI) can reason over the enterprise headlessly,
via a standardized tool-calling protocol rather than needing to know
NaviGraph's internal REST contracts. Mounted onto the EXISTING gateway
FastAPI app at the root (see `main.py`) rather than standing up a new
service -- reuses the already-provisioned shared `httpx.AsyncClient`, the
existing Kubernetes deployment/ingress/CD pipeline, and avoids a second
connection pool.

A separate MCP service was considered and rejected: it would only be
justified by an independent scaling/auth boundary from `/ask`, which
doesn't apply here -- gateway already owns tenant/role context
construction and the shared HTTP client to agent-runtime.

MOUNTING GOTCHA (found live while designing this, confirmed via a real
smoke test against the installed `mcp==1.28.1` package): `FastMCP.
streamable_http_app()` returns a Starlette app whose OWN internal route is
already `/mcp` -- mounting it at `/mcp` on the outer app would produce
`/mcp/mcp`. It must be mounted at the OUTER app's ROOT, LAST (after every
other route -- Starlette matches in registration order and a root mount
matches every path), so the effective path is exactly `/mcp` without
shadowing `/healthz`/`/ask`/etc. Starlette also does not run a mounted
sub-app's own lifespan, so `session_manager.run()` must be entered inside
the OUTER app's own `lifespan()` (see `main.py`) -- without it, every real
request fails with `RuntimeError: Task group is not initialized`.

DNS-REBINDING GOTCHA (also found live): `FastMCP`'s
`TransportSecuritySettings` defaults to allowing only `127.0.0.1`/
`localhost`/`[::1]` -- an unconfigured mount would reject every real
request at the actual deployed hostname with `421 Misdirected Request`.
`GatewaySettings.mcp_allowed_hosts`/`mcp_allowed_origins` (see
`settings.py`) must be passed in explicitly.

AUTH: each tool takes explicit `tenant_id`/`user_id`/`roles`/`claims`
parameters, mirroring `/ask`'s `AskRequest` body exactly -- the same
trust model (see `main.py`'s module docstring on Azure AD verification
not yet being wired to a live tenant). MCP tool functions are not FastAPI
path operations, so they can't use `Depends()`; the official SDK's
HTTP-header-passthrough helpers are third-party/unstable, so explicit
params are the portable, well-supported choice for `mcp.server.fastmcp.
FastMCP` today. Once a real Azure AD tenant is wired (see
`navigraph_shared.auth.azure_ad`), the natural next step is an ASGI
middleware in front of this mount that verifies the `Authorization`
header and overrides these params server-side -- not built yet since
there's no live tenant to test it against.

Each real capability is a plain, MODULE-LEVEL async function taking
`http_client` as its first parameter (directly unit-testable with a mock
transport, no MCP protocol machinery involved) -- `build_mcp_server`
registers thin `@mcp_server.tool()` closures over these, so the actual
logic is tested once, directly, rather than only reachable through
`FastMCP.call_tool`'s JSON-RPC content-block serialization.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from navigraph_gateway.settings import GatewaySettings

# Default `intent` for a bare term/concept lookup via `resolve_business_term`.
# Ontology's own concept-resolution path does not use `intent` at all (only
# `SchemaMappingAgent`'s downstream role-assignment heuristic does) -- this
# default is never semantically load-bearing for this tool, just a value
# `OntologyPayload.intent` requires structurally.
_DEFAULT_TERM_LOOKUP_INTENT = "metric_lookup"


async def ask_navigraph(
    http_client: httpx.AsyncClient,
    *,
    question: str,
    tenant_id: str,
    user_id: str,
    roles: list[str] | None = None,
    claims: dict[str, Any] | None = None,
    session_id: str | None = None,
    data_source_id: str | None = None,
) -> dict[str, Any]:
    """Ask NaviGraph a business question end-to-end.

    Runs the full real pipeline (Understanding -> Query -> Guardrail ->
    Insight, with lineage recorded at every stage) via the Request
    Orchestrator agent -- the exact same call the `/ask` REST endpoint
    makes. Returns the real `RequestOrchestratorOutput` verbatim (chart
    spec, grounded narrative, follow-up suggestions, the actual executed
    SQL, and either the real answer or a clarifying question/failure
    reason).
    """

    request_context = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "trace_id": str(uuid.uuid4()),
        "roles": roles or [],
        "claims": claims or {},
    }
    payload = {
        "question": question,
        "session_id": session_id,
        "data_source_id": data_source_id,
    }

    response = await http_client.post(
        "/agents/orchestrator/request_orchestrator/invoke",
        json={"request_context": request_context, "payload": payload},
    )
    response.raise_for_status()
    return response.json()


async def resolve_business_term(
    http_client: httpx.AsyncClient,
    *,
    term: str,
    tenant_id: str,
    user_id: str,
    roles: list[str] | None = None,
    claims: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a single business term or concept without a full SQL
    round-trip.

    Calls the Ontology agent directly -- useful for "what does 'Net
    Revenue' mean" or "which table/column does this concept map to"
    without generating and executing SQL. Returns the real
    `concept_resolutions`/`relationship_resolutions`/`unresolved_terms`
    for that one term.
    """

    request_context = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "trace_id": str(uuid.uuid4()),
        "roles": roles or [],
        "claims": claims or {},
    }
    payload = {"entities": [term], "intent": _DEFAULT_TERM_LOOKUP_INTENT}

    response = await http_client.post(
        "/agents/understanding/ontology/invoke",
        json={"request_context": request_context, "payload": payload},
    )
    response.raise_for_status()
    return response.json()


async def list_data_sources(http_client: httpx.AsyncClient, *, tenant_id: str) -> dict[str, Any]:
    """List every data source registered for a tenant (id, name,
    source_type -- e.g. snowflake/postgres/databricks)."""

    response = await http_client.get("/data_sources", params={"tenant_id": tenant_id})
    response.raise_for_status()
    return response.json()


async def list_business_glossary(
    http_client: httpx.AsyncClient, *, tenant_id: str
) -> dict[str, Any]:
    """List every real business concept in the tenant's glossary (name,
    synonyms, and the catalog column/table it maps to) -- useful for an
    agent that wants to browse what NaviGraph already knows about the
    business's terminology before asking a question."""

    response = await http_client.get("/glossary", params={"tenant_id": tenant_id})
    response.raise_for_status()
    return response.json()


async def get_lineage(
    http_client: httpx.AsyncClient, *, trace_id: str, tenant_id: str
) -> dict[str, Any]:
    """Retrieve the full assembled lineage chain for a previous
    `ask_navigraph` call's `trace_id` -- every agent that ran, in order,
    with input/output summaries."""

    response = await http_client.get(f"/lineage/{trace_id}", params={"tenant_id": tenant_id})
    response.raise_for_status()
    return response.json()


def build_mcp_server(*, http_client: httpx.AsyncClient, settings: GatewaySettings) -> FastMCP:
    """Construct the FastMCP instance with all 5 NaviGraph tools registered
    as thin closures over the module-level functions above, each capturing
    the gateway's ALREADY-CONSTRUCTED shared `http_client` (matches `/ask`'s
    existing call pattern exactly -- one connection pool, not one per tool).
    """

    mcp_server = FastMCP(
        "navigraph",
        instructions=(
            "NaviGraph is a multi-tenant conversational BI platform. "
            "Use ask_navigraph to answer a business question end-to-end "
            "(NL -> SQL -> guardrails -> chart/narrative). Use "
            "resolve_business_term for a lightweight glossary/ontology "
            "lookup without a full SQL round-trip. Every tool requires a "
            "tenant_id and the caller's roles/claims (RBAC is enforced "
            "downstream by OPA)."
        ),
        transport_security=TransportSecuritySettings(
            allowed_hosts=settings.mcp_allowed_hosts,
            allowed_origins=settings.mcp_allowed_origins,
        ),
    )

    @mcp_server.tool(name="ask_navigraph")
    async def ask_navigraph_tool(
        question: str,
        tenant_id: str,
        user_id: str,
        roles: list[str] | None = None,
        claims: dict[str, Any] | None = None,
        session_id: str | None = None,
        data_source_id: str | None = None,
    ) -> dict[str, Any]:
        """Ask NaviGraph a business question end-to-end (NL -> SQL ->
        guardrails -> chart/narrative), via the real Request Orchestrator."""

        return await ask_navigraph(
            http_client,
            question=question,
            tenant_id=tenant_id,
            user_id=user_id,
            roles=roles,
            claims=claims,
            session_id=session_id,
            data_source_id=data_source_id,
        )

    @mcp_server.tool(name="resolve_business_term")
    async def resolve_business_term_tool(
        term: str,
        tenant_id: str,
        user_id: str,
        roles: list[str] | None = None,
        claims: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Resolve a single business term/concept without a full SQL
        round-trip, via the Ontology agent directly."""

        return await resolve_business_term(
            http_client,
            term=term,
            tenant_id=tenant_id,
            user_id=user_id,
            roles=roles,
            claims=claims,
        )

    @mcp_server.tool(name="list_data_sources")
    async def list_data_sources_tool(tenant_id: str) -> dict[str, Any]:
        """List every data source registered for a tenant."""

        return await list_data_sources(http_client, tenant_id=tenant_id)

    @mcp_server.tool(name="list_business_glossary")
    async def list_business_glossary_tool(tenant_id: str) -> dict[str, Any]:
        """List every real business concept in the tenant's glossary."""

        return await list_business_glossary(http_client, tenant_id=tenant_id)

    @mcp_server.tool(name="get_lineage")
    async def get_lineage_tool(trace_id: str, tenant_id: str) -> dict[str, Any]:
        """Retrieve the full assembled lineage chain for a trace_id."""

        return await get_lineage(http_client, trace_id=trace_id, tenant_id=tenant_id)

    return mcp_server
