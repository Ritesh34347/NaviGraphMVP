"""Deriving a `SecretsProvider` scope string from a `DataSource` identity.

`DataSource` already enforces `UniqueConstraint("tenant_id", "name")` at the
database level (see `navigraph_catalog.models`), so reusing that exact pair
as the secret scope key means two tenants -- or two data sources for one
tenant -- can never collide on the same scope without also violating a
constraint the catalog already guarantees. This module exists so every
caller (the self-service registration route today, any future re-key/
rotation tooling later) builds the scope string identically instead of
hand-formatting it inline in more than one place.
"""

from __future__ import annotations

import re

_UNSAFE_CHARS = re.compile(r"[^a-z0-9_-]+")


def build_secret_scope(*, tenant_id: str, data_source_name: str) -> str:
    """Build a `SecretsProvider` scope string for one tenant's data source.

    Lowercases both inputs and replaces any character outside
    `[a-z0-9_-]` with `_`, then joins them with a double underscore (chosen
    over a single underscore so a `tenant_id` or `data_source_name` that
    itself contains an underscore can't accidentally produce a scope string
    that collides with a different (tenant_id, name) pair -- e.g. without
    the double-underscore separator, `tenant="a_b", name="c"` and
    `tenant="a", name="b_c"` would both build the scope `a_b_c`).

    This only needs to guarantee global uniqueness of the (tenant_id, name)
    pair -- not Key Vault-legality directly, since
    `AzureKeyVaultSecretsProvider.get`/`.set` already replace `_` with `-`
    before calling Key Vault.
    """
    safe_tenant = _UNSAFE_CHARS.sub("_", tenant_id.lower()).strip("_")
    safe_name = _UNSAFE_CHARS.sub("_", data_source_name.lower()).strip("_")
    return f"{safe_tenant}__{safe_name}"
