"""Settings for the Databricks connector.

Every field has a safe default (empty string) so that importing this
module and constructing `DatabricksSettings()` never crashes, even with a
completely empty environment -- matching the convention established by
`SnowflakeSettings`/`PostgresSettings`.

`databricks_catalog` is REQUIRED for a real crawl: Unity Catalog's
`information_schema` is catalog-scoped (`{catalog}.information_schema.*`),
unlike Postgres/Snowflake's single, database-wide `information_schema` --
there is no schema-less "default catalog" query this connector can fall
back to. `databricks_schema` is optional; when set, `introspect_schema`
crawls only that one schema within the catalog rather than the whole
catalog.
"""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings


class DatabricksSettings(NaviGraphSettings):
    """Connection settings for `DatabricksConnector`."""

    databricks_server_hostname: str = ""
    databricks_http_path: str = ""
    databricks_access_token: str = ""
    databricks_catalog: str = ""
    databricks_schema: str = ""
