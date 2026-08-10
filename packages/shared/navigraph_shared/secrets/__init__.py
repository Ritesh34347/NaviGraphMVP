"""Secrets provider abstractions: provider-agnostic base + real env-var/Key
Vault clients + a fake test double."""

from navigraph_shared.secrets.client import (
    AzureKeyVaultSecretsProvider,
    EnvVarSecretsProvider,
    FakeSecretsProvider,
    SecretsProvider,
)
from navigraph_shared.secrets.scoping import build_secret_scope

__all__ = [
    "AzureKeyVaultSecretsProvider",
    "EnvVarSecretsProvider",
    "FakeSecretsProvider",
    "SecretsProvider",
    "build_secret_scope",
]
