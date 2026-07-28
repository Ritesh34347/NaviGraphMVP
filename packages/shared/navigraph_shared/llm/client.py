"""Provider-agnostic LLM client abstraction.

`LLMClient` is the abstract base every agent codes against. Two concrete
implementations are provided:

- `AnthropicLLMClient` -- a REAL implementation backed by the `anthropic`
  Python SDK's `AsyncAnthropic` client (see `python/claude-api/README.md` in
  the Anthropic API reference for the exact call shape this mirrors).
- `FakeLLMClient` -- a no-network test double that returns a canned response
  (or the result of a callable) and records every call made to it, so unit
  tests can assert on exactly what was sent to the "model" without ever
  making a real HTTP request or requiring an API key.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel

if TYPE_CHECKING:
    # Only needed for the cast() type annotation below; importing under
    # TYPE_CHECKING keeps `anthropic` an optional runtime dependency for
    # code paths that only ever use FakeLLMClient (e.g. most unit tests).
    from anthropic.types import MessageParam


class LLMResponse(BaseModel):
    """Normalized response shape returned by every `LLMClient` implementation."""

    text: str
    tokens_input: int
    tokens_output: int
    model: str


class LLMClient(ABC):
    """Abstract base class for a chat-completion-style LLM client."""

    @abstractmethod
    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        """Run one completion turn and return a normalized `LLMResponse`.

        Args:
            system: The system prompt.
            messages: A list of `{"role": "user" | "assistant", "content": str}`
                dicts, following the Anthropic Messages API shape.
            max_tokens: Maximum tokens to generate.
        """
        raise NotImplementedError


class AnthropicLLMClient(LLMClient):
    """Real `LLMClient` implementation backed by `anthropic.AsyncAnthropic`.

    Reads the API key from the `ANTHROPIC_API_KEY` environment variable (via
    the SDK's default credential resolution) unless an explicit `api_key` is
    passed. The model name defaults to the `ANTHROPIC_MODEL` env var (falling
    back to `"claude-sonnet-5"`) unless overridden per-instance.

    NOTE ON API SHAPE: this mirrors `anthropic.AsyncAnthropic().messages.create(
        model=..., max_tokens=..., system=..., messages=[...]
    )`. The response's `content` field is a list of content blocks (the SDK
    may also return `thinking` blocks depending on model/config), so we
    filter for `block.type == "text"` rather than assuming `content[0]` is
    text. Token usage is read from `response.usage.input_tokens` /
    `response.usage.output_tokens`.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        # Imported lazily so importing this module never requires the
        # `anthropic` package to be installed unless this class is actually
        # instantiated (keeps FakeLLMClient-only test runs lightweight).
        import anthropic

        self._model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key or os.environ.get("ANTHROPIC_API_KEY") or None,
        )

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            # Our provider-agnostic `list[dict[str, Any]]` shape is
            # structurally the same `{"role": ..., "content": ...}` shape
            # Anthropic's SDK expects; cast rather than widen the abstract
            # LLMClient.complete() signature to a provider-specific type.
            messages=cast("list[MessageParam]", messages),
        )

        text_parts = [block.text for block in response.content if block.type == "text"]
        text = "".join(text_parts)

        return LLMResponse(
            text=text,
            tokens_input=response.usage.input_tokens,
            tokens_output=response.usage.output_tokens,
            model=response.model,
        )


class FakeLLMClient(LLMClient):
    """No-network test double for `LLMClient`.

    Used by default in the unit test suite so tests never need a real
    `ANTHROPIC_API_KEY` or network access.

    Construct with either:
      - `response`: a fixed `LLMResponse` (or plain string, auto-wrapped)
        returned on every call, or
      - `response_fn`: a callable `(system, messages, max_tokens) -> LLMResponse`
        for tests that need per-call control (e.g. simulating malformed JSON).

    Every call is recorded in `self.calls` as a dict with keys `system`,
    `messages`, and `max_tokens`, so tests can assert on exactly what was
    sent to the "model".
    """

    def __init__(
        self,
        response: LLMResponse | str | None = None,
        response_fn: Callable[[str, list[dict[str, Any]], int], LLMResponse] | None = None,
        *,
        model: str = "fake-model",
    ) -> None:
        if response is not None and response_fn is not None:
            raise ValueError("pass either response or response_fn, not both")

        self._model = model
        self._response_fn = response_fn

        if isinstance(response, str):
            self._fixed_response: LLMResponse | None = LLMResponse(
                text=response,
                tokens_input=0,
                tokens_output=0,
                model=model,
            )
        else:
            self._fixed_response = response

        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.calls.append({"system": system, "messages": messages, "max_tokens": max_tokens})

        if self._response_fn is not None:
            return self._response_fn(system, messages, max_tokens)

        if self._fixed_response is not None:
            return self._fixed_response

        # No canned response configured -- return a harmless empty completion
        # rather than raising, so tests that don't care about the LLM output
        # (e.g. only checking lineage/metadata plumbing) don't need to pass one.
        return LLMResponse(text="", tokens_input=0, tokens_output=0, model=self._model)
