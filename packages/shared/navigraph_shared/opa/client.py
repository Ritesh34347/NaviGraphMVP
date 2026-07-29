"""OPA (Open Policy Agent) client abstraction.

`OpaClient` is the abstract base every agent codes against. Two concrete
implementations are provided, mirroring `navigraph_shared.llm.client`'s
exact `LLMClient`/`AnthropicLLMClient`/`FakeLLMClient` triad:

- `HttpOpaClient` -- a REAL implementation backed by `httpx.AsyncClient`,
  calling OPA's real HTTP Data API (`POST /v1/data/<package_path>`).
- `FakeOpaClient` -- a no-network test double that returns a canned
  decision (or the result of a callable) and records every call made to
  it, so unit tests can assert on exactly what was sent to "OPA" without
  ever making a real HTTP request or requiring a running OPA server.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from navigraph_shared.opa.settings import OpaSettings


class OpaDecisionResponse(BaseModel):
    """Normalized decision shape returned by every `OpaClient` implementation.

    Mirrors the real `authz.rego` policy's `decision` rule output
    (`{"allow": bool, "deny_reasons": [...]}`) -- see
    `infra/opa/policies/authz.rego`.
    """

    model_config = ConfigDict(extra="forbid")

    allow: bool
    deny_reasons: list[str] = Field(default_factory=list)


class OpaClient(ABC):
    """Abstract base class for an OPA Data-API client."""

    @abstractmethod
    async def evaluate(
        self, *, package_path: str, input_document: dict[str, Any]
    ) -> OpaDecisionResponse:
        """Evaluate `package_path` (e.g. `"navigraph/authz/decision"`)
        against `input_document`, returning a normalized decision.

        Args:
            package_path: The Rego package + rule path, exactly as it
                appears after `/v1/data/` in OPA's real HTTP Data API
                (e.g. a `package navigraph.authz` with a `decision` rule
                is queried as `"navigraph/authz/decision"`).
            input_document: The `input` document OPA evaluates the policy
                against -- e.g. `{"tenant_id": ..., "roles": [...], ...}`.
        """
        raise NotImplementedError


class HttpOpaClient(OpaClient):
    """Real `OpaClient` implementation backed by `httpx.AsyncClient`.

    Reads `OPA_URL` from the environment (via `OpaSettings`, defaulting to
    the docker-compose in-network DNS name `http://opa:8181`) unless
    explicit `settings` are passed. `transport` is exposed purely for
    tests (an `httpx.MockTransport` can be injected to simulate OPA
    responses -- including an unreachable server -- with no real network
    call), mirroring the dependency-injection style already used by
    `CachingAgent`'s `cache_client` parameter.
    """

    def __init__(
        self,
        settings: OpaSettings | None = None,
        *,
        timeout_seconds: float = 5.0,
        transport: Any | None = None,
    ) -> None:
        self._settings = settings or OpaSettings()
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def evaluate(
        self, *, package_path: str, input_document: dict[str, Any]
    ) -> OpaDecisionResponse:
        # Imported lazily so importing this module never requires `httpx`
        # to be installed unless this class is actually instantiated,
        # mirroring `AnthropicLLMClient`/`SnowflakeConnector`/`Neo4jClient`'s
        # established lazy-driver-import convention.
        import httpx

        url = f"{self._settings.opa_url}/v1/data/{package_path}"

        async with httpx.AsyncClient(
            timeout=self._timeout_seconds, transport=self._transport
        ) as client:
            response = await client.post(url, json={"input": input_document})
            response.raise_for_status()
            body = response.json()

        result = body.get("result", {})
        return OpaDecisionResponse(
            allow=bool(result.get("allow", False)),
            deny_reasons=list(result.get("deny_reasons", [])),
        )


class FakeOpaClient(OpaClient):
    """No-network test double for `OpaClient`.

    Used by every Guardrail agent's own unit tests so they never need a
    real running OPA server.

    Construct with either:
      - `response`: a fixed `OpaDecisionResponse` (or plain bool, auto-
        wrapped as `allow=<bool>, deny_reasons=[]`) returned on every call, or
      - `response_fn`: a callable `(package_path, input_document) ->
        OpaDecisionResponse` for tests that need per-call control, or
      - `raise_exc`: an exception instance raised on every call, to
        simulate OPA being unreachable (connection refused, timeout, etc.)
        -- this is what `PolicyAuthorizationAgent`'s fail-closed behavior
        is tested against.

    Every call is recorded in `self.calls` as a dict with keys
    `package_path` and `input_document`, so tests can assert on exactly
    what was sent to "OPA".
    """

    def __init__(
        self,
        response: OpaDecisionResponse | bool | None = None,
        response_fn: Callable[[str, dict[str, Any]], OpaDecisionResponse] | None = None,
        *,
        raise_exc: Exception | None = None,
    ) -> None:
        provided = [x is not None for x in (response, response_fn, raise_exc)]
        if sum(provided) > 1:
            raise ValueError("pass at most one of response, response_fn, raise_exc")

        self._response_fn = response_fn
        self._raise_exc = raise_exc

        if isinstance(response, bool):
            self._fixed_response: OpaDecisionResponse | None = OpaDecisionResponse(
                allow=response, deny_reasons=[]
            )
        else:
            self._fixed_response = response

        self.calls: list[dict[str, Any]] = []

    async def evaluate(
        self, *, package_path: str, input_document: dict[str, Any]
    ) -> OpaDecisionResponse:
        self.calls.append({"package_path": package_path, "input_document": input_document})

        if self._raise_exc is not None:
            raise self._raise_exc

        if self._response_fn is not None:
            return self._response_fn(package_path, input_document)

        if self._fixed_response is not None:
            return self._fixed_response

        # No canned response configured -- deny by default, matching the
        # real policy's own `default allow = false` rather than silently
        # allowing everything when a test forgets to configure a response.
        return OpaDecisionResponse(allow=False, deny_reasons=["no response configured"])
