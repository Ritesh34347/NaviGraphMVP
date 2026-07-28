"""Simple dict-based agent registry.

`AGENT_REGISTRY` maps a dotted agent key (`"<domain>.<agent_name>"`, matching
the on-disk path `navigraph_agents/<domain>/<agent_name>/`) to that agent's
bound `run` callable. `main.py` populates this registry at startup (after
constructing the appropriate `LLMClient`) and the `/agents/.../invoke` route
handlers look up their agent by key here.

This is intentionally NOT a class-based plugin system -- with exactly one
real agent in this phase, a dict is the simplest thing that could possibly
work, and it is exactly the pattern `tools/scripts/new-agent.py` will keep
extending as more agents are added in later phases.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

AgentRunCallable = Callable[[Any], Awaitable[Any]]

AGENT_REGISTRY: dict[str, AgentRunCallable] = {}


def register(key: str, fn: AgentRunCallable | None = None):
    """Register an agent's `run` callable under `key`.

    Usable two ways:

    1. Directly, after constructing an agent instance (this is how
       `main.py` registers the real Intent Understanding agent, since its
       `run` is a bound instance method that needs a constructed
       `LLMClient`):

           agent = IntentUnderstandingAgent(llm_client=client)
           register("understanding.intent_understanding", agent.run)

    2. As a decorator over a module-level async function, for agents whose
       `run` doesn't need per-instance construction:

           @register("some_domain.some_agent")
           async def run(input: SomeAgentInput) -> SomeAgentOutput:
               ...
    """

    if fn is not None:
        AGENT_REGISTRY[key] = fn
        return fn

    def decorator(f: AgentRunCallable) -> AgentRunCallable:
        AGENT_REGISTRY[key] = f
        return f

    return decorator


def get_agent(key: str) -> AgentRunCallable:
    """Look up a registered agent's `run` callable by key.

    Raises `KeyError` if nothing is registered under `key` -- callers (see
    `main.py`) are expected to translate that into an HTTP 404.
    """

    return AGENT_REGISTRY[key]
