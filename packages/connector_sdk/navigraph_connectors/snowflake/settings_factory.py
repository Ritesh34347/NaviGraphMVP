"""Resolves a real, per-`DataSource` `SnowflakeSettings` from that
`DataSource`'s own `connection_ref` plus an injected `SecretsProvider`.

Registered as this connector's `settings_factory` in
`navigraph_connectors.snowflake.__init__`. See
`navigraph_connectors.registry`'s module docstring for why this exists
(LIMITATIONS.md item 21): every field is passed explicitly to
`SnowflakeSettings(...)` so none of them silently fall back to
`SnowflakeSettings`'s own global-env-var reading (a `pydantic-settings`
`BaseSettings` subclass reads `SNOWFLAKE_ACCOUNT`-style env vars for any
field NOT given explicitly) -- passing every field, even when a resolved
value is empty, is what actually isolates two `DataSource` rows of
`source_type="snowflake"` from each other.

`connection_ref` shape this factory expects: `{"secret_scope": "<a string
identifying this DataSource's own real credential set>"}` -- e.g.
`{"secret_scope": "navikenz_poc_snowflake"}`. A `connection_ref` without a
`secret_scope` never reaches this factory -- callers resolve
`navigraph_connectors.registry.get_settings_factory` only when
`secret_scope` is present, falling back to `SnowflakeConnector()`'s
original global-env-var construction otherwise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from navigraph_connectors.snowflake.settings import SnowflakeSettings

if TYPE_CHECKING:
    from navigraph_shared.secrets import SecretsProvider


def build_snowflake_settings(
    connection_ref: dict[str, Any], secrets: SecretsProvider
) -> SnowflakeSettings:
    """Build this `DataSource`'s real `SnowflakeSettings` from its own
    `connection_ref` + `secrets`.

    Raises:
        ValueError: if `connection_ref` doesn't carry a `secret_scope`.
    """

    try:
        scope = connection_ref["secret_scope"]
    except KeyError as exc:
        raise ValueError(
            "connection_ref must include a 'secret_scope' key for real "
            "per-DataSource Snowflake credential resolution -- see "
            "navigraph_shared.secrets.SecretsProvider"
        ) from exc

    def field(name: str, default: str = "") -> str:
        return secrets.get(scope=scope, field=name) or default

    return SnowflakeSettings(
        snowflake_account=field("account"),
        snowflake_user=field("user"),
        snowflake_warehouse=field("warehouse"),
        snowflake_database=field("database"),
        snowflake_role=field("role"),
        snowflake_auth_method=field("auth_method", "password"),  # type: ignore[arg-type]
        snowflake_password=field("password"),
        snowflake_private_key_path=field("private_key_path"),
        snowflake_private_key_passphrase=field("private_key_passphrase"),
    )
