"""Adversarial test: documents (does not silently accept) the real,
already-known Kubernetes RBAC gap named in `terraform/modules/aks/main.tf`'s
own Phase 10 comment and `LIMITATIONS.md`.

`terraform/modules/aks` has no `azure_active_directory_role_based_access_control`
block -- the cluster uses local Kubernetes accounts, not AAD-integrated auth.
Once any identity can fetch a kubeconfig via `az aks get-credentials` at
all (granted via the real `Azure Kubernetes Service Cluster User Role`
`azurerm_role_assignment` this phase added), it is effectively
cluster-admin -- there is no namespace-scoped Kubernetes RBAC layered on
top. This test's job is to PROVE that real, current state rather than
assume it -- if a future phase adds real AAD-integrated RBAC, this test
should start failing, which is the correct signal to update it (and
`LIMITATIONS.md`) rather than a regression to silently work around.
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
        "this is a SURPRISE given the documented gap (no AAD-integrated K8s "
        "RBAC in terraform/modules/aks). If real namespace-scoped RBAC has "
        "been added, update this test AND LIMITATIONS.md's corresponding item "
        "to reflect the real, improved state -- do not just delete this test."
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
        "expected 'yes' (the documented, known-broad-permission state) -- if "
        "this now returns 'no', real least-privilege RBAC has landed; update "
        "this test and LIMITATIONS.md to reflect it rather than treating this "
        "assertion failure as a real regression"
    )
