# Agent Contract

This is the formal specification every agent in NaviGraph must implement,
regardless of domain. If you are adding a new agent, this document — not
convention or imitation of a neighboring agent — is the source of truth.

## Directory shape

Every agent lives at
`packages/agent_runtime/navigraph_agents/<domain>/<agent_name>/` and contains:

```
<agent_name>/
  agent.py        # the agent's logic: input -> (LLM/graph/db calls) -> output
  contracts.py     # AgentInput / AgentOutput Pydantic models for this agent
  prompts/         # optional: prompt templates, versioned
  tests/           # required: unit tests for this agent
```

`prompts/` is optional (some agents, e.g. deterministic validators, have no
LLM prompt at all). `agent.py`, `contracts.py`, and `tests/` are required for
every agent.

## `AgentInput`

Every agent's input model **must** include a non-optional `RequestContext`:

```python
class RequestContext(BaseModel):
    tenant_id: str
    user_id: str
    trace_id: str
    roles: list[str]
    claims: dict[str, Any] = {}

class AgentInput(BaseModel):
    context: RequestContext
    # ... agent-specific fields below
```

`RequestContext` is non-optional because every agent call must be attributable
to a tenant and user, and traceable via `trace_id` — there is no code path in
NaviGraph where an agent runs without this context, including in tests (use a
fixture `RequestContext`, not `None`).

## `AgentOutput`

Every agent's output model **must** include:

```python
class AgentOutput(BaseModel):
    result: Any                    # agent-specific payload
    confidence: float              # 0.0-1.0
    lineage_events: list[LineageEvent]  # must be non-empty
    errors: list[AgentError] = []
    metadata: AgentMetadata

class AgentMetadata(BaseModel):
    tokens: TokenUsage | None = None
    latency_ms: float
    model_version: str | None = None
    prompt_version: str | None = None
```

`lineage_events` must never be empty, even for deterministic/non-LLM agents —
every agent invocation is an auditable event in the request's lineage trail,
which is what makes NaviGraph's answers explainable end-to-end (see
`data-flow.md`). `errors` is populated (rather than raising) when the agent can
produce a partial or degraded result that the orchestrator may still want to
use; raise an exception only for conditions the orchestrator cannot reasonably
handle.

## Dual invocation

Every agent supports two invocation paths against the same underlying logic:

1. **In-process, as a LangGraph node.** The Orchestrator domain's graph calls
   the agent directly as a Python function/node for the normal request
   lifecycle. This is the hot path and avoids network overhead between agents
   in the same request.
2. **Thin HTTP wrapper**: `POST /agents/{domain}/{agent_name}/invoke`, accepting
   a JSON body matching that agent's `AgentInput` and returning its
   `AgentOutput`. This exists so the agent can be invoked in isolation — by the
   eval harness, by integration tests, or for manual debugging — without
   standing up the full orchestrator graph.

Both paths must produce identical output for identical input; the HTTP wrapper
is a thin `FastAPI` route that deserializes, calls the same `agent.py` entry
point, and serializes the result. Do not duplicate logic between the two
paths.

## Unit-testing convention

- **Default**: every agent's `tests/` uses a fake or mocked LLM client. Tests
  must be fast, deterministic, and runnable with no network access and no API
  key.
- **Real-LLM tests**: any test that calls a real LLM (Anthropic Claude via the
  provider-agnostic client) must be marked `@pytest.mark.llm_integration` so it
  can be excluded from the default fast test run (`pytest packages/` in CI
  excludes this marker by default; run `pytest -m llm_integration` explicitly
  to include them). These tests require `ANTHROPIC_API_KEY` to be set and are
  not part of the required CI gate for every PR — they are for deliberate,
  occasional verification that prompts still behave against the real model.

## Reference implementation

The one agent that is fully real today —
`packages/agent_runtime/navigraph_agents/understanding/intent_understanding/`
— is the canonical example of everything in this document: its
`contracts.py` shows a real `AgentInput`/`AgentOutput` pair, its `agent.py`
shows the LLM-call-plus-lineage-emission pattern, and its `tests/` shows the
fake-LLM-by-default convention in practice. When in doubt about how to
interpret this spec, read that implementation first.
