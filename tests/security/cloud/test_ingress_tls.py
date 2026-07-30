"""Adversarial test: the real public hostnames serve valid HTTPS and
redirect plain HTTP -- proving cert-manager + Let's Encrypt (installed once
during cluster bootstrap, see `docs/runbooks/k8s-local-validation.md`) are
actually issuing and terminating real certificates, not just configured on
paper.

Requires the real domain (`NAVIGRAPH_DOMAIN` env var, matching
`.github/workflows/cd-deploy.yml`'s `vars.NAVIGRAPH_DOMAIN`) to be set --
this cannot run against the `overlays/kind` local cluster at all (no real
domain, no Let's Encrypt HTTP01 challenge is possible against `.example.com`/
`.local` hostnames), only against the real, DNS-resolvable `dev`
deployment.
"""

from __future__ import annotations

import os
import subprocess

import pytest

pytestmark = pytest.mark.cloud_integration


def _require_domain() -> str:
    domain = os.environ.get("NAVIGRAPH_DOMAIN")
    if not domain:
        pytest.skip("NAVIGRAPH_DOMAIN not set -- this test needs the real, deployed domain")
    return domain


@pytest.mark.parametrize("subdomain", ["api", "app"])
def test_https_serves_a_real_valid_certificate(subdomain: str) -> None:
    domain = _require_domain()
    host = f"{subdomain}.navigraph.{domain}"

    result = subprocess.run(
        ["curl", "-sf", "-o", "/dev/null", f"https://{host}/"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, (
        f"https://{host}/ failed real TLS validation (curl without -k) -- "
        f"cert-manager/Let's Encrypt may not have issued a valid cert yet, "
        f"or the ingress TLS config is wrong: {result.stderr}"
    )


@pytest.mark.parametrize("subdomain", ["api", "app"])
def test_plain_http_is_redirected_to_https(subdomain: str) -> None:
    domain = _require_domain()
    host = f"{subdomain}.navigraph.{domain}"

    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://{host}/"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    status_code = result.stdout.strip()
    assert status_code in ("301", "308"), (
        f"http://{host}/ returned HTTP {status_code!r} instead of a real "
        f"redirect (301/308) to HTTPS -- ingress-nginx's ssl-redirect must "
        f"be enabled for the real dev domain (it's deliberately disabled "
        f"for overlays/kind's plain-HTTP local testing, see "
        f"overlays/dev/ingress-patch.yaml)"
    )
