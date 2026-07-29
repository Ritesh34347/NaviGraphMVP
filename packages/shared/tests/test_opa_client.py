"""Unit tests for `HttpOpaClient` and `FakeOpaClient`.

`HttpOpaClient` is tested against a real `httpx.MockTransport` (an
in-process fake transport httpx itself provides) rather than a live OPA
server or `unittest.mock.patch` -- this exercises the actual request/
response parsing code path for real, matching this repo's preference for
real integration surfaces over mocked-out internals wherever a cheap real
substitute exists.
"""

from __future__ import annotations

import httpx
import pytest

from navigraph_shared.opa.client import FakeOpaClient, HttpOpaClient, OpaDecisionResponse
from navigraph_shared.opa.settings import OpaSettings


def _client_with_transport(handler) -> HttpOpaClient:
    transport = httpx.MockTransport(handler)
    return HttpOpaClient(OpaSettings(opa_url="http://opa-test:8181"), transport=transport)


@pytest.mark.asyncio
async def test_evaluate_posts_to_the_real_data_api_path_with_input_wrapper() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.content
        return httpx.Response(200, json={"result": {"allow": True, "deny_reasons": []}})

    client = _client_with_transport(handler)
    result = await client.evaluate(
        package_path="navigraph/authz/decision",
        input_document={"tenant_id": "acme", "roles": ["analyst"]},
    )

    assert captured["url"] == "http://opa-test:8181/v1/data/navigraph/authz/decision"
    assert b'"input"' in captured["body"]
    assert b'"tenant_id":"acme"' in captured["body"]
    assert result == OpaDecisionResponse(allow=True, deny_reasons=[])


@pytest.mark.asyncio
async def test_evaluate_parses_deny_with_reasons() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"result": {"allow": False, "deny_reasons": ["no role in [] is authorized"]}},
        )

    client = _client_with_transport(handler)
    result = await client.evaluate(package_path="navigraph/authz/decision", input_document={})

    assert result.allow is False
    assert result.deny_reasons == ["no role in [] is authorized"]


@pytest.mark.asyncio
async def test_evaluate_defaults_missing_result_fields_to_deny() -> None:
    """A malformed/empty OPA response (e.g. an undefined rule) must never
    be silently treated as an allow."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {}})

    client = _client_with_transport(handler)
    result = await client.evaluate(package_path="navigraph/authz/decision", input_document={})

    assert result.allow is False
    assert result.deny_reasons == []


@pytest.mark.asyncio
async def test_evaluate_raises_on_unreachable_opa() -> None:
    """`evaluate` MAY raise -- callers (PolicyAuthorizationAgent) are
    expected to catch it and fail closed, never treat an exception here
    as an implicit allow."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _client_with_transport(handler)

    with pytest.raises(httpx.ConnectError):
        await client.evaluate(package_path="navigraph/authz/decision", input_document={})


@pytest.mark.asyncio
async def test_evaluate_raises_on_non_2xx_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    client = _client_with_transport(handler)

    with pytest.raises(httpx.HTTPStatusError):
        await client.evaluate(package_path="navigraph/authz/decision", input_document={})


@pytest.mark.asyncio
async def test_fake_opa_client_records_calls_and_returns_fixed_response() -> None:
    client = FakeOpaClient(response=OpaDecisionResponse(allow=True, deny_reasons=[]))

    result = await client.evaluate(
        package_path="navigraph/authz/decision", input_document={"tenant_id": "acme"}
    )

    assert result.allow is True
    assert client.calls == [
        {
            "package_path": "navigraph/authz/decision",
            "input_document": {"tenant_id": "acme"},
        }
    ]


@pytest.mark.asyncio
async def test_fake_opa_client_bool_shorthand() -> None:
    client = FakeOpaClient(response=False)

    result = await client.evaluate(package_path="navigraph/authz/decision", input_document={})

    assert result == OpaDecisionResponse(allow=False, deny_reasons=[])


@pytest.mark.asyncio
async def test_fake_opa_client_raise_exc_simulates_opa_unreachable() -> None:
    client = FakeOpaClient(raise_exc=ConnectionError("opa unreachable"))

    with pytest.raises(ConnectionError, match="opa unreachable"):
        await client.evaluate(package_path="navigraph/authz/decision", input_document={})


@pytest.mark.asyncio
async def test_fake_opa_client_no_response_configured_defaults_to_deny() -> None:
    client = FakeOpaClient()

    result = await client.evaluate(package_path="navigraph/authz/decision", input_document={})

    assert result.allow is False


def test_fake_opa_client_rejects_more_than_one_response_mode() -> None:
    with pytest.raises(ValueError, match="at most one"):
        FakeOpaClient(response=True, raise_exc=RuntimeError("x"))
