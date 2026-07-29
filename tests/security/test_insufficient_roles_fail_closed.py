"""Adversarial test: insufficient roles/claims fail CLOSED, not open.

Satisfies `tests/security/README.md`'s REQUIRED minimum #2: attempt to
invoke an agent with insufficient `roles`/`claims` and assert authorization
fails closed (deny by default), not open.

Two distinct failure modes are proven here, both against a live-service
dependency, both resulting in zero authorized statements:

1. A real OPA denial: empty/unrecognized `roles`, even with a genuinely
   matching tenant claim -- the real, non-allow-all `authz.rego` policy's
   `default allow := false` must actually fire, not silently default to
   allow.
2. A real infra failure: `PolicyAuthorizationAgent` pointed at an
   unreachable OPA instance must fail closed (`opa_unreachable`,
   `recoverable=False`, `authorized=[]`) rather than treating "the policy
   engine didn't answer" as an implicit allow -- the deliberate opposite of
   `CachingAgent`'s fail-open convention (see
   `guardrail/policy_authorization/agent.py`'s module docstring).
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
async def test_empty_roles_with_matching_tenant_claim_is_denied() -> None:
    """A matching tenant claim alone is not sufficient -- `roles=[]` must
    still be denied by the real policy's RBAC rule (deny-by-default)."""

    context = RequestContext(
        tenant_id="tenant-a",
        user_id="no-role-user",
        trace_id="adversarial-no-roles",
        roles=[],
        claims={"tenant_id": "tenant-a"},
    )

    agent = PolicyAuthorizationAgent(opa_client=HttpOpaClient(OpaSettings()))
    output = await agent.run(
        PolicyAuthorizationInput(
            request_context=context,
            payload=PolicyAuthorizationPayload(statements=[_statement()], intent="metric_lookup"),
        )
    )

    assert output.result.authorized == []
    assert len(output.result.rejected) == 1
    assert output.result.rejected[0].code == "policy_denied"
    assert "no role" in output.result.rejected[0].message.lower()


@pytest.mark.asyncio
async def test_unrecognized_role_with_matching_tenant_claim_is_denied() -> None:
    """A role string that simply isn't in the policy's `allowed_roles` set
    must be denied -- proves the check is a real allowlist, not a
    non-empty-list check."""

    context = RequestContext(
        tenant_id="tenant-a",
        user_id="bogus-role-user",
        trace_id="adversarial-bogus-role",
        roles=["not_a_real_role"],
        claims={"tenant_id": "tenant-a"},
    )

    agent = PolicyAuthorizationAgent(opa_client=HttpOpaClient(OpaSettings()))
    output = await agent.run(
        PolicyAuthorizationInput(
            request_context=context,
            payload=PolicyAuthorizationPayload(statements=[_statement()], intent="metric_lookup"),
        )
    )

    assert output.result.authorized == []
    assert len(output.result.rejected) == 1
    assert output.result.rejected[0].code == "policy_denied"


@pytest.mark.asyncio
async def test_opa_unreachable_fails_closed_not_open() -> None:
    """A real, live infra failure -- OPA unreachable at the configured URL
    -- must never be treated as an implicit allow."""

    context = RequestContext(
        tenant_id="tenant-a",
        user_id="legitimate-user",
        trace_id="adversarial-opa-unreachable",
        roles=["analyst"],
        claims={"tenant_id": "tenant-a"},
    )

    # A real, but deliberately unreachable, address -- not mocked: this
    # proves HttpOpaClient's real connection-failure exception path (not a
    # canned FakeOpaClient response) is what PolicyAuthorizationAgent
    # catches and fails closed on.
    unreachable_client = HttpOpaClient(OpaSettings(opa_url="http://localhost:1"))
    agent = PolicyAuthorizationAgent(opa_client=unreachable_client)

    output = await agent.run(
        PolicyAuthorizationInput(
            request_context=context,
            payload=PolicyAuthorizationPayload(statements=[_statement()], intent="metric_lookup"),
        )
    )

    assert output.result.authorized == [], (
        "OPA being unreachable must never be treated as an implicit allow"
    )
    assert len(output.result.rejected) == 1
    assert output.result.rejected[0].code == "opa_unreachable"
    assert output.result.rejected[0].recoverable is False
