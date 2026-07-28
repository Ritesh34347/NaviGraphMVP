"""Structured JSON logging for NaviGraph services.

Uses stdlib `logging` with a JSON formatter. `trace_id` and `tenant_id` are
injected onto every log record via a `logging.Filter` that reads from
`contextvars`, so any code path can set the current request's identity once
(via `bind_request_context`) and every log line emitted from that point on
(across `await` boundaries, unlike thread-locals) automatically carries it --
without threading `trace_id`/`tenant_id` through every function signature.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from typing import Any

_trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "navigraph_trace_id", default="-"
)
_tenant_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "navigraph_tenant_id", default="-"
)


def bind_request_context(*, trace_id: str, tenant_id: str) -> None:
    """Set the trace_id/tenant_id that will be attached to subsequent log records
    emitted on this async task / thread."""

    _trace_id_var.set(trace_id)
    _tenant_id_var.set(tenant_id)


class RequestContextFilter(logging.Filter):
    """Injects `trace_id` and `tenant_id` onto every `LogRecord`."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = _trace_id_var.get()
        record.tenant_id = _tenant_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """Renders each `LogRecord` as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", "-"),
            "tenant_id": getattr(record, "tenant_id", "-"),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(service_name: str, *, level: int = logging.INFO) -> logging.Logger:
    """Configure the root logger for `service_name` with JSON output to stdout.

    Idempotent-ish: safe to call more than once (e.g. once at app import time
    and once in tests) -- it clears and re-adds handlers rather than
    stacking duplicate handlers.
    """

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RequestContextFilter())
    root.addHandler(handler)

    return logging.getLogger(service_name)
