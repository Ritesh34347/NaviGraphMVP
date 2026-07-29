"""Settings for the Snowflake connector.

Every field has a safe default (empty string) so that importing this module
and constructing `SnowflakeSettings()` never crashes, even with a completely
empty environment -- matching the convention established by
`navigraph_shared.config.NaviGraphSettings`. Real values are supplied via
env vars (or a `.env` file) in every real deployment.

Field names map to env vars by uppercasing, exactly like
`NaviGraphSettings.anthropic_api_key` maps to `ANTHROPIC_API_KEY`:
`snowflake_account` -> `SNOWFLAKE_ACCOUNT`, `snowflake_user` ->
`SNOWFLAKE_USER`, and so on.
"""

from __future__ import annotations

from typing import Literal

from navigraph_shared.config import NaviGraphSettings


class SnowflakeSettings(NaviGraphSettings):
    """Connection settings for `SnowflakeConnector`."""

    snowflake_account: str = ""
    snowflake_user: str = ""
    snowflake_warehouse: str = ""
    snowflake_database: str = ""
    snowflake_role: str = ""
    snowflake_auth_method: Literal["password", "key_pair"] = "password"
    snowflake_password: str = ""
    snowflake_private_key_path: str = ""
    snowflake_private_key_passphrase: str = ""
