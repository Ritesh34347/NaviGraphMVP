"""Adversarial test: each SecretProviderClass syncs only the secret names
it explicitly declares, and services that need no secrets get no secret
material mounted at all.

Unlike the original Phase 10 technical design's assumption (one shared
`navigraph-app-secrets` Secret name synced by every SecretProviderClass,
which would have made real per-service scoping impossible -- any pod could
read any other service's secrets from that one shared object), the actual
implementation gives each service its OWN Secret name
(`agent-runtime-secrets`, `neo4j-secrets`, `grafana-secrets`) via its own
`overlays/dev/secretproviderclass-*.yaml`, and `gateway`/`web` mount no CSI
volume at all since they need zero secret values. This test proves that
real design choice actually holds against the live cluster, not just that
it reads that way in the YAML.
"""

from __future__ import annotations

import subprocess

import pytest
from conftest import NAMESPACE, run_kubectl

pytestmark = pytest.mark.cloud_integration

_AGENT_RUNTIME_EXPECTED_KEYS = {
    "ANTHROPIC-API-KEY",
    "SNOWFLAKE-PASSWORD",
    "POSTGRES-PASSWORD",
    "NEO4J-PASSWORD",
}


def _list_mounted_secret_files(pod_name: str, mount_path: str) -> set[str] | None:
    """Real `kubectl exec ls` of the CSI-mounted secret directory. Returns
    None if the mount doesn't exist at all (a legitimate, expected state
    for services with no declared secrets)."""

    result = subprocess.run(
        ["kubectl", "exec", "-n", NAMESPACE, pod_name, "--", "ls", mount_path],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return None
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def test_agent_runtime_secret_mount_contains_exactly_its_declared_keys() -> None:
    pod_result = run_kubectl(
        "get", "pod", "-n", NAMESPACE, "-l", "app=agent-runtime",
        "-o", "jsonpath={.items[0].metadata.name}",
    )
    pod = pod_result.stdout.strip()
    assert pod, "no agent-runtime pod found"

    mounted = _list_mounted_secret_files(pod, "/mnt/secrets-store")
    assert mounted is not None, "agent-runtime has no CSI secret mount at all -- expected one"
    assert mounted == _AGENT_RUNTIME_EXPECTED_KEYS, (
        f"agent-runtime's secret mount contains {mounted}, expected exactly "
        f"{_AGENT_RUNTIME_EXPECTED_KEYS} -- either a real over-exposure (extra "
        f"keys present) or a real config drift (expected keys missing)"
    )


def test_gateway_has_no_secret_mount_at_all() -> None:
    """gateway needs zero secret values (it only talks to agent-runtime
    over plain HTTP) -- it should have no SecretProviderClass, no CSI
    volume, and nothing at /mnt/secrets-store."""

    pod_result = run_kubectl(
        "get", "pod", "-n", NAMESPACE, "-l", "app=gateway,track=stable",
        "-o", "jsonpath={.items[0].metadata.name}",
    )
    pod = pod_result.stdout.strip()
    assert pod, "no gateway-stable pod found"

    mounted = _list_mounted_secret_files(pod, "/mnt/secrets-store")
    assert mounted is None, (
        f"gateway has a /mnt/secrets-store mount containing {mounted} -- it "
        f"should have zero secret material, it never had a "
        f"SecretProviderClass or CSI volume declared for it"
    )


def test_neo4j_secret_mount_does_not_contain_other_services_secrets() -> None:
    pod_result = run_kubectl(
        "get", "pod", "-n", NAMESPACE, "-l", "app=neo4j",
        "-o", "jsonpath={.items[0].metadata.name}",
    )
    pod = pod_result.stdout.strip()
    assert pod, "no neo4j pod found"

    mounted = _list_mounted_secret_files(pod, "/mnt/secrets-store")
    assert mounted is not None, "neo4j has no CSI secret mount at all -- expected one"
    assert mounted == {"NEO4J-PASSWORD"}, (
        f"neo4j's secret mount contains {mounted}, expected exactly "
        f"{{'NEO4J-PASSWORD'}} -- it should never see ANTHROPIC-API-KEY, "
        f"SNOWFLAKE-PASSWORD, or POSTGRES-PASSWORD"
    )
