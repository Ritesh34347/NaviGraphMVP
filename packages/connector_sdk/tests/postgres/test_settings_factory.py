"""Unit tests for `build_postgres_settings` (LIMITATIONS.md item 21)."""

from __future__ import annotations

import pytest
from navigraph_connectors.postgres.settings_factory import build_postgres_settings
from navigraph_shared.secrets import FakeSecretsProvider


def test_builds_settings_from_secret_scope() -> None:
    secrets = FakeSecretsProvider(
        {
            ("tenant_a_postgres", "host"): "db.tenant-a.internal",
            ("tenant_a_postgres", "port"): "5432",
            ("tenant_a_postgres", "database"): "analytics",
            ("tenant_a_postgres", "user"): "reader",
            ("tenant_a_postgres", "password"): "hunter2",
        }
    )

    settings = build_postgres_settings({"secret_scope": "tenant_a_postgres"}, secrets)

    assert settings.source_postgres_host == "db.tenant-a.internal"
    assert settings.source_postgres_port == 5432
    assert settings.source_postgres_database == "analytics"
    assert settings.source_postgres_user == "reader"
    assert settings.source_postgres_password == "hunter2"
    assert settings.source_postgres_sslmode == "prefer"


def test_missing_port_defaults_to_5432() -> None:
    settings = build_postgres_settings(
        {"secret_scope": "tenant_a_postgres"}, FakeSecretsProvider()
    )

    assert settings.source_postgres_port == 5432


def test_two_scopes_resolve_to_distinct_settings() -> None:
    secrets = FakeSecretsProvider(
        {
            ("tenant_a_postgres", "host"): "host-a",
            ("tenant_b_postgres", "host"): "host-b",
        }
    )

    settings_a = build_postgres_settings({"secret_scope": "tenant_a_postgres"}, secrets)
    settings_b = build_postgres_settings({"secret_scope": "tenant_b_postgres"}, secrets)

    assert settings_a.source_postgres_host == "host-a"
    assert settings_b.source_postgres_host == "host-b"


def test_missing_secret_scope_raises() -> None:
    with pytest.raises(ValueError, match="secret_scope"):
        build_postgres_settings({}, FakeSecretsProvider())
