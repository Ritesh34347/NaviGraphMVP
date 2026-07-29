"""OPA client abstractions: provider-agnostic base + real HTTP client + fake test double."""

from navigraph_shared.opa.client import (
    FakeOpaClient,
    HttpOpaClient,
    OpaClient,
    OpaDecisionResponse,
)
from navigraph_shared.opa.settings import OpaSettings

__all__ = [
    "FakeOpaClient",
    "HttpOpaClient",
    "OpaClient",
    "OpaDecisionResponse",
    "OpaSettings",
]
