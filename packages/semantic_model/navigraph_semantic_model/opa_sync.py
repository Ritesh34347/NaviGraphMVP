"""Compile a `SemanticModel`'s `policy_bindings` into a real per-tenant
OPA data document, and push it via `OpaClient.set_data`.

`infra/opa/policies/authz.rego`'s `allowed_roles` rule reads
`data.navigraph.tenants[input.tenant_id].allowed_roles`, falling back to a
generic `default_allowed_roles` set when no document exists for a given
tenant -- see that policy file's own comment for the full design
rationale (LIMITATIONS.md item 38's structural fix, Phase 12.3). This
module is the one real place that writes to that path; the Rego policy
itself never needs a second, per-tenant copy.

This is a management/onboarding-time operation -- called once per tenant
whenever its Semantic Model is compiled/activated, never on the
per-request hot path `PolicyAuthorizationAgent` runs.
"""

from __future__ import annotations

from typing import Any

from navigraph_shared.opa import OpaClient

from navigraph_semantic_model.contracts import SemanticModel


def compile_policy_bindings_document(model: SemanticModel) -> dict[str, Any]:
    """Build the real OPA data document a `SemanticModel`'s
    `policy_bindings` compile to -- currently just `allowed_roles`, the
    one tenant-specific fact `authz.rego` reads from `data` (see that
    file's own comment for why everything else in it is policy LOGIC,
    not per-tenant data)."""

    return {"allowed_roles": list(model.policy_bindings.allowed_roles)}


async def sync_policy_bindings(opa_client: OpaClient, model: SemanticModel) -> None:
    """Push `model`'s compiled policy-bindings document to OPA's real Data
    API at `navigraph/tenants/<model.tenant_id>` -- overwrites whatever
    was previously stored there for this tenant in full."""

    await opa_client.set_data(
        path=f"navigraph/tenants/{model.tenant_id}",
        document=compile_policy_bindings_document(model),
    )
