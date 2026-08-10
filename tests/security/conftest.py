"""Local marker registration for this adversarial test directory.

Registered here (rather than a repo-root pytest config, which does not
exist) so `pytest tests/security` doesn't emit an "unknown marker" warning
-- mirrors `tests/integration/query_pipeline/conftest.py`'s pattern
exactly.
"""

from __future__ import annotations

import asyncio

import pytest
from navigraph_semantic_model.contracts import (
    Entity,
    EntityBinding,
    PolicyBindings,
    SemanticModel,
)
from navigraph_semantic_model.opa_sync import sync_policy_bindings
from navigraph_shared.opa import HttpOpaClient, OpaSettings

# Every `opa_integration`-marked test in this directory runs its real
# input against tenant "tenant-a" as the one legitimate/control tenant
# (other tenant IDs only ever appear as deliberate MISMATCH targets, which
# must be denied regardless of whether they have a synced document at
# all). Kept as one constant so the fixture below and every test file stay
# in sync by construction, not by four independently hand-typed strings.
_OPA_INTEGRATION_TEST_TENANT_ID = "tenant-a"


@pytest.fixture(scope="module", autouse=True)
def _sync_opa_integration_test_tenant_policy_bindings(request: pytest.FixtureRequest) -> None:
    """Push `tenant-a`'s real OPA `policy_bindings` document once per test
    module, before any `opa_integration`-marked test in it runs.

    Phase 3 of the configurable-platform build plan made `authz.rego`'s
    `allowed_roles` read `data.navigraph.tenants[tenant_id].allowed_roles`
    instead of a static literal, resolving to an EMPTY set (fail-closed)
    for any tenant with no synced document -- without this fixture, every
    real test below would fail against that empty default, not because
    the policy or the test is wrong, but because nothing ever synced
    `tenant-a`'s document in the first place.

    Built from a real, in-memory `SemanticModel` and pushed via the real
    `sync_policy_bindings` -- never a hand-authored data.json -- using the
    exact same default `allowed_roles` (`analyst`/`pii_viewer`/`admin`)
    the OLD static Rego literal granted every tenant, so this suite's
    real-policy assertions are about the SAME roles as before, only now
    sourced from a real per-tenant document. Only runs for modules
    actually carrying `pytestmark = pytest.mark.opa_integration` (checked
    via the module's own marker, not by module scope alone) --
    `test_pii_exposure_denied.py`'s `postgres_integration` tests never
    touch OPA at all and must not pay for or depend on this call. Not
    skipped gracefully if OPA is unreachable, same as every other
    OPA-dependent fixture in this suite -- these tests are meant to run
    against the real docker-compose stack, including in CI.
    """

    if request.node.get_closest_marker("opa_integration") is None:
        return

    model = SemanticModel(
        tenant_id=_OPA_INTEGRATION_TEST_TENANT_ID,
        version=1,
        entities=[
            Entity(
                name="Transaction",
                bindings=[
                    EntityBinding(
                        data_source="test-fixture",
                        table="S.STAGING_TRANSACTIONS",
                        key="MARKETID",
                    )
                ],
            )
        ],
        policy_bindings=PolicyBindings(allowed_roles=["analyst", "pii_viewer", "admin"]),
    )
    asyncio.run(sync_policy_bindings(HttpOpaClient(OpaSettings()), model))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "postgres_integration: tests that require a real, reachable Postgres "
        "(the docker-compose `postgres` service). Not skipped gracefully -- "
        "this tier is documented as running against the actual docker-compose "
        "stack in a separate CI job, so a hard dependency on Postgres being up "
        "is expected and correct here.",
    )
    config.addinivalue_line(
        "markers",
        "opa_integration: tests that require a real, reachable OPA (the "
        "docker-compose `opa` service) running the real, non-allow-all "
        "authz.rego policy. Not skipped gracefully, same reasoning as "
        "postgres_integration above -- these tests ARE the real adversarial "
        "proof this policy denies correctly, not an optional extra.",
    )
