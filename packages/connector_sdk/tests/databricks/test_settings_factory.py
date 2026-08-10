"""Unit tests for `build_databricks_settings` (LIMITATIONS.md item 21)."""

from __future__ import annotations

import pytest
from navigraph_connectors.databricks.settings_factory import build_databricks_settings
from navigraph_shared.secrets import FakeSecretsProvider


def test_builds_settings_from_secret_scope() -> None:
    secrets = FakeSecretsProvider(
        {
            ("tenant_a_databricks", "server_hostname"): "adb-123.azuredatabricks.net",
            ("tenant_a_databricks", "http_path"): "/sql/1.0/warehouses/abc",
            ("tenant_a_databricks", "access_token"): "dapi-token",
            ("tenant_a_databricks", "catalog"): "main",
            ("tenant_a_databricks", "schema"): "analytics",
        }
    )

    settings = build_databricks_settings({"secret_scope": "tenant_a_databricks"}, secrets)

    assert settings.databricks_server_hostname == "adb-123.azuredatabricks.net"
    assert settings.databricks_http_path == "/sql/1.0/warehouses/abc"
    assert settings.databricks_access_token == "dapi-token"
    assert settings.databricks_catalog == "main"
    assert settings.databricks_schema == "analytics"


def test_two_scopes_resolve_to_distinct_settings() -> None:
    secrets = FakeSecretsProvider(
        {
            ("tenant_a_databricks", "catalog"): "catalog-a",
            ("tenant_b_databricks", "catalog"): "catalog-b",
        }
    )

    settings_a = build_databricks_settings({"secret_scope": "tenant_a_databricks"}, secrets)
    settings_b = build_databricks_settings({"secret_scope": "tenant_b_databricks"}, secrets)

    assert settings_a.databricks_catalog == "catalog-a"
    assert settings_b.databricks_catalog == "catalog-b"


def test_missing_secret_scope_raises() -> None:
    with pytest.raises(ValueError, match="secret_scope"):
        build_databricks_settings({}, FakeSecretsProvider())
