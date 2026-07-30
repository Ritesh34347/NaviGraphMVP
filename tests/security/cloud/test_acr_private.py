"""Adversarial test: the real ACR (`terraform/modules/acr`'s
`admin_enabled = false`) actually rejects anonymous, credential-less pulls
-- proving the setting does what it claims against the real, live registry
rather than trusting the Terraform config alone.

Deliberately does NOT `docker logout` or otherwise touch any real
credentials this environment might already hold for this registry --
instead runs in a way that fails at the AUTHENTICATION step regardless of
whether the image tag it names actually exists, so this test doesn't
depend on knowing a specific real tag that was pushed by
`.github/workflows/cd-deploy.yml`.
"""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.cloud_integration

_ACR_LOGIN_SERVER = "navigraphdevacr.azurecr.io"


def test_anonymous_pull_is_rejected() -> None:
    # A real, credential-less HTTP call to the registry's own v2 catalog
    # API -- ACR's standard anonymous-pull-disabled response is a 401 with
    # a WWW-Authenticate challenge, not a silent 200.
    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"https://{_ACR_LOGIN_SERVER}/v2/"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    status_code = result.stdout.strip()
    assert status_code == "401", (
        f"anonymous request to {_ACR_LOGIN_SERVER}/v2/ returned HTTP "
        f"{status_code!r}, expected 401 -- ACR's admin_enabled=false and "
        f"default anonymous-pull-disabled setting should reject this outright"
    )
