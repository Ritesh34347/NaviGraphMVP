"""Settings for the OPA (Open Policy Agent) client.

Mirrors `navigraph_federation.settings.FederationSettings`'s exact shape:
a single new field with a safe default, subclassing `NaviGraphSettings` so
`OpaSettings()` never crashes even with a completely empty environment.

`opa_url` defaults to `"http://opa:8181"`, matching the Docker Compose
service name `infra/docker-compose.yml` gives the OPA container (`opa`,
port `8181` inside the `navigraph-net` network) -- the same in-network
DNS-name-default convention already used by `FederationSettings.trino_host`,
`Neo4jClient`'s default `bolt://neo4j:7687`, and `_redis_url()`'s
`redis://redis:6379` default.
"""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings


class OpaSettings(NaviGraphSettings):
    """Connection settings for `HttpOpaClient`."""

    opa_url: str = "http://opa:8181"
