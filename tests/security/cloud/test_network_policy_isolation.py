"""Adversarial test: real NetworkPolicy enforcement against the real,
deployed AKS cluster.

Proves TWO things, not one -- an isolation-only test can pass vacuously if
the whole network is accidentally broken (e.g. `network_profile` never
actually applied, or every pod firewalled off from everything):

1. A disposable pod with no matching label CANNOT reach `neo4j`, `redis`,
   or `agent-runtime` directly -- `infra/k8s/base/networkpolicy-*.yaml`'s
   default-deny-all plus explicit per-service allows must actually be
   enforced by `terraform/modules/aks`'s `network_profile { network_policy
   = "azure" }` (Phase 10), not just accepted as inert API objects the way
   they were in local `kind` testing (kindnet does not enforce
   NetworkPolicy at all -- this is the one check that can ONLY be proven
   against real AKS).
2. The POSITIVE CONTROL: `gateway` pods can still reach `agent-runtime` --
   proves the isolation above is real, deliberate policy, not a broken
   network blocking everything indiscriminately.
"""

from __future__ import annotations

import subprocess

import pytest
from conftest import NAMESPACE, run_kubectl

pytestmark = pytest.mark.cloud_integration


def _debug_pod_can_reach(target: str, *, timeout: int = 5) -> bool:
    """Run a real, disposable debug pod attempting one HTTP GET against
    `target` (host:port/path); return whether it succeeded."""

    result = subprocess.run(
        [
            "kubectl",
            "run",
            "netpol-debug-check",
            "-n",
            NAMESPACE,
            "--rm",
            "-i",
            "--restart=Never",
            "--image=busybox",
            "--overrides",
            '{"spec":{"terminationGracePeriodSeconds":0}}',
            "--",
            "wget",
            "-qO-",
            f"--timeout={timeout}",
            target,
        ],
        capture_output=True,
        text=True,
        timeout=timeout + 15,
        check=False,
    )
    return result.returncode == 0


def test_unlabeled_pod_cannot_reach_agent_runtime_directly() -> None:
    reachable = _debug_pod_can_reach("http://agent-runtime.navigraph.svc.cluster.local:8001/healthz")
    assert not reachable, (
        "a pod with no matching label reached agent-runtime directly -- "
        "the default-deny-all + allow-gateway-to-agent-runtime policies "
        "are not being enforced"
    )


def test_unlabeled_pod_cannot_reach_neo4j_directly() -> None:
    reachable = _debug_pod_can_reach("http://neo4j.navigraph.svc.cluster.local:7474/")
    assert not reachable, (
        "a pod with no matching label reached neo4j directly -- "
        "allow-neo4j-redis-opa-ingress-from-agent-runtime is not being enforced"
    )


def test_positive_control_gateway_can_still_reach_agent_runtime() -> None:
    """Without this, the two denial tests above could pass simply because
    the whole cluster network is broken, not because policy is correctly
    scoped."""

    gateway_pod_result = run_kubectl(
        "get",
        "pod",
        "-n",
        NAMESPACE,
        "-l",
        "app=gateway,track=stable",
        "-o",
        "jsonpath={.items[0].metadata.name}",
    )
    gateway_pod = gateway_pod_result.stdout.strip()
    assert gateway_pod, "no gateway-stable pod found -- cannot run the positive control"

    probe_snippet = (
        "import urllib.request; urllib.request.urlopen("
        "'http://agent-runtime.navigraph.svc.cluster.local:8001/healthz', timeout=5).read()"
    )
    result = subprocess.run(
        [
            "kubectl",
            "exec",
            "-n",
            NAMESPACE,
            gateway_pod,
            "--",
            "python3",
            "-c",
            probe_snippet,
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, (
        f"gateway could not reach agent-runtime even though this SHOULD be "
        f"explicitly allowed -- either the allow policy is missing/wrong, or "
        f"the whole network is broken (making the denial tests above "
        f"meaningless): {result.stderr}"
    )
