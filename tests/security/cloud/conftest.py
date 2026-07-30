"""Local marker registration for this directory, plus shared subprocess
helpers every test here uses to talk to the real, live AKS cluster.

These tests need a real, already-`kubectl`-authenticated AKS context (via
`az aks get-credentials`, done once by whatever calls pytest here -- see
`.github/workflows/cloud-security-tests.yml` and
`docs/runbooks/k8s-local-validation.md`) -- there is no Python Kubernetes
client dependency in this project; every test shells out to the real
`kubectl`/`az`/`docker` CLIs, exactly matching this codebase's existing
"real command output over a mocked client library" preference for
infrastructure-level checks (see `tools/scripts/tag_pii_columns.py`'s own
direct-API style for the Python-level equivalent).
"""

from __future__ import annotations

import json
import subprocess

import pytest

NAMESPACE = "navigraph"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "cloud_integration: tests that require a real, already-authenticated "
        "AKS kubectl context (via `az aks get-credentials`) -- only runnable "
        "after Phase 10b's real terraform apply. Not skipped gracefully, same "
        "reasoning as this project's other *_integration markers: these ARE "
        "the real adversarial proof the deployed cloud environment behaves as "
        "designed, not an optional extra.",
    )


def run_kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a real `kubectl` command against the current context, returning
    the completed process (stdout/stderr captured as text)."""

    return subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        timeout=60,
        check=check,
    )


def run_kubectl_json(*args: str) -> object:
    """Run a real `kubectl ... -o json` command and parse the result."""

    result = run_kubectl(*args, "-o", "json")
    return json.loads(result.stdout)
