"""Settings for the Postgres connector.

Every field has a safe default (empty string) so that importing this module
and constructing `PostgresSettings()` never crashes, even with a completely
empty environment -- matching the convention established by
`navigraph_shared.config.NaviGraphSettings` and
`navigraph_connectors.snowflake.settings.SnowflakeSettings`. Real values are
supplied via env vars (or a `.env` file) in every real deployment.

Field names map to env vars by uppercasing, exactly like
`SnowflakeSettings.snowflake_account` maps to `SNOWFLAKE_ACCOUNT` --
`customer_postgres_host` -> `CUSTOMER_POSTGRES_HOST`, and so on.

DELIBERATELY NOT `postgres_host`/`POSTGRES_HOST`, etc.: those exact names
are already claimed by `navigraph_catalog.settings.MetadataCatalogSettings`
for NaviGraph's OWN internal catalog/lineage Postgres database (see
`infra/docker-compose.yml`'s `postgres` service). Reusing them here would
silently point this connector -- a customer-facing data SOURCE, conceptually
identical to `SnowflakeSettings` -- at NaviGraph's own internal operational
database instead of a real customer Postgres instance. The `customer_`
prefix exists specifically to make that collision structurally impossible,
not for stylistic consistency.
"""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings


class PostgresSettings(NaviGraphSettings):
    """Connection settings for `PostgresConnector`."""

    customer_postgres_host: str = ""
    customer_postgres_port: int = 5432
    customer_postgres_database: str = ""
    customer_postgres_user: str = ""
    customer_postgres_password: str = ""
    # "prefer" (psycopg2/libpq's own default): use TLS if the server offers
    # it, fall back to plaintext otherwise. Real production deployments
    # against a customer's own Postgres should set this to "require" (or
    # stricter) once that customer's real TLS posture is known -- this
    # default is deliberately permissive for a first connector, not a
    # security recommendation.
    customer_postgres_sslmode: str = "prefer"
