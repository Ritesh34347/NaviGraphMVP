"""NaviGraph agent-runtime service.

Hosts the agent registry (see `registry.py`) and serves each registered
agent over HTTP (see `main.py`). All 25 real agents across the
Understanding, Query, Guardrail, Insight, Ops, and Orchestrator domains are
registered at startup, including the Request Orchestrator
(`orchestrator.request_orchestrator`), which calls 20 of them directly, in
sequence, for the normal request lifecycle (19 in the linear sequence, plus
Caching wrapping Data Federation with a real lookup/store) -- see
`docs/architecture/single-stage-mvp.md` at the repo root for the real call
order and outcome model, and LIMITATIONS.md for what's still deferred
(e.g. no real Azure AD JWT verification behind OPA's already-real RBAC/ABAC
policy).
"""

__version__ = "0.1.0"
