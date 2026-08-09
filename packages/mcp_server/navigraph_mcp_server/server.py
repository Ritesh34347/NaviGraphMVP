"""The NaviGraph MCP tool-surface server (Phase 14.2).

Wraps the gateway's real `POST /ask` (and `GET /healthz`) as MCP tools, so
an external agentic client (Claude Desktop, another agent framework that
speaks MCP) can call NaviGraph exactly like any other tool, without
knowing anything about the gateway's HTTP contract.

This server is a thin, stateless adapter -- it holds no session/tenant
state of its own beyond forwarding whatever the caller passes. Every real
piece of business logic (understanding, guardrails, execution, insight)
still lives entirely in the existing agent-runtime pipeline behind the
gateway; this module's only job is protocol translation.

Deliberately never lets a tool call raise: a gateway/network failure
becomes a structured `{"ok": False, "error": ...}` result, mirroring this
codebase's established "never crash, return a recoverable structured
error" discipline (`AgentError`, the web chat UI's own `/api/ask` error
shape) rather than surfacing a raw exception through the MCP transport.
"""

from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from navigraph_mcp_server.settings import get_mcp_server_settings

# NOTE: this module deliberately does NOT `from __future__ import
# annotations`, unlike every other module in this codebase. `FastMCP.tool()`
# introspects each tool function's *live* `inspect.signature(fn)` at
# decoration time to build its JSON-schema input spec, including a real
# `issubclass(param.annotation, Context)` check for a special optional
# parameter. With postponed evaluation of annotations enabled, every
# annotation becomes a plain string instead of a real type object, and that
# `issubclass()` call raises `TypeError: issubclass() arg 1 must be a
# class` for every single tool -- found for real running this module's own
# test suite. `str | None`/`list[str] | None` below are still fine as live
# (non-postponed) expressions on Python 3.11 (PEP 604), so no other syntax
# change was needed once this file stopped postponing evaluation.


def build_server(*, http_client: httpx.AsyncClient | None = None) -> FastMCP:
    """Construct the MCP server. `http_client` is injectable so tests can
    pass one built with `httpx.MockTransport` instead of a real gateway --
    see `tests/test_server.py`. When omitted, builds a real client against
    this process's configured `gateway_base_url`."""

    client = http_client or httpx.AsyncClient(
        base_url=get_mcp_server_settings().gateway_base_url, timeout=60.0
    )

    mcp = FastMCP(
        "navigraph",
        instructions=(
            "Tools for querying NaviGraph, a multi-tenant conversational BI "
            "platform. Use ask_navigraph to ask a natural-language question "
            "about a tenant's business data and get back a real, executed "
            "answer (a narrative, a result table, or a request for "
            "clarification -- never a hallucinated answer)."
        ),
    )

    @mcp.tool()
    async def ask_navigraph(
        question: str,
        tenant_id: str,
        user_id: str,
        session_id: str | None = None,
        roles: list[str] | None = None,
    ) -> dict[str, Any]:
        """Ask NaviGraph a natural-language question about a tenant's real
        business data. Runs the full real pipeline (intent understanding,
        schema mapping, guardrails, SQL execution, insight generation) and
        returns its actual result -- never a fabricated answer.

        Args:
            question: The natural-language question to ask.
            tenant_id: Which NaviGraph tenant's data to query.
            user_id: The identity asking the question (for audit/lineage).
            session_id: Pass back the `session_id` from a previous
                `ask_navigraph` call to continue that same conversation
                (needed to answer a `needs_clarification` follow-up).
            roles: The asker's roles (e.g. `["analyst"]`), used by
                NaviGraph's real authorization policy. Defaults to no roles
                if omitted, which most tenant policies will deny.

        Returns:
            On success, the real `RequestOrchestratorOutput` JSON verbatim
            (see its `result.outcome`: `"answered"`, `"needs_clarification"`,
            or `"failed"`). On a transport-level failure, a
            `{"ok": False, "error": ...}` dict instead of a raised exception.
        """

        payload = {
            "question": question,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "session_id": session_id,
            "roles": roles or [],
        }
        try:
            response = await client.post("/ask", json=payload)
        except httpx.HTTPError as exc:
            return {"ok": False, "error": f"gateway request failed: {exc}"}

        if response.status_code >= 400:
            return {
                "ok": False,
                "error": "gateway returned an error",
                "status_code": response.status_code,
                "detail": _safe_body(response),
            }

        return response.json()

    @mcp.tool()
    async def check_navigraph_health() -> dict[str, Any]:
        """Check whether the NaviGraph gateway is reachable and healthy."""

        try:
            response = await client.get("/healthz")
        except httpx.HTTPError as exc:
            return {"ok": False, "error": f"gateway request failed: {exc}"}

        return {"ok": response.status_code < 400, "status_code": response.status_code}

    return mcp


def _safe_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text
