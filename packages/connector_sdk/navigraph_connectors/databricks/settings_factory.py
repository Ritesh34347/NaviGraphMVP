"""Resolves a real, per-`DataSource` `DatabricksSettings` from that
`DataSource`'s own `connection_ref` plus an injected `SecretsProvider`.

Registered as this connector's `settings_factory` in
`navigraph_connectors.databricks.__init__`. See
`navigraph_connectors.snowflake.settings_factory`'s identical module
docstring for the full rationale (LIMITATIONS.md item 21) -- every field is
passed explicitly to `DatabricksSettings(...)` so none of them silently
fall back to `DatabricksSettings`'s own global `DATABRICKS_*` env-var
reading.

`connection_ref` shape this factory expects: `{"secret_scope": "<a string
identifying this DataSource's own real credential set>"}`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from navigraph_connectors.databricks.settings import DatabricksSettings

if TYPE_CHECKING:
    from navigraph_shared.secrets import SecretsProvider


def build_databricks_settings(
    connection_ref: dict[str, Any], secrets: SecretsProvider
) -> DatabricksSettings:
    """Build this `DataSource`'s real `DatabricksSettings` from its own
    `connection_ref` + `secrets`.

    Raises:
        ValueError: if `connection_ref` doesn't carry a `secret_scope`.
    """

    try:
        scope = connection_ref["secret_scope"]
    except KeyError as exc:
        raise ValueError(
            "connection_ref must include a 'secret_scope' key for real "
            "per-DataSource Databricks credential resolution -- see "
            "navigraph_shared.secrets.SecretsProvider"
        ) from exc

    def field(name: str, default: str = "") -> str:
        return secrets.get(scope=scope, field=name) or default

    return DatabricksSettings(
        databricks_server_hostname=field("server_hostname"),
        databricks_http_path=field("http_path"),
        databricks_access_token=field("access_token"),
        databricks_catalog=field("catalog"),
        databricks_schema=field("schema"),
    )
