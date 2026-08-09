"""Adversarial + functional test: per-tenant OPA data documents (Phase 12.3).

Runs against the real, live `opa` docker-compose service and the real
`infra/opa/policies/authz.rego` policy -- not mocked, not a
`FakeOpaClient`. Proves, against real OPA state (a genuine
`PUT /v1/data/navigraph/tenants/<tenant_id>` followed by a real decision
query), that:

  1. A tenant with NO data document pushed still resolves via
     `default_allowed_roles` -- the fallback that keeps every existing
     tenant/test/eval run working unchanged.
  2. Pushing a real per-tenant `allowed_roles` document actually changes
     that tenant's real authorization outcome (both a role it newly
     allows and a role it newly denies).
  3. An empty `allowed_roles: []` is a real, deliberate lockout -- it must
     NOT be treated as "not configured" and silently fall back to
     defaults.
  4. Per-tenant data is genuinely isolated: pushing a document for one
     tenant never changes a DIFFERENT tenant's real decision.

A unique, randomly-suffixed `tenant_id` is used per test so this file
never collides with `test_opa_policy_adversarial.py`'s `tenant-a` (or any
other real tenant) sharing the same live OPA instance across test runs.
"""

from __future__ import annotations

import uuid

import pytest
from navigraph_shared.opa import HttpOpaClient, OpaSettings

pytestmark = pytest.mark.opa_integration

_PACKAGE_PATH = "navigraph/authz/decision"


def _client() -> HttpOpaClient:
    return HttpOpaClient(OpaSettings())


def _input_for(tenant_id: str, *, roles: list[str]) -> dict:
    return {
        "tenant_id": tenant_id,
        "user_id": "adversarial-test-user",
        "roles": roles,
        "claims": {"tenant_id": tenant_id},
        "intent": "metric_lookup",
        "data_source_id": "85db584d-cd08-48c1-a355-c1fe5ddaf2ff",
        "referenced_tables": ["STAGING_TRANSACTIONS"],
        "referenced_columns": ["STAGING_TRANSACTIONS.MARKETID"],
    }


@pytest.mark.asyncio
async def test_tenant_with_no_data_document_uses_the_real_fallback_defaults() -> None:
    tenant_id = f"phase12-fallback-{uuid.uuid4().hex[:8]}"
    client = _client()

    decision = await client.evaluate(
        package_path=_PACKAGE_PATH, input_document=_input_for(tenant_id, roles=["analyst"])
    )

    assert decision.allow is True


@pytest.mark.asyncio
async def test_pushing_a_real_data_document_changes_the_real_decision() -> None:
    tenant_id = f"phase12-override-{uuid.uuid4().hex[:8]}"
    client = _client()

    # Before any data document exists, "analyst" is allowed (the real
    # fallback) and "compliance_officer" is not (not in the fallback set).
    before_analyst = await client.evaluate(
        package_path=_PACKAGE_PATH, input_document=_input_for(tenant_id, roles=["analyst"])
    )
    before_compliance = await client.evaluate(
        package_path=_PACKAGE_PATH,
        input_document=_input_for(tenant_id, roles=["compliance_officer"]),
    )
    assert before_analyst.allow is True
    assert before_compliance.allow is False

    await client.set_data(
        path=f"navigraph/tenants/{tenant_id}",
        document={"allowed_roles": ["compliance_officer"]},
    )

    # After the real push: the tenant's own real, narrower role set wins --
    # analyst is now denied, compliance_officer is now allowed. Both
    # directions matter: this proves the override REPLACES the fallback
    # rather than merely adding to it.
    after_analyst = await client.evaluate(
        package_path=_PACKAGE_PATH, input_document=_input_for(tenant_id, roles=["analyst"])
    )
    after_compliance = await client.evaluate(
        package_path=_PACKAGE_PATH,
        input_document=_input_for(tenant_id, roles=["compliance_officer"]),
    )
    assert after_analyst.allow is False
    assert "compliance_officer" in after_analyst.deny_reasons[0]
    assert after_compliance.allow is True


@pytest.mark.asyncio
async def test_empty_allowed_roles_is_a_real_lockout_not_a_fallback_trigger() -> None:
    tenant_id = f"phase12-lockout-{uuid.uuid4().hex[:8]}"
    client = _client()

    await client.set_data(
        path=f"navigraph/tenants/{tenant_id}", document={"allowed_roles": []}
    )

    decision = await client.evaluate(
        package_path=_PACKAGE_PATH, input_document=_input_for(tenant_id, roles=["analyst", "admin"])
    )

    assert decision.allow is False
    assert "allowed: []" in decision.deny_reasons[0]


@pytest.mark.asyncio
async def test_one_tenants_data_document_never_affects_a_different_tenant() -> None:
    locked_out_tenant = f"phase12-isolation-locked-{uuid.uuid4().hex[:8]}"
    untouched_tenant = f"phase12-isolation-untouched-{uuid.uuid4().hex[:8]}"
    client = _client()

    await client.set_data(
        path=f"navigraph/tenants/{locked_out_tenant}", document={"allowed_roles": []}
    )

    locked_decision = await client.evaluate(
        package_path=_PACKAGE_PATH,
        input_document=_input_for(locked_out_tenant, roles=["analyst"]),
    )
    untouched_decision = await client.evaluate(
        package_path=_PACKAGE_PATH,
        input_document=_input_for(untouched_tenant, roles=["analyst"]),
    )

    assert locked_decision.allow is False
    assert untouched_decision.allow is True
