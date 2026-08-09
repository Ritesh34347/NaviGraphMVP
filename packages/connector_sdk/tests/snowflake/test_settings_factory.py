"""Unit tests for `build_snowflake_settings` (LIMITATIONS.md item 21)."""

from __future__ import annotations

import pytest

from navigraph_connectors.snowflake.settings_factory import build_snowflake_settings
from navigraph_shared.secrets import FakeSecretsProvider


def test_resolves_all_fields_from_the_scoped_secrets_provider() -> None:
    secrets = FakeSecretsProvider(
        {
            ("navikenz_poc_snowflake", "account"): "acct-1",
            ("navikenz_poc_snowflake", "user"): "user-1",
            ("navikenz_poc_snowflake", "warehouse"): "wh-1",
            ("navikenz_poc_snowflake", "database"): "db-1",
            ("navikenz_poc_snowflake", "role"): "role-1",
            ("navikenz_poc_snowflake", "auth_method"): "password",
            ("navikenz_poc_snowflake", "password"): "hunter2",
        }
    )

    settings = build_snowflake_settings(
        {"secret_scope": "navikenz_poc_snowflake"}, secrets
    )

    assert settings.snowflake_account == "acct-1"
    assert settings.snowflake_user == "user-1"
    assert settings.snowflake_warehouse == "wh-1"
    assert settings.snowflake_database == "db-1"
    assert settings.snowflake_role == "role-1"
    assert settings.snowflake_auth_method == "password"
    assert settings.snowflake_password == "hunter2"


def test_two_data_sources_of_the_same_source_type_get_distinct_settings(monkeypatch) -> None:
    """The real point: two Snowflake DataSource rows must never resolve to
    the same credentials, and must never fall back to a shared global env
    var either."""

    monkeypatch.setenv("SNOWFLAKE_ACCOUNT", "global-leaked-account")
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
    # Neither resolved DataSource's settings leak the unrelated global env var.
    assert "global-leaked-account" not in (settings_a.snowflake_account, settings_b.snowflake_account)


def test_missing_secret_scope_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="secret_scope"):
        build_snowflake_settings({}, FakeSecretsProvider())


def test_unset_optional_fields_default_to_empty_string() -> None:
    settings = build_snowflake_settings(
        {"secret_scope": "sparse_scope"}, FakeSecretsProvider()
    )

    assert settings.snowflake_warehouse == ""
    assert settings.snowflake_role == ""
    assert settings.snowflake_auth_method == "password"
