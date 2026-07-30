"""Unit tests for `AnthropicLLMClient`.

Tested against a real `httpx.MockTransport` (mirroring
`navigraph_shared.opa.client.HttpOpaClient`'s identical testing pattern),
not `unittest.mock.patch` on internals -- this exercises the actual
response-parsing code path for real, including the retry-on-empty-text
behavior below.
"""

from __future__ import annotations

import httpx
import pytest

from navigraph_shared.llm.client import AnthropicLLMClient

_WELL_FORMED_BODY = {
    "id": "msg_test",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [{"type": "text", "text": "Hello from the model."}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 5},
}

_EMPTY_TEXT_BODY = {
    "id": "msg_empty",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 10, "output_tokens": 0},
}


def _client_with_handler(handler) -> AnthropicLLMClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport)
    return AnthropicLLMClient(api_key="test-key", http_client=http_client)


@pytest.mark.asyncio
async def test_well_formed_response_returns_text_without_retry() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_WELL_FORMED_BODY)

    client = _client_with_handler(handler)
    response = await client.complete(system="You are helpful.", messages=[{"role": "user", "content": "hi"}])

    assert response.text == "Hello from the model."
    assert response.tokens_input == 10
    assert response.tokens_output == 5
    assert response.model == "claude-sonnet-5"
    assert call_count == 1


@pytest.mark.asyncio
async def test_empty_first_response_retries_once_and_returns_second_response() -> None:
    """REAL BUG, found live against a real model: the API can return a
    genuine 200 response with zero real text content blocks. Exactly one
    retry must be attempted, and the SECOND response's text/tokens/model
    must be what the caller ultimately receives."""

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return httpx.Response(200, json=_EMPTY_TEXT_BODY)
        return httpx.Response(200, json=_WELL_FORMED_BODY)

    client = _client_with_handler(handler)
    response = await client.complete(system="You are helpful.", messages=[{"role": "user", "content": "hi"}])

    assert response.text == "Hello from the model."
    assert response.tokens_output == 5
    assert call_count == 2


@pytest.mark.asyncio
async def test_both_responses_empty_gives_up_after_exactly_one_retry() -> None:
    """Must not retry indefinitely -- exactly one retry, then return
    whatever the second attempt produced even if still empty, leaving the
    caller's own existing malformed-response handling to degrade
    gracefully."""

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json=_EMPTY_TEXT_BODY)

    client = _client_with_handler(handler)
    response = await client.complete(system="You are helpful.", messages=[{"role": "user", "content": "hi"}])

    assert response.text == ""
    assert call_count == 2
