"""Secrets provider abstraction.

`SecretsProvider` is the abstract base every per-`DataSource` credential
resolution codes against -- mirroring `navigraph_shared.llm.client`'s and
`navigraph_shared.opa.client`'s exact ABC/real/fake triad. Three concrete
implementations:

- `EnvVarSecretsProvider` -- a REAL implementation backed by process
  environment variables, scoped per lookup (not a single global prefix).
- `AzureKeyVaultSecretsProvider` -- a REAL implementation backed by a real
  Azure Key Vault, via `azure-identity`/`azure-keyvault-secrets`.
- `FakeSecretsProvider` -- a no-network test double that returns canned
  values (or raises, to simulate a backend outage) and records every call
  made to it, so unit tests can assert on exactly what was looked up
  without a real environment or a real Key Vault.

LIMITATIONS.md item 10 (`connection_ref` is not a real secrets-manager
integration) and item 21 (connector credential routing is global-env-var-
based, not per-`DataSource`) are both what this module exists to close --
see `navigraph_connectors.registry`'s settings-factory mechanism for how a
`DataSource.connection_ref` plus a `SecretsProvider` together resolve a
real, per-source `Settings` instance instead of every connector of a given
`source_type` sharing one process-wide credential set.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any


class SecretsProvider(ABC):
    """Abstract base for resolving one secret field within a scope.

    `scope` identifies which real credential set to resolve (e.g. a
    `DataSource`-specific string like `"navikenz_poc_snowflake"`); `field`
    identifies which piece of that credential set (e.g. `"account"`,
    `"password"`). Two different scopes must never resolve to the same
    underlying secret unless the backend genuinely stores them that way --
    this is the mechanism that makes credentials per-`DataSource` rather
    than per-`source_type`-globally.
    """

    @abstractmethod
    def get(self, *, scope: str, field: str) -> str | None:
        """Look up one secret field within `scope`.

        Returns `None` if the field is simply not set within an otherwise-
        reachable backend -- expected, not an error (e.g. an optional
        Snowflake field like `role` that many real accounts don't set).
        MAY raise if the backend itself is unreachable or errors (e.g. a
        real Key Vault authentication failure) -- that is a genuine,
        unusual failure worth propagating, not data to silently return as
        `None`, mirroring `navigraph_connectors.base.Connector
        .introspect_schema`/`execute_query`'s documented "MAY raise"
        contract (as opposed to `test_connection`'s "must never raise").
        """
        raise NotImplementedError

    @abstractmethod
    def set(self, *, scope: str, field: str, value: str) -> None:
        """Persist one secret field within `scope`.

        This is the write half `get()` never needed until self-service data
        source onboarding: a client entering their own credentials in a form
        has to land somewhere durable before `register_data_source()` can
        store a `connection_ref` pointing at it. Every concrete subclass
        must make an explicit, reviewed decision about what "write a
        secret" means for its backend -- there is deliberately no default
        no-op implementation here, because a silent no-op would let a
        provider that can't really persist anything (see
        `EnvVarSecretsProvider`) pretend to succeed and lose the client's
        credential on the next process restart.

        MUST raise (not return silently) if the write fails or the backend
        is unreachable -- a caller building a new `DataSource` on top of
        this call needs to know definitively that the secret never landed,
        rather than discovering it days later as an inexplicable "credential
        not found" on the first crawl.
        """
        raise NotImplementedError


class EnvVarSecretsProvider(SecretsProvider):
    """Real implementation backed by process environment variables.

    Builds `{SCOPE}_{FIELD}` (uppercased) as the env var name -- e.g.
    `scope="navikenz_poc_snowflake", field="account"` ->
    `NAVIKENZ_POC_SNOWFLAKE_ACCOUNT`. This generalizes the previous global
    pattern (a single `{"env_prefix": "SNOWFLAKE"}` shared by every
    connector of a given `source_type` in the whole process, so two
    `DataSource` rows of the same type could never hold distinct real
    credentials) into a genuinely per-scope one: two `DataSource` rows now
    read distinct env vars as long as their `connection_ref.secret_scope`
    values differ. Still fundamentally a local/dev-appropriate mechanism,
    not real secret storage with rotation/access-control -- see
    `AzureKeyVaultSecretsProvider` for that.
    """

    def get(self, *, scope: str, field: str) -> str | None:
        env_var = f"{scope}_{field}".upper()
        return os.environ.get(env_var) or None

    def set(self, *, scope: str, field: str, value: str) -> None:
        raise NotImplementedError(
            "EnvVarSecretsProvider is read-only: a process cannot durably "
            "rewrite its own future environment variables. Self-service "
            "credential writes require AzureKeyVaultSecretsProvider (or "
            "another real secrets-manager-backed provider) -- configure "
            "one before enabling data source self-service onboarding."
        )


class AzureKeyVaultSecretsProvider(SecretsProvider):
    """Real implementation backed by a real Azure Key Vault.

    Uses `azure-identity`'s `DefaultAzureCredential` by default -- the same
    credential chain the real AKS Secrets Store CSI driver setup already
    uses for the platform's own operational secrets (see
    `infra/k8s/overlays/dev/secretproviderclass-*.yaml`); this class is the
    application-level equivalent for secrets resolved at request-construction
    time rather than mounted as a volume at pod startup.

    Key Vault secret names allow only alphanumeric characters and hyphens
    (no underscores), so `scope`/`field` are joined with a hyphen and any
    underscore in either is itself replaced with a hyphen:
    `scope="navikenz_poc_snowflake", field="private_key_passphrase"` ->
    secret name `"navikenz-poc-snowflake-private-key-passphrase"`.

    `vault_url` is required at construction -- there is no single default
    vault shared across every tenant; callers resolve it from the specific
    `DataSource.connection_ref` being processed (see
    `navigraph_connectors`' settings factories), not from a global setting.

    When no explicit `credential` is passed, the default `DefaultAzureCredential()`
    is built with `managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID")`.
    This is a REAL, not hypothetical, requirement: the AKS node pool this
    runs on has multiple user-assigned managed identities attached, and a
    bare `DefaultAzureCredential()`'s `ManagedIdentityCredential` step
    cannot disambiguate between them via IMDS alone -- confirmed live via
    `ClientAuthenticationError: ... Multiple user assigned identities
    exist, please specify the clientId / resourceId`. `AZURE_CLIENT_ID` is
    `None` (i.e. unset) in single-identity environments, where passing
    `managed_identity_client_id=None` is a harmless no-op.
    """

    def __init__(self, vault_url: str, *, credential: Any | None = None) -> None:
        self._vault_url = vault_url
        self._credential = credential

    def _resolve_credential(self) -> Any:
        if self._credential is not None:
            return self._credential

        from azure.identity import DefaultAzureCredential

        return DefaultAzureCredential(managed_identity_client_id=os.environ.get("AZURE_CLIENT_ID"))

    def get(self, *, scope: str, field: str) -> str | None:
        # Imported lazily, mirroring SnowflakeConnector/PostgresConnector's
        # established lazy-driver-import convention -- importing this
        # module should never require azure-identity/azure-keyvault-secrets
        # to be installed unless this class is actually instantiated.
        from azure.core.exceptions import ResourceNotFoundError
        from azure.keyvault.secrets import SecretClient

        client = SecretClient(vault_url=self._vault_url, credential=self._resolve_credential())
        secret_name = f"{scope}-{field}".replace("_", "-")
        try:
            return client.get_secret(secret_name).value
        except ResourceNotFoundError:
            return None

    def set(self, *, scope: str, field: str, value: str) -> None:
        # Imported lazily -- see the identical note on `get()` above.
        from azure.keyvault.secrets import SecretClient

        client = SecretClient(vault_url=self._vault_url, credential=self._resolve_credential())
        # Must exactly match get()'s naming, or a value written via set()
        # becomes unreadable via get() -- there is no test for "these two
        # methods agree" other than construction being identical here.
        secret_name = f"{scope}-{field}".replace("_", "-")
        client.set_secret(secret_name, value)


class FakeSecretsProvider(SecretsProvider):
    """No-network test double for `SecretsProvider`.

    Construct with a `{(scope, field): value}` dict, or build one up via
    `.set(scope=..., field=..., value=...)`. Every call -- `get` and `set`
    alike -- is recorded in `self.calls` (mirroring `FakeOpaClient`'s
    identical call-recording convention) so tests can assert on exactly
    what was looked up or written. Pass `raise_exc` to simulate a backend
    outage on every `get`/`set` call.
    """

    def __init__(
        self,
        values: dict[tuple[str, str], str] | None = None,
        *,
        raise_exc: Exception | None = None,
    ) -> None:
        self._values = dict(values or {})
        self._raise_exc = raise_exc
        self.calls: list[dict[str, str]] = []

    def set(self, *, scope: str, field: str, value: str) -> None:
        self.calls.append({"op": "set", "scope": scope, "field": field})
        if self._raise_exc is not None:
            raise self._raise_exc
        self._values[(scope, field)] = value

    def get(self, *, scope: str, field: str) -> str | None:
        self.calls.append({"op": "get", "scope": scope, "field": field})
        if self._raise_exc is not None:
            raise self._raise_exc
        return self._values.get((scope, field))
