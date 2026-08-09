"""NaviGraph agent-runtime service.

Hosts the agent registry (see `registry.py`) and serves each registered
agent over HTTP (see `main.py`). All 25 real agents across the
Understanding, Query, Guardrail, Insight, Ops, and Orchestrator domains are
registered at startup, including the Request Orchestrator
(`orchestrator.request_orchestrator`), which calls 19 of them directly, in
sequence, for the normal request lifecycle -- see
`docs/architecture/single-stage-mvp.md` at the repo root for the real call
order and outcome model, and LIMITATIONS.md for what's still deferred
(e.g. OPA's placeholder policy, `query.caching` not yet wired into that
sequence).
"""

__version__ = "0.1.0"
