"""Shared agent contract types.

Re-exports the real implementations from `agent_io.py` so callers can write
`from navigraph_shared.contracts import RequestContext` etc.
"""

from navigraph_shared.contracts.agent_io import (
    AgentError,
    AgentInput,
    AgentMetadata,
    AgentOutput,
    LineageEvent,
    RequestContext,
)

__all__ = [
    "AgentError",
    "AgentInput",
    "AgentMetadata",
    "AgentOutput",
    "LineageEvent",
    "RequestContext",
]
