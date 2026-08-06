"""Real unit tests for the MCP tool functions (`navigraph_gateway.mcp_tools`).

Tests the module-level functions directly (`ask_navigraph`,
`resolve_business_term`, `list_data_sources`, `list_business_glossary`,
`get_lineage`) against a mocked `httpx.AsyncClient` (via
`httpx.MockTransport`, the same injection pattern already used for
`HttpOpaClient`'s tests in `navigraph_shared`) -- no real agent-runtime,
Postgres, or Neo4j needed. `asyncio_mode = "auto"` is set at the workspace
root `packages/pyproject.toml`, so `async def test_...` functions run
without an explicit `@pytest.mark.asyncio` decorator.

A separate `TestBuildMcpServer` class proves `build_mcp_server`'s
registered tools are reachable via the real MCP protocol (`FastMCP.
call_tool`), not just that the underlying functions work in isolation.
"""

from __future__ import annotations

import json

import httpx
import pytest

from navigraph_gateway.mcp_tools import (
    ask_navigraph,
    build_mcp_server,
    get_lineage,
    list_business_glossary,
    list_data_sources,
    resolve_business_term,
)
from navigraph_gateway.settings import GatewaySettings


def _client_for(handler) -> httpx.AsyncClient:
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(transport=transport, base_url="http://agent-runtime-test")


class TestAskNavigraph:
    async def test_posts_to_request_orchestrator_and_returns_response_verbatim(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"result": {"outcome": "answered"}})

        client = _client_for(handler)

        result = await ask_navigraph(
            client,
            question="What is the total transaction volume by market?",
            tenant_id="tenant-a",
            user_id="user-1",
            roles=["analyst"],
            claims={"tenant_id": "tenant-a"},
        )

        assert result == {"result": {"outcome": "answered"}}
        assert captured["url"].endswith("/agents/orchestrator/request_orchestrator/invoke")
        assert captured["body"]["request_context"]["tenant_id"] == "tenant-a"
        assert captured["body"]["request_context"]["roles"] == ["analyst"]
        assert captured["body"]["request_context"]["claims"] == {"tenant_id": "tenant-a"}
        assert captured["body"]["payload"]["question"] == (
            "What is the total transaction volume by market?"
        )

    async def test_defaults_roles_and_claims_when_omitted(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={})

        client = _client_for(handler)

        await ask_navigraph(client, question="q", tenant_id="t", user_id="u")

        assert captured["body"]["request_context"]["roles"] == []
        assert captured["body"]["request_context"]["claims"] == {}

    async def test_raises_on_http_error(self) -> None:
        client = _client_for(lambda request: httpx.Response(502, text="bad gateway"))

        with pytest.raises(httpx.HTTPStatusError):
            await ask_navigraph(client, question="q", tenant_id="t", user_id="u")


class TestResolveBusinessTerm:
    async def test_calls_ontology_agent_directly_with_the_term_as_a_single_entity(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200, json={"result": {"concept_resolutions": [{"term": "revenue", "resolved": True}]}}
            )

        client = _client_for(handler)

        result = await resolve_business_term(
            client, term="revenue", tenant_id="tenant-a", user_id="user-1"
        )

        assert result["result"]["concept_resolutions"][0]["term"] == "revenue"
        assert captured["url"].endswith("/agents/understanding/ontology/invoke")
        assert captured["body"]["payload"]["entities"] == ["revenue"]
        assert captured["body"]["payload"]["intent"]  # non-empty default


class TestListDataSources:
    async def test_calls_the_plain_data_sources_route_with_tenant_id(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"tenant_id": "tenant-a", "data_sources": []})

        client = _client_for(handler)

        result = await list_data_sources(client, tenant_id="tenant-a")

        assert result == {"tenant_id": "tenant-a", "data_sources": []}
        assert "/data_sources" in captured["url"]
        assert "tenant_id=tenant-a" in captured["url"]


class TestListBusinessGlossary:
    async def test_calls_the_plain_glossary_route_with_tenant_id(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"tenant_id": "tenant-a", "concepts": []})

        client = _client_for(handler)

        result = await list_business_glossary(client, tenant_id="tenant-a")

        assert result == {"tenant_id": "tenant-a", "concepts": []}
        assert "/glossary" in captured["url"]


class TestGetLineage:
    async def test_calls_the_lineage_route_for_the_given_trace_id(self) -> None:
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"trace_id": "trace-1", "events": []})

        client = _client_for(handler)

        result = await get_lineage(client, trace_id="trace-1", tenant_id="tenant-a")

        assert result == {"trace_id": "trace-1", "events": []}
        assert "/lineage/trace-1" in captured["url"]


class TestBuildMcpServer:
    """Proves the tools are reachable via the real MCP protocol layer
    (`FastMCP.call_tool`), not just callable as bare Python functions."""

    async def test_registers_all_five_tools_with_clean_names(self) -> None:
        client = _client_for(lambda request: httpx.Response(200, json={}))
        mcp_server = build_mcp_server(http_client=client, settings=GatewaySettings())

        tools = await mcp_server.list_tools()
        tool_names = {t.name for t in tools}

        assert tool_names == {
            "ask_navigraph",
            "resolve_business_term",
            "list_data_sources",
            "list_business_glossary",
            "get_lineage",
        }

    async def test_call_tool_list_data_sources_round_trips_through_mcp(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "tenant_id": "tenant-a",
                    "data_sources": [{"id": "ds-1", "name": "prod", "source_type": "snowflake"}],
                },
            )

        client = _client_for(handler)
        mcp_server = build_mcp_server(http_client=client, settings=GatewaySettings())

        # `call_tool` returns `(content_blocks, structured_dict)` when the
        # tool's return type is annotated `dict[str, Any]` (confirmed live
        # against the installed `mcp==1.28.1` -- structured-output mode).
        _content_blocks, structured = await mcp_server.call_tool(
            "list_data_sources", {"tenant_id": "tenant-a"}
        )

        assert structured["data_sources"][0]["source_type"] == "snowflake"
