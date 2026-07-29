"""LLM client abstractions: provider-agnostic base + Anthropic + fake test double."""

from navigraph_shared.llm.client import (
    AnthropicLLMClient,
    FakeLLMClient,
    LLMClient,
    LLMResponse,
)
from navigraph_shared.llm.json_parsing import strip_json_code_fence

__all__ = [
    "AnthropicLLMClient",
    "FakeLLMClient",
    "LLMClient",
    "LLMResponse",
    "strip_json_code_fence",
]
