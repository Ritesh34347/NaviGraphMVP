"""Secrets provider abstractions: provider-agnostic base + real env-var and
real Azure Key Vault clients + a fake test double."""

from navigraph_shared.secrets.client import (
    AzureKeyVaultSecretsProvider,
    EnvVarSecretsProvider,
    FakeSecretsProvider,
    SecretsProvider,
)

__all__ = [
    "AzureKeyVaultSecretsProvider",
    "EnvVarSecretsProvider",
    "FakeSecretsProvider",
    "SecretsProvider",
]
