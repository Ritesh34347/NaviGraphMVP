"""Unit tests for the `SecretsProvider` triad.

`EnvVarSecretsProvider` is tested against the real `os.environ` (via
`monkeypatch`, never a real network call). `AzureKeyVaultSecretsProvider` is
tested with `unittest.mock.patch` against `azure.keyvault.secrets.SecretClient`
and `azure.identity.DefaultAzureCredential` -- there is no real-in-process-
fake-transport equivalent for the Azure SDK the way `httpx.MockTransport`
lets `HttpOpaClient`'s tests exercise real request/response parsing, so this
mirrors `test_snowflake_connector.py`'s `unittest.mock.patch` convention for
a real third-party driver instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navigraph_shared.secrets.client import (
    AzureKeyVaultSecretsProvider,
    EnvVarSecretsProvider,
    FakeSecretsProvider,
)


def test_env_var_provider_builds_scope_field_env_var_name(monkeypatch) -> None:
    monkeypatch.setenv("NAVIKENZ_POC_SNOWFLAKE_ACCOUNT", "acct-1")
    provider = EnvVarSecretsProvider()

    assert provider.get(scope="navikenz_poc_snowflake", field="account") == "acct-1"


def test_env_var_provider_returns_none_for_unset_field(monkeypatch) -> None:
    monkeypatch.delenv("NAVIKENZ_POC_SNOWFLAKE_ROLE", raising=False)
    provider = EnvVarSecretsProvider()

    assert provider.get(scope="navikenz_poc_snowflake", field="role") is None


def test_env_var_provider_scopes_are_independent(monkeypatch) -> None:
    """The real point of item 21/this module: two DataSources of the same
    source_type must resolve to genuinely distinct credentials, not one
    shared global env var."""

    monkeypatch.setenv("TENANT_A_SNOWFLAKE_ACCOUNT", "acct-a")
    monkeypatch.setenv("TENANT_B_SNOWFLAKE_ACCOUNT", "acct-b")
    provider = EnvVarSecretsProvider()

    assert provider.get(scope="tenant_a_snowflake", field="account") == "acct-a"
    assert provider.get(scope="tenant_b_snowflake", field="account") == "acct-b"


def test_fake_provider_returns_configured_values_and_records_calls() -> None:
    provider = FakeSecretsProvider({("scope-1", "password"): "hunter2"})

    assert provider.get(scope="scope-1", field="password") == "hunter2"
    assert provider.get(scope="scope-1", field="missing") is None
    assert provider.calls == [
        {"scope": "scope-1", "field": "password"},
        {"scope": "scope-1", "field": "missing"},
    ]


def test_fake_provider_set_builds_up_values() -> None:
    provider = FakeSecretsProvider()
    provider.set("scope-1", "user", "alice")

    assert provider.get(scope="scope-1", field="user") == "alice"


def test_fake_provider_raises_configured_exception() -> None:
    provider = FakeSecretsProvider(raise_exc=RuntimeError("vault unreachable"))

    with pytest.raises(RuntimeError, match="vault unreachable"):
        provider.get(scope="scope-1", field="password")


def test_azure_key_vault_provider_builds_hyphenated_secret_name_and_returns_value() -> None:
    mock_secret = MagicMock()
    mock_secret.value = "hunter2"
    mock_client = MagicMock()
    mock_client.get_secret.return_value = mock_secret

    with (
        patch("azure.keyvault.secrets.SecretClient", return_value=mock_client) as mock_ctor,
        patch("azure.identity.DefaultAzureCredential", return_value=MagicMock()),
    ):
        provider = AzureKeyVaultSecretsProvider("https://navigraph-dev-kv.vault.azure.net")
        result = provider.get(scope="navikenz_poc_snowflake", field="private_key_passphrase")

    mock_ctor.assert_called_once()
    assert mock_ctor.call_args.kwargs["vault_url"] == "https://navigraph-dev-kv.vault.azure.net"
    mock_client.get_secret.assert_called_once_with(
        "navikenz-poc-snowflake-private-key-passphrase"
    )
    assert result == "hunter2"


def test_azure_key_vault_provider_returns_none_when_secret_not_found() -> None:
    from azure.core.exceptions import ResourceNotFoundError

    mock_client = MagicMock()
    mock_client.get_secret.side_effect = ResourceNotFoundError("not found")

    with (
        patch("azure.keyvault.secrets.SecretClient", return_value=mock_client),
        patch("azure.identity.DefaultAzureCredential", return_value=MagicMock()),
    ):
        provider = AzureKeyVaultSecretsProvider("https://navigraph-dev-kv.vault.azure.net")
        result = provider.get(scope="scope-1", field="missing")

    assert result is None


def test_azure_key_vault_provider_uses_injected_credential_not_default() -> None:
    mock_secret = MagicMock()
    mock_secret.value = "x"
    mock_client = MagicMock()
    mock_client.get_secret.return_value = mock_secret
    injected_credential = MagicMock()

    with (
        patch("azure.keyvault.secrets.SecretClient", return_value=mock_client) as mock_ctor,
        patch("azure.identity.DefaultAzureCredential") as mock_default_cred,
    ):
        provider = AzureKeyVaultSecretsProvider(
            "https://navigraph-dev-kv.vault.azure.net", credential=injected_credential
        )
        provider.get(scope="scope-1", field="field-1")

    mock_default_cred.assert_not_called()
    assert mock_ctor.call_args.kwargs["credential"] is injected_credential
