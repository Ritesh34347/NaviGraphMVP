"""Adversarial test: exercise the real, non-allow-all `authz.rego` policy
directly with malformed/adversarial inputs.

Satisfies `tests/security/README.md`'s REQUIRED minimum #3: exercise the
real OPA policy with adversarial inputs -- malformed tenant IDs, missing
claims, role escalation attempts -- and assert every one is denied.

Calls `HttpOpaClient.evaluate` directly (below the
`PolicyAuthorizationAgent` layer) so each case is a surgical, minimal
`input` document straight against the real policy -- not routed through
the full agent contract, which the other two files in this directory
already cover. This is the real, live `opa` docker-compose service running
`infra/opa/policies/authz.rego`, never a `FakeOpaClient`.
"""

from __future__ import annotations

import pytest
from navigraph_shared.opa import HttpOpaClient, OpaSettings

pytestmark = pytest.mark.opa_integration

_PACKAGE_PATH = "navigraph/authz/decision"

_BASE_INPUT = {
    "tenant_id": "tenant-a",
    "user_id": "adversarial-test-user",
    "roles": ["analyst"],
    "claims": {"tenant_id": "tenant-a"},
    "intent": "metric_lookup",
    "data_source_id": "85db584d-cd08-48c1-a355-c1fe5ddaf2ff",
    "referenced_tables": ["STAGING_TRANSACTIONS"],
    "referenced_columns": ["STAGING_TRANSACTIONS.MARKETID"],
}


def _client() -> HttpOpaClient:
    return HttpOpaClient(OpaSettings())


@pytest.mark.asyncio
async def test_control_case_is_allowed() -> None:
    """Sanity control, run against the same real policy every adversarial
    case below runs against: a well-formed, legitimate input is allowed."""

    decision = await _client().evaluate(package_path=_PACKAGE_PATH, input_document=_BASE_INPUT)

    assert decision.allow is True
    assert decision.deny_reasons == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_name,overrides",
    [
        (
            "empty_tenant_id",
            {"tenant_id": "", "claims": {"tenant_id": ""}},
        ),
        (
            "mismatched_sql_injection_shaped_tenant_id",
            # A SQL-injection-shaped string on its own isn't a threat to THIS
            # rule -- it's an opaque string compared with plain `==`, never
            # interpolated into SQL text (that boundary belongs to SQL
            # Generation's bind-parameterization and Execution Planning's
            # SELECT-only gate, not this ABAC check). What this rule must
            # still get right is that two DIFFERENT injection-shaped strings
            # don't somehow compare equal -- i.e. weird-looking content
            # doesn't confuse the equality check into a false match.
            {
                "tenant_id": "tenant-a' OR '1'='1",
                "claims": {"tenant_id": "tenant-b' OR '1'='1"},
            },
        ),
        (
            "missing_claims_tenant_id_key_entirely",
            {"claims": {}},
        ),
        (
            "claims_is_missing_entirely",
            {"claims": None},
        ),
        (
            "empty_roles_list",
            {"roles": []},
        ),
        (
            "role_escalation_attempt_self_declared_admin",
            {"roles": ["admin"], "claims": {"tenant_id": "some-other-tenant"}},
        ),
        (
            "tenant_mismatch_with_valid_role",
            {"claims": {"tenant_id": "a-completely-different-tenant"}},
        ),
    ],
)
async def test_adversarial_input_is_denied(case_name: str, overrides: dict) -> None:
    input_document = {**_BASE_INPUT, **overrides}

    decision = await _client().evaluate(package_path=_PACKAGE_PATH, input_document=input_document)

    assert decision.allow is False, f"expected denial for case {case_name!r}, got allow=True"
    assert len(decision.deny_reasons) >= 1, (
        f"expected at least one deny reason for case {case_name!r}"
    )


@pytest.mark.asyncio
async def test_self_declared_role_escalation_with_a_matching_tenant_claim_is_allowed() -> None:
    """Documents a real, KNOWN, deliberately out-of-scope gap rather than
    leaving it implicit: this policy can only check that `input.roles`
    contains an allowed role string -- it has no way to verify that role
    claim's provenance. A caller that self-declares `roles=["admin"]` with
    an otherwise-legitimate, matching tenant claim IS allowed by this
    policy, because Rego has no cryptographic identity to check it
    against. Closing this requires real Azure AD JWT verification
    populating `RequestContext.roles` from a verified token -- a separate,
    explicitly deferred gap (see `LIMITATIONS.md`), not a bug in this
    policy. This test exists so that gap is asserted and visible, not
    silently assumed away."""

    input_document = {**_BASE_INPUT, "roles": ["admin"]}

    decision = await _client().evaluate(package_path=_PACKAGE_PATH, input_document=input_document)

    assert decision.allow is True
