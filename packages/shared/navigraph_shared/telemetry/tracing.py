"""OpenTelemetry tracer factory.

`get_tracer(service_name)` sets up a process-wide `TracerProvider` (once) with
an OTLP gRPC span exporter pointed at `OTEL_EXPORTER_OTLP_ENDPOINT` (default
`http://otel-collector:4317`), and returns a `Tracer` for the given service
name.

IMPORTANT: this must never crash the calling service just because the OTel
collector isn't up (e.g. running a single package's tests without the full
docker-compose stack). The `grpc` transport used by
`OTLPSpanExporter` connects lazily, and `BatchSpanProcessor` exports on a
background thread and swallows exporter errors internally (logging a
warning), so a down collector degrades to "spans are dropped after a
network-timeout warning" rather than an exception on the request path. We
additionally wrap provider/exporter *construction* in a try/except and fall
back to a no-op `TracerProvider` (spans created, never exported anywhere) if
construction itself fails for any reason -- e.g. a malformed endpoint URL.
"""

from __future__ import annotations

import logging
import os
import threading

from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import Tracer

logger = logging.getLogger(__name__)

_setup_lock = threading.Lock()
_provider_configured = False


def _build_provider(service_name: str) -> TracerProvider:
    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")

    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    except Exception:  # noqa: BLE001 - tracing setup must never crash the app
        logger.warning(
            "Failed to configure OTLP span exporter for endpoint %s; "
            "spans will be created but not exported.",
            endpoint,
            exc_info=True,
        )

    return provider


def get_tracer(service_name: str) -> Tracer:
    """Return a `Tracer` for `service_name`, configuring the process-wide
    `TracerProvider` once per process on first use.

    Safe to call repeatedly and from multiple modules. Each Python process in
    this codebase (the gateway, the agent-runtime) hosts exactly one
    service, so the first call's `service_name` sets the resource attributes
    for the whole process; subsequent calls just fetch a scoped `Tracer`
    from the already-configured global provider.
    """

    global _provider_configured

    with _setup_lock:
        if not _provider_configured:
            provider = _build_provider(service_name)
            trace.set_tracer_provider(provider)
            _provider_configured = True

    return trace.get_tracer(service_name)
