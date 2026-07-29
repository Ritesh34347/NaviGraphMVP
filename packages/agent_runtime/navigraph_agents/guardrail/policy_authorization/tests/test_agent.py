"""Real unit tests for the Policy Authorization agent, network-free.

Uses `navigraph_shared.opa.FakeOpaClient` throughout -- never a real OPA
server, never a real HTTP call. Mirrors this repo's established "inject a
fake client, assert on exactly what it recorded" convention (see
`navigraph_shared.llm.FakeLLMClient`'s equivalent role in every LLM-backed
agent's own tests).

`asyncio_mode = "auto"` is set in packages/agent_runtime/pyproject.toml, so
these `async def test_...` functions run without an explicit
`@pytest.mark.asyncio` decorator.
"""

from __future__ import annotations

from navigraph_shared.contracts import RequestContext
from navigraph_shared.opa import FakeOpaClient, OpaDecisionResponse

from navigraph_agents.guardrail.policy_authorization.agent import (
    PolicyAuthorizationAgent,
)
from navigraph_agents.guardrail.policy_authorization.contracts import (
    GeneratedSql,
    IntentLabel,
    PolicyAuthorizationInput,
    PolicyAuthorizationPayload,
)


def _request_context() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-acme",
        user_id="user-1",
        trace_id="trace-1",
        roles=["analyst"],
        claims={"department": "finance"},
    )


def _make_input(
    statements: list[GeneratedSql], intent: IntentLabel = "metric_lookup"
) -> PolicyAuthorizationInput:
    return PolicyAuthorizationInput(
        request_context=_request_context(),
        payload=PolicyAuthorizationPayload(statements=statements, intent=intent),
    )


def _statement(data_source_id: str) -> GeneratedSql:
    return GeneratedSql(
        data_source_id=data_source_id,
        sql="SELECT 1",
        params={},
        referenced_tables=["ORDERS"],
        referenced_columns=["ORDER_ID"],
    )


async def test_allow_response_authorizes_every_statement_with_no_rejections() -> None:
    fake_client = FakeOpaClient(response=True)
    agent = PolicyAuthorizationAgent(opa_client=fake_client)

    statements = [_statement("ds-1"), _statement("ds-2")]
    output = await agent.run(_make_input(statements))

    assert len(output.result.authorized) == 2
    assert output.result.rejected == []
    assert len(output.result.decisions) == 2
    assert all(decision.allow is True for decision in output.result.decisions)
    assert all(decision.deny_reasons == [] for decision in output.result.decisions)
    assert output.confidence == 1.0
    assert output.errors == []

    assert len(output.lineage_events) == 1
    lineage = output.lineage_events[0]
    assert lineage.agent_name == "guardrail.policy_authorization"
    assert lineage.tenant_id == "tenant-acme"
    assert lineage.trace_id == "trace-1"
    assert output.metadata.latency_ms >= 0


async def test_deny_response_rejects_every_statement_but_still_records_decisions() -> None:
    fake_client = FakeOpaClient(
        response=OpaDecisionResponse(allow=False, deny_reasons=["tenant_isolation_violation"])
    )
    agent = PolicyAuthorizationAgent(opa_client=fake_client)

    statements = [_statement("ds-1"), _statement("ds-2")]
    output = await agent.run(_make_input(statements))

    assert output.result.authorized == []
    assert len(output.result.rejected) == 2
    assert all(error.code == "policy_denied" for error in output.result.rejected)
    assert all(error.recoverable is False for error in output.result.rejected)

    # Decisions are still populated for audit, even though nothing was authorized.
    assert len(output.result.decisions) == 2
    assert all(decision.allow is False for decision in output.result.decisions)
    assert all(
        decision.deny_reasons == ["tenant_isolation_violation"]
        for decision in output.result.decisions
    )

    assert output.confidence == 0.0


async def test_mixed_batch_only_allowed_statements_land_in_authorized() -> None:
    statements = [_statement("ds-allow"), _statement("ds-deny")]

    def response_fn(package_path: str, input_document: dict) -> OpaDecisionResponse:
        if input_document["data_source_id"] == "ds-allow":
            return OpaDecisionResponse(allow=True, deny_reasons=[])
        return OpaDecisionResponse(allow=False, deny_reasons=["rbac_denied"])

    fake_client = FakeOpaClient(response_fn=response_fn)
    agent = PolicyAuthorizationAgent(opa_client=fake_client)

    output = await agent.run(_make_input(statements))

    assert len(output.result.authorized) == 1
    assert output.result.authorized[0].data_source_id == "ds-allow"

    assert len(output.result.rejected) == 1
    assert output.result.rejected[0].code == "policy_denied"
    assert "ds-deny" in output.result.rejected[0].message

    assert len(output.result.decisions) == 2
    allow_decision = next(d for d in output.result.decisions if d.data_source_id == "ds-allow")
    deny_decision = next(d for d in output.result.decisions if d.data_source_id == "ds-deny")
    assert allow_decision.allow is True
    assert deny_decision.allow is False
    assert deny_decision.deny_reasons == ["rbac_denied"]

    assert output.confidence == 0.0


async def test_opa_unreachable_fails_closed_with_exactly_one_error() -> None:
    """The fail-CLOSED contract: OPA raising for the first statement in a
    multi-statement batch must not be retried per statement, must discard
    anything already authorized, and must surface as exactly one
    `opa_unreachable` error -- not one per statement."""

    fake_client = FakeOpaClient(raise_exc=ConnectionError("refused"))
    agent = PolicyAuthorizationAgent(opa_client=fake_client)

    statements = [_statement("ds-1"), _statement("ds-2"), _statement("ds-3")]
    output = await agent.run(_make_input(statements))

    assert output.result.authorized == []
    assert len(output.result.rejected) == 1
    assert output.result.rejected[0].code == "opa_unreachable"
    assert output.result.rejected[0].recoverable is False
    assert "refused" in output.result.rejected[0].message

    # Fails closed on the very first statement -- OPA is never called again
    # for the remaining statements in the same batch.
    assert len(fake_client.calls) == 1

    assert output.confidence == 0.0
    assert output.errors == []
    assert len(output.lineage_events) == 1
    assert output.metadata.latency_ms >= 0


async def test_input_document_shape_sent_to_opa() -> None:
    """Assert on exactly what this agent sends OPA -- the real `input`
    document shape a Rego policy author will actually write rules against."""

    fake_client = FakeOpaClient(response=True)
    agent = PolicyAuthorizationAgent(opa_client=fake_client)

    statement = _statement("ds-1")
    await agent.run(_make_input([statement], intent="trend_analysis"))

    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["package_path"] == "navigraph/authz/decision"
    assert call["input_document"] == {
        "tenant_id": "tenant-acme",
        "user_id": "user-1",
        "roles": ["analyst"],
        "claims": {"department": "finance"},
        "intent": "trend_analysis",
        "data_source_id": "ds-1",
        "referenced_tables": ["ORDERS"],
        "referenced_columns": ["ORDER_ID"],
    }
