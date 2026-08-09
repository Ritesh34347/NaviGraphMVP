"""Unit tests for `build_postgres_settings` (LIMITATIONS.md item 21)."""

from __future__ import annotations

import pytest

from navigraph_connectors.postgres.settings_factory import build_postgres_settings
from navigraph_shared.secrets import FakeSecretsProvider


def test_resolves_all_fields_from_the_scoped_secrets_provider() -> None:
    secrets = FakeSecretsProvider(
        {
            ("acme_postgres", "host"): "db.acme.example.com",
            ("acme_postgres", "port"): "5433",
            ("acme_postgres", "database"): "sample",
            ("acme_postgres", "user"): "acme_user",
            ("acme_postgres", "password"): "hunter2",
            ("acme_postgres", "sslmode"): "require",
        }
    )

    settings = build_postgres_settings({"secret_scope": "acme_postgres"}, secrets)

    assert settings.customer_postgres_host == "db.acme.example.com"
    assert settings.customer_postgres_port == 5433
    assert settings.customer_postgres_database == "sample"
    assert settings.customer_postgres_user == "acme_user"
    assert settings.customer_postgres_password == "hunter2"
    assert settings.customer_postgres_sslmode == "require"


def test_two_data_sources_of_the_same_source_type_get_distinct_settings(monkeypatch) -> None:
    monkeypatch.setenv("CUSTOMER_POSTGRES_HOST", "global-leaked-host")
    secrets = FakeSecretsProvider(
        {
            ("tenant_a_postgres", "host"): "a.example.com",
            ("tenant_b_postgres", "host"): "b.example.com",
        }
    )

    settings_a = build_postgres_settings({"secret_scope": "tenant_a_postgres"}, secrets)
    settings_b = build_postgres_settings({"secret_scope": "tenant_b_postgres"}, secrets)

    assert settings_a.customer_postgres_host == "a.example.com"
    assert settings_b.customer_postgres_host == "b.example.com"
    assert "global-leaked-host" not in (
        settings_a.customer_postgres_host,
        settings_b.customer_postgres_host,
    )


def test_missing_secret_scope_raises_clear_error() -> None:
    with pytest.raises(ValueError, match="secret_scope"):
        build_postgres_settings({}, FakeSecretsProvider())


def test_unset_port_defaults_to_5432() -> None:
    settings = build_postgres_settings({"secret_scope": "sparse_scope"}, FakeSecretsProvider())

    assert settings.customer_postgres_port == 5432
    assert settings.customer_postgres_sslmode == "prefer"
