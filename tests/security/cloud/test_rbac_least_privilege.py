"""Adversarial test: documents (does not silently accept) the real,
already-known Kubernetes RBAC gap named in `terraform/modules/aks/main.tf`'s
own Phase 10 comment and `LIMITATIONS.md`.

`terraform/modules/aks` had no `azure_active_directory_role_based_access_control`
block until Phase 15.4 (LIMITATIONS.md item 51) -- the cluster used local
Kubernetes accounts, not AAD-integrated auth. Once any identity could fetch
a kubeconfig via `az aks get-credentials` at all (granted via the real
`Azure Kubernetes Service Cluster User Role` `azurerm_role_assignment`
Phase 10 added), it was effectively cluster-admin -- there was no
namespace-scoped Kubernetes RBAC layered on top.

UPDATE (Phase 15.4): real Terraform code for AAD-integrated K8s RBAC now
exists (`terraform/modules/aks`'s new `azure_active_directory_role_based
_access_control` block, `terraform/modules/aks-aad-groups`, and
`infra/k8s/base/rbac/`) but has DELIBERATELY NOT been applied to any live
cluster -- see `docs/runbooks/aad-k8s-rbac-rollout.md` for why (a real,
live-infrastructure change needs a human decision on real group
membership first) and for the exact steps to roll it out for real.

This test's identity is the CI/deploy service principal
(`ci_service_principal_object_id`), NOT a human accessing the cluster
through one of the new AAD groups -- that grant is separate, already-real,
and DELIBERATELY still broad (deployment automation genuinely needs to
manage cluster resources). Rolling out Phase 15.4's new AAD groups for
real does not change this test's expected result: it should keep
returning `yes` for the deploy identity even after real human RBAC is
applied and enforced. If a FUTURE phase also narrows the deploy
identity's own permissions, THAT is the signal to update this test and
`LIMITATIONS.md` -- not Phase 15.4 on its own.
"""

from __future__ import annotations

import pytest
from conftest import run_kubectl

pytestmark = pytest.mark.cloud_integration


def test_current_identity_has_effective_cluster_admin_a_known_documented_gap() -> None:
    result = run_kubectl(
        "auth", "can-i", "*", "*", "--all-namespaces", check=False,
    )
    assert result.stdout.strip() == "yes", (
        "the current kubectl identity does NOT have effective cluster-admin -- "
        "this is a SURPRISE: this test's identity is the CI/deploy service "
        "principal, whose own broad grant is unrelated to Phase 15.4's new "
        "AAD-integrated RBAC for human access (see this module's docstring). "
        "If the DEPLOY identity's own permissions have been narrowed, update "
        "this test AND LIMITATIONS.md's corresponding item to reflect the "
        "real, improved state -- do not just delete this test."
    )


def test_can_delete_secrets_across_the_whole_cluster_the_concrete_blast_radius(
) -> None:
    """The abstract "effectively cluster-admin" finding above, made
    concrete: the same identity that deploys navigraph can also delete any
    Secret in any namespace, including ones it has no legitimate reason to
    touch. This is the literal, real consequence of the documented gap, not
    a hypothetical."""

    result = run_kubectl(
        "auth", "can-i", "delete", "secrets", "--all-namespaces", check=False,
    )
    assert result.stdout.strip() == "yes", (
        "expected 'yes' (the documented, known-broad-permission state for "
        "the CI/deploy identity) -- if this now returns 'no', the deploy "
        "identity's OWN permissions have been narrowed (a separate change "
        "from Phase 15.4's human-facing AAD RBAC); update this test and "
        "LIMITATIONS.md to reflect it rather than treating this assertion "
        "failure as a real regression"
    )
