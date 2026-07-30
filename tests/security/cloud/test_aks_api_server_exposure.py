"""Adversarial test: the real AKS API server is exposed publicly by design
in this `dev` environment (`terraform/modules/aks` has no
`private_cluster_enabled`/`api_server_access_profile.authorized_ip_ranges`
-- a real, logged, deliberate scope choice, not an oversight -- see
`LIMITATIONS.md`), but it must still require real authentication --
reachable is fine, unauthenticated access is not.

This test does NOT assert the API server is unreachable (it's public by
design in `dev`); it asserts an unauthenticated request gets a real `401`,
proving TLS + auth are both actually enforced rather than assuming a
public endpoint is automatically a security problem.
"""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.cloud_integration


def _api_server_url() -> str:
    result = subprocess.run(
        ["kubectl", "config", "view", "--minify", "-o", "jsonpath={.clusters[0].cluster.server}"],
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    server = result.stdout.strip()
    assert server, "kubectl config view returned no current cluster server URL"
    return server


def test_unauthenticated_request_to_the_api_server_gets_a_real_401() -> None:
    server = _api_server_url()

    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-k", f"{server}/api/v1/namespaces"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    status_code = result.stdout.strip()
    assert status_code == "401", (
        f"unauthenticated request to the AKS API server returned HTTP "
        f"{status_code!r}, expected 401 -- either the API server is not "
        f"actually reachable (contradicting the documented public-by-design "
        f"scope, worth re-confirming) or, more seriously, it's accepting "
        f"unauthenticated requests"
    )
