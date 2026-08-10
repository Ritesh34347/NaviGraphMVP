"""Unit tests for `build_snowflake_settings` (LIMITATIONS.md item 21)."""

from __future__ import annotations

import pytest
from navigraph_connectors.snowflake.settings_factory import build_snowflake_settings
from navigraph_shared.secrets import FakeSecretsProvider


def test_builds_settings_from_secret_scope() -> None:
    secrets = FakeSecretsProvider(
        {
            ("tenant_a_snowflake", "account"): "acct-a",
            ("tenant_a_snowflake", "user"): "user-a",
            ("tenant_a_snowflake", "warehouse"): "wh-a",
            ("tenant_a_snowflake", "database"): "db-a",
            ("tenant_a_snowflake", "password"): "hunter2",
        }
    )

    settings = build_snowflake_settings({"secret_scope": "tenant_a_snowflake"}, secrets)

    assert settings.snowflake_account == "acct-a"
    assert settings.snowflake_user == "user-a"
    assert settings.snowflake_warehouse == "wh-a"
    assert settings.snowflake_database == "db-a"
    assert settings.snowflake_password == "hunter2"
    assert settings.snowflake_auth_method == "password"


def test_two_scopes_resolve_to_distinct_settings() -> None:
    """The real point of item 21: two DataSources of the same source_type
    must resolve to genuinely distinct credentials."""

    secrets = FakeSecretsProvider(
        {
            ("tenant_a_snowflake", "account"): "acct-a",
            ("tenant_b_snowflake", "account"): "acct-b",
        }
    )

    settings_a = build_snowflake_settings({"secret_scope": "tenant_a_snowflake"}, secrets)
    settings_b = build_snowflake_settings({"secret_scope": "tenant_b_snowflake"}, secrets)

    assert settings_a.snowflake_account == "acct-a"
    assert settings_b.snowflake_account == "acct-b"


def test_missing_secret_scope_raises() -> None:
    with pytest.raises(ValueError, match="secret_scope"):
        build_snowflake_settings({}, FakeSecretsProvider())
