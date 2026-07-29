"""Adversarial test: tenant isolation via the real OPA policy.

Satisfies `tests/security/README.md`'s REQUIRED minimum #1: construct a
`RequestContext` for tenant A, attempt to access/query/influence output
scoped to tenant B, and assert it is rejected.

Runs against the real, live `opa` docker-compose service and the real,
non-allow-all `infra/opa/policies/authz.rego` policy -- not mocked, not a
`FakeOpaClient`. This is the real, load-bearing proof that
`guardrail.policy_authorization`'s tenant ABAC check actually works, not
just an assertion about intended behavior.

Point this at the live OPA service via `OPA_URL` (defaults to the
docker-compose in-network DNS name; from the host against
`infra/docker-compose.yml`'s published ports, set
`OPA_URL=http://localhost:8181`).
"""

from __future__ import annotations

import pytest
from navigraph_agents.guardrail.policy_authorization.agent import (
    PolicyAuthorizationAgent,
)
from navigraph_agents.guardrail.policy_authorization.contracts import (
    GeneratedSql,
    PolicyAuthorizationInput,
    PolicyAuthorizationPayload,
)
from navigraph_shared.contracts import RequestContext
from navigraph_shared.opa import HttpOpaClient, OpaSettings

pytestmark = pytest.mark.opa_integration


def _statement() -> GeneratedSql:
    return GeneratedSql(
        data_source_id="85db584d-cd08-48c1-a355-c1fe5ddaf2ff",
        sql="SELECT MARKETID FROM STAGING_TRANSACTIONS",
        params={},
        referenced_tables=["STAGING_TRANSACTIONS"],
        referenced_columns=["STAGING_TRANSACTIONS.MARKETID"],
    )


@pytest.mark.asyncio
async def test_mismatched_tenant_claim_is_denied_by_the_real_opa_policy() -> None:
    """Tenant A's request carries a claim asserting tenant B -- the real
    Rego policy's `tenant_claim_matches` rule must deny this, not silently
    scope the request to whichever tenant the claim names."""

    tenant_a_context = RequestContext(
        tenant_id="tenant-a",
        user_id="attacker",
        trace_id="adversarial-tenant-isolation-1",
        roles=["analyst"],
        claims={"tenant_id": "tenant-b"},
    )

    agent = PolicyAuthorizationAgent(opa_client=HttpOpaClient(OpaSettings()))
    output = await agent.run(
        PolicyAuthorizationInput(
            request_context=tenant_a_context,
            payload=PolicyAuthorizationPayload(statements=[_statement()], intent="metric_lookup"),
        )
    )

    assert output.result.authorized == [], (
        "a request whose claims name a DIFFERENT tenant than the request itself "
        "must never be authorized -- this is the literal tenant-isolation bypass "
        "this test exists to catch"
    )
    assert len(output.result.rejected) == 1
    assert output.result.rejected[0].code == "policy_denied"
    assert "tenant-a" in output.result.rejected[0].message
    assert "tenant-b" in output.result.rejected[0].message


@pytest.mark.asyncio
async def test_matching_tenant_claim_is_the_control_case_and_is_allowed() -> None:
    """Control case, run against the SAME real OPA service: a genuine,
    matching tenant claim with an authorized role must be allowed -- proves
    the previous test's denial is about the mismatch specifically, not
    about the policy denying everything."""

    tenant_a_context = RequestContext(
        tenant_id="tenant-a",
        user_id="legitimate-user",
        trace_id="adversarial-tenant-isolation-control",
        roles=["analyst"],
        claims={"tenant_id": "tenant-a"},
    )

    agent = PolicyAuthorizationAgent(opa_client=HttpOpaClient(OpaSettings()))
    output = await agent.run(
        PolicyAuthorizationInput(
            request_context=tenant_a_context,
            payload=PolicyAuthorizationPayload(statements=[_statement()], intent="metric_lookup"),
        )
    )

    assert output.result.rejected == []
    assert len(output.result.authorized) == 1
