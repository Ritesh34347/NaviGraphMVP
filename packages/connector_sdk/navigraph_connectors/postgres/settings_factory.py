"""Resolves a real, per-`DataSource` `PostgresSettings` from that
`DataSource`'s own `connection_ref` plus an injected `SecretsProvider`.

Registered as this connector's `settings_factory` in
`navigraph_connectors.postgres.__init__`. See
`navigraph_connectors.snowflake.settings_factory`'s identical module
docstring for the full rationale (LIMITATIONS.md item 21) -- every field is
passed explicitly to `PostgresSettings(...)` so none of them silently fall
back to `PostgresSettings`'s own global `CUSTOMER_POSTGRES_*` env-var
reading.

`connection_ref` shape this factory expects: `{"secret_scope": "<a string
identifying this DataSource's own real credential set>"}`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from navigraph_connectors.postgres.settings import PostgresSettings

if TYPE_CHECKING:
    from navigraph_shared.secrets import SecretsProvider


def build_postgres_settings(
    connection_ref: dict[str, Any], secrets: "SecretsProvider"
) -> PostgresSettings:
    """Build this `DataSource`'s real `PostgresSettings` from its own
    `connection_ref` + `secrets`.

    Raises:
        ValueError: if `connection_ref` doesn't carry a `secret_scope`.
    """

    try:
        scope = connection_ref["secret_scope"]
    except KeyError as exc:
        raise ValueError(
            "connection_ref must include a 'secret_scope' key for real "
            "per-DataSource Postgres credential resolution -- see "
            "navigraph_shared.secrets.SecretsProvider"
        ) from exc

    def field(name: str, default: str = "") -> str:
        return secrets.get(scope=scope, field=name) or default

    port = field("port")

    return PostgresSettings(
        customer_postgres_host=field("host"),
        customer_postgres_port=int(port) if port else 5432,
        customer_postgres_database=field("database"),
        customer_postgres_user=field("user"),
        customer_postgres_password=field("password"),
        customer_postgres_sslmode=field("sslmode", "prefer"),
    )
