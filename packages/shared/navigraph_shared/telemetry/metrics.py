"""Prometheus metrics helpers for agent invocations.

Uses `prometheus_client` directly against the library's default global
registry, so these metrics are automatically picked up by whichever HTTP
framework instrumentation exposes `/metrics` in a given service (both the
gateway and agent-runtime FastAPI apps use
`prometheus_fastapi_instrumentator`, which scrapes the same default
registry -- see `navigraph_gateway.main` / `navigraph_agents.main`).
"""

from __future__ import annotations

from prometheus_client import Counter, Histogram

AGENT_INVOCATION_LATENCY_SECONDS = Histogram(
    "navigraph_agent_invocation_latency_seconds",
    "Latency of a single agent invocation, in seconds.",
    labelnames=("agent_name",),
)

AGENT_INVOCATION_TOTAL = Counter(
    "navigraph_agent_invocations_total",
    "Total number of agent invocations.",
    labelnames=("agent_name", "outcome"),
)

AGENT_ERROR_TOTAL = Counter(
    "navigraph_agent_errors_total",
    "Total number of agent errors, keyed by error code.",
    labelnames=("agent_name", "error_code", "recoverable"),
)


def record_agent_invocation(agent_name: str, *, latency_ms: float, success: bool) -> None:
    """Record one completed agent invocation."""

    AGENT_INVOCATION_LATENCY_SECONDS.labels(agent_name=agent_name).observe(latency_ms / 1000.0)
    AGENT_INVOCATION_TOTAL.labels(
        agent_name=agent_name, outcome="success" if success else "error"
    ).inc()


def record_agent_error(agent_name: str, *, error_code: str, recoverable: bool) -> None:
    """Record one agent error (does not raise; purely observational)."""

    AGENT_ERROR_TOTAL.labels(
        agent_name=agent_name,
        error_code=error_code,
        recoverable=str(recoverable).lower(),
    ).inc()
