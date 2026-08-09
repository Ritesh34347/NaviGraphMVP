"""Real unit tests for the NaviGraph MCP tool-surface server.

No live gateway needed: `httpx.MockTransport` fakes the gateway's HTTP
boundary for real -- these tests exercise the actual `httpx.AsyncClient`
request/response path (headers, JSON encoding/decoding, status-code
branching), not a stub of `ask_navigraph` itself. Tools are called through
`FastMCP.call_tool` (the same entry point a real MCP client uses), and its
`TextContent` results are JSON-parsed back, matching how `build_server`'s
dict returns are actually serialized on the wire.

`asyncio_mode = "auto"` is set in pyproject.toml, so these `async def
test_...` functions run without an explicit `@pytest.mark.asyncio`
decorator.
"""

from __future__ import annotations

import json

import httpx
import pytest

from navigraph_mcp_server.server import build_server


def _client_with_handler(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://gateway:8000")


def _content_to_dict(content) -> dict:
    assert len(content) == 1
    return json.loads(content[0].text)


async def test_ask_navigraph_forwards_the_question_and_returns_the_gateway_response_verbatim() -> None:
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(
            200,
            json={
                "result": {
                    "outcome": "answered",
                    "session_id": "session-abc",
                    "narrative": "Revenue grew 12%.",
                },
                "confidence": 0.9,
            },
        )

    server = build_server(http_client=_client_with_handler(handler))

    result = await server.call_tool(
        "ask_navigraph",
        {
            "question": "What was our revenue growth?",
            "tenant_id": "tenant-acme",
            "user_id": "user-1",
        },
    )

    assert len(captured_requests) == 1
    sent_request = captured_requests[0]
    assert sent_request.method == "POST"
    assert sent_request.url.path == "/ask"
    sent_body = json.loads(sent_request.content)
    assert sent_body["question"] == "What was our revenue growth?"
    assert sent_body["tenant_id"] == "tenant-acme"
    assert sent_body["user_id"] == "user-1"
    assert sent_body["session_id"] is None
    assert sent_body["roles"] == []

    parsed = _content_to_dict(result)
    assert parsed["result"]["outcome"] == "answered"
    assert parsed["result"]["session_id"] == "session-abc"
    assert parsed["result"]["narrative"] == "Revenue grew 12%."


async def test_ask_navigraph_forwards_session_id_and_roles_when_given() -> None:
    captured_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_requests.append(request)
        return httpx.Response(200, json={"result": {"outcome": "answered", "session_id": "s1"}})

    server = build_server(http_client=_client_with_handler(handler))

    await server.call_tool(
        "ask_navigraph",
        {
            "question": "follow-up question",
            "tenant_id": "tenant-acme",
            "user_id": "user-1",
            "session_id": "existing-session",
            "roles": ["analyst", "pii_viewer"],
        },
    )

    sent_body = json.loads(captured_requests[0].content)
    assert sent_body["session_id"] == "existing-session"
    assert sent_body["roles"] == ["analyst", "pii_viewer"]


async def test_ask_navigraph_returns_a_structured_error_on_a_gateway_4xx_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "roles must be a non-empty list"})

    server = build_server(http_client=_client_with_handler(handler))

    result = await server.call_tool(
        "ask_navigraph", {"question": "q", "tenant_id": "t", "user_id": "u"}
    )

    parsed = _content_to_dict(result)
    assert parsed["ok"] is False
    assert parsed["status_code"] == 422
    assert parsed["detail"] == {"detail": "roles must be a non-empty list"}


async def test_ask_navigraph_returns_a_structured_error_when_the_gateway_is_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    server = build_server(http_client=_client_with_handler(handler))

    result = await server.call_tool(
        "ask_navigraph", {"question": "q", "tenant_id": "t", "user_id": "u"}
    )

    parsed = _content_to_dict(result)
    assert parsed["ok"] is False
    assert "gateway request failed" in parsed["error"]


async def test_check_navigraph_health_reports_ok_true_for_a_2xx_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/healthz"
        return httpx.Response(200, json={"status": "ok"})

    server = build_server(http_client=_client_with_handler(handler))

    result = await server.call_tool("check_navigraph_health", {})

    parsed = _content_to_dict(result)
    assert parsed == {"ok": True, "status_code": 200}


async def test_check_navigraph_health_reports_ok_false_for_a_5xx_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    server = build_server(http_client=_client_with_handler(handler))

    result = await server.call_tool("check_navigraph_health", {})

    parsed = _content_to_dict(result)
    assert parsed == {"ok": False, "status_code": 503}


async def test_check_navigraph_health_returns_a_structured_error_when_unreachable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    server = build_server(http_client=_client_with_handler(handler))

    result = await server.call_tool("check_navigraph_health", {})

    parsed = _content_to_dict(result)
    assert parsed["ok"] is False
    assert "gateway request failed" in parsed["error"]


async def test_both_tools_are_registered_with_descriptions() -> None:
    server = build_server(http_client=_client_with_handler(lambda r: httpx.Response(200)))

    tools = await server.list_tools()
    tool_names = {tool.name for tool in tools}

    assert tool_names == {"ask_navigraph", "check_navigraph_health"}
    for tool in tools:
        assert tool.description


@pytest.mark.parametrize(
    "missing_field",
    ["question", "tenant_id", "user_id"],
)
async def test_ask_navigraph_requires_question_tenant_id_and_user_id(missing_field: str) -> None:
    server = build_server(http_client=_client_with_handler(lambda r: httpx.Response(200, json={})))

    args = {"question": "q", "tenant_id": "t", "user_id": "u"}
    del args[missing_field]

    with pytest.raises(Exception):  # noqa: B017 - FastMCP's own arg-validation error type
        await server.call_tool("ask_navigraph", args)
