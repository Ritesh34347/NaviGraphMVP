"""Settings for the Trino federation client.

Every field has a safe default so that importing this module and
constructing `FederationSettings()` never crashes, even with a completely
empty environment -- matching the convention established by
`navigraph_shared.config.NaviGraphSettings`. Real values are supplied via
env vars (or a `.env` file) in every real deployment.

Field names map to env vars by uppercasing, exactly like
`NaviGraphSettings.anthropic_api_key` maps to `ANTHROPIC_API_KEY`:
`trino_host` -> `TRINO_HOST`, `trino_port` -> `TRINO_PORT`, `trino_user` ->
`TRINO_USER`, `trino_catalog` -> `TRINO_CATALOG`.

Defaults, and where each comes from:

- `trino_host` defaults to `"trino-coordinator"`, matching the Docker
  Compose service name `infra/docker-compose.yml` gives the coordinator
  container (`trino-coordinator`, port `8080` inside the `navigraph-net`
  network) -- the hostname every other in-network service (this client
  included, once it runs inside `agent-runtime`) reaches it by.
- `trino_port` defaults to `8080`, matching that same compose file's
  `trino-coordinator` service port.
- `trino_user` defaults to `"navigraph"`. Trino's DB-API always requires a
  `user` value even when no real authentication is configured (dev/local
  clusters commonly run with trust/no-auth, but the protocol still records
  a user identity for every query, e.g. for `SELECT * FROM
  system.runtime.queries`) -- `"navigraph"` is a real, sensible service
  identity for this project rather than a placeholder like `"user"`.
- `trino_catalog` defaults to `"snowflake"`. This assumes
  `infra/trino/coordinator/catalog/snowflake.properties.example`'s eventual
  real filename (`snowflake.properties`) is the catalog name Trino will
  register Snowflake under -- Trino names a catalog after its properties
  file's basename, so `snowflake.properties` becomes catalog `snowflake`.
  This is a documented ASSUMPTION, not yet confirmed against a real,
  non-`.example` catalog file (none exists yet -- see that file's own
  comment and `DECISIONS.md`'s 2026-07-28 Trino entry): if the real file is
  ever named differently, this default must be updated to match.
"""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings


class FederationSettings(NaviGraphSettings):
    """Connection settings for `TrinoClient`."""

    trino_host: str = "trino-coordinator"
    trino_port: int = 8080
    trino_user: str = "navigraph"
    trino_catalog: str = "snowflake"
