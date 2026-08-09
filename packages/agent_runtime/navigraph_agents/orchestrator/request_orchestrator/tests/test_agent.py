"""Real unit tests for the Request Orchestrator agent's own orchestration
logic -- sequencing, short-circuiting, the clarification trigger,
`data_source_id` auto-resolution, session read/append, and lineage-failure
swallowing.

This does NOT re-test each of the 20 sub-agents' own real behavior (each
already has its own real unit tests in its own directory) -- it constructs
a real `RequestOrchestratorAgent` with fake/lightweight constructor
dependencies (none of which are ever actually queried, since every
sub-agent's own `.run` method is monkeypatched with an `AsyncMock`
returning a real, correctly-shaped canned output), then asserts on the
ORCHESTRATOR's own decisions: which stage's output gets checked for a
short-circuit, what triggers `needs_clarification`, how `data_source_id`
gets resolved, and that lineage-recording failures never abort a request.

The real, full end-to-end proof (every real agent actually running,
against live Postgres/Neo4j/OPA/Redis) is
`tests/integration/orchestrator_pipeline/test_pipeline_chain.py`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from navigraph_shared.contracts import (
    AgentError,
    AgentMetadata,
    LineageEvent,
    RequestContext,
)
from navigraph_shared.llm import FakeLLMClient
from navigraph_shared.opa import FakeOpaClient

from navigraph_agents.guardrail.pii_exposure_checker.contracts import (
    GeneratedSql as PiiGeneratedSql,
)
from navigraph_agents.guardrail.pii_exposure_checker.contracts import (
    PiiExposureCheckerOutput,
    PiiExposureCheckerResult,
)
from navigraph_agents.guardrail.policy_authorization.contracts import (
    GeneratedSql as PolicyGeneratedSql,
)
from navigraph_agents.guardrail.policy_authorization.contracts import (
    PolicyAuthorizationOutput,
    PolicyAuthorizationResult,
)
from navigraph_agents.guardrail.query_cost_estimator.contracts import (
    OptimizedSql as CostOptimizedSql,
)
from navigraph_agents.guardrail.query_cost_estimator.contracts import (
    QueryCostEstimatorOutput,
    QueryCostEstimatorResult,
)
from navigraph_agents.guardrail.schema_constraint_validator.contracts import (
    GeneratedSql as ConstraintGeneratedSql,
)
from navigraph_agents.guardrail.schema_constraint_validator.contracts import (
    SchemaConstraintValidatorOutput,
    SchemaConstraintValidatorResult,
)
from navigraph_agents.insight.anomaly_outlier_highlighter.contracts import (
    AnomalyDetectionOutput,
    AnomalyDetectionResult,
)
from navigraph_agents.insight.chart_selection.contracts import (
    ChartSelectionOutput,
    ChartSelectionResult,
    ChartSpec,
)
from navigraph_agents.insight.follow_up_suggestion.contracts import (
    FollowUpQuestion,
    FollowUpSuggestionOutput,
    FollowUpSuggestionResult,
)
from navigraph_agents.insight.grounded_narrative_generation.contracts import (
    NarrativeGenerationOutput,
    NarrativeGenerationResult,
)
from navigraph_agents.orchestrator.clarification_coordinator.contracts import (
    ClarificationCoordinatorOutput,
    ClarificationCoordinatorResult,
)
from navigraph_agents.orchestrator.request_orchestrator.agent import (
    RequestOrchestratorAgent,
)
from navigraph_agents.orchestrator.request_orchestrator.contracts import (
    RequestOrchestratorInput,
    RequestOrchestratorPayload,
)
from navigraph_agents.orchestrator.session_context_manager.contracts import (
    SessionContextManagerOutput,
    SessionContextManagerResult,
)
from navigraph_agents.query.caching.contracts import (
    CachingOutput,
    CachingResult,
)
from navigraph_agents.query.data_federation.contracts import (
    DataFederationOutput,
    DataFederationResult,
)
from navigraph_agents.query.data_source_discovery.contracts import (
    DataSourceDiscoveryOutput,
    DataSourceDiscoveryResult,
    ResolvedDataSource,
)
from navigraph_agents.query.execution_planning.contracts import (
    ExecutionPlan,
    ExecutionPlanningOutput,
    ExecutionPlanningResult,
)
from navigraph_agents.query.sql_generation.contracts import (
    GeneratedSql,
    SqlGenerationOutput,
    SqlGenerationResult,
)
from navigraph_agents.query.sql_optimization.contracts import (
    OptimizedSql,
    SqlOptimizationOutput,
    SqlOptimizationResult,
)
from navigraph_agents.understanding.conversation.contracts import (
    ConversationOutput,
    ConversationResult,
)
from navigraph_agents.understanding.intent_understanding.contracts import (
    IntentUnderstandingOutput,
    IntentUnderstandingResult,
)
from navigraph_agents.understanding.metadata_discovery.contracts import (
    MetadataDiscoveryOutput,
    MetadataDiscoveryResult,
)
from navigraph_agents.understanding.ontology.contracts import (
    OntologyOutput,
    OntologyResult,
)
from navigraph_agents.understanding.schema_mapping.contracts import (
    ResolvedColumnRef,
    SchemaMappingOutput,
    SchemaMappingResult,
)
from navigraph_agents.understanding.semantic_retrieval.contracts import (
    SemanticRetrievalOutput,
    SemanticRetrievalResult,
)

_AGENT_MODULE = "navigraph_agents.orchestrator.request_orchestrator.agent"

_METADATA = AgentMetadata(latency_ms=1.0)


def _lineage(agent_name: str) -> list[LineageEvent]:
    return [
        LineageEvent(
            agent_name=agent_name,
            input_summary="x",
            output_summary="y",
            tenant_id="navikenz-poc",
            trace_id="trace-1",
        )
    ]


def _make_agent() -> Any:
    """A real `RequestOrchestratorAgent`, constructed with lightweight
    fakes for every constructor dependency -- none of these are ever
    actually queried, since every constructed sub-agent's own `.run` is
    monkeypatched in each test below.

    Typed `Any` (not `RequestOrchestratorAgent`) on purpose: every test
    below reaches through to monkeypatch a constructed sub-agent's own
    `.run` method (e.g. `agent._sql_generation_agent.run = AsyncMock(...)`)
    and later inspects the resulting `AsyncMock` (`.return_value`,
    `.assert_not_called()`, `.await_count`) -- mypy statically forbids
    assigning over a real bound method and has no way to know the
    replacement is a mock, so the real, concrete type would fail on
    every one of those lines. This is a test-only escape hatch; the real
    `RequestOrchestratorAgent` class itself stays fully typed."""

    return RequestOrchestratorAgent(
        llm_client=FakeLLMClient(),
        catalog_session_factory=MagicMock(),
        lineage_session_factory=MagicMock(),
        neo4j_client=MagicMock(),
        opa_client=FakeOpaClient(),
        cache_client=MagicMock(),
    )


def _make_input(*, session_id: str | None = None, data_source_id: str | None = None):
    return RequestOrchestratorInput(
        request_context=RequestContext(
            tenant_id="navikenz-poc",
            user_id="user-1",
            trace_id="trace-1",
            roles=["analyst"],
            claims={"tenant_id": "navikenz-poc"},
        ),
        payload=RequestOrchestratorPayload(
            question="What is the total transaction volume by market?",
            session_id=session_id,
            data_source_id=data_source_id,
        ),
    )


def _wire_happy_path(agent: Any) -> None:
    """Monkeypatch every sub-agent's `.run` to return a real, correctly-
    shaped canned output representing a clean, fully-successful run."""

    agent._session_context_manager_agent.run = AsyncMock(
        return_value=SessionContextManagerOutput(
            result=SessionContextManagerResult(
                session_id="sess_existing", conversation_history=[], turn_count=0
            ),
            confidence=1.0,
            lineage_events=_lineage("orchestrator.session_context_manager"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._conversation_agent.run = AsyncMock(
        return_value=ConversationOutput(
            result=ConversationResult(
                resolved_question="What is the total transaction volume by market?",
                is_follow_up=False,
                referenced_turn_id=None,
                raw_question="What is the total transaction volume by market?",
            ),
            confidence=1.0,
            lineage_events=_lineage("understanding.conversation"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._intent_agent.run = AsyncMock(
        return_value=IntentUnderstandingOutput(
            result=IntentUnderstandingResult(
                intent="comparison",
                entities=["units", "market"],
                raw_question="What is the total transaction volume by market?",
            ),
            confidence=1.0,
            lineage_events=_lineage("understanding.intent_understanding"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._metadata_discovery_agent.run = AsyncMock(
        return_value=MetadataDiscoveryOutput(
            result=MetadataDiscoveryResult(data_source_id="ds-1", columns=[]),
            confidence=1.0,
            lineage_events=_lineage("understanding.metadata_discovery"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._ontology_agent.run = AsyncMock(
        return_value=OntologyOutput(
            result=OntologyResult(
                concept_resolutions=[], relationship_resolutions=[], unresolved_terms=["market"]
            ),
            confidence=1.0,
            lineage_events=_lineage("understanding.ontology"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._semantic_retrieval_agent.run = AsyncMock(
        return_value=SemanticRetrievalOutput(
            result=SemanticRetrievalResult(matches=[]),
            confidence=1.0,
            lineage_events=_lineage("understanding.semantic_retrieval"),
            errors=[],
            metadata=_METADATA,
        )
    )
    column = ResolvedColumnRef(
        term="units",
        catalog_column_id="col-1",
        table_name="STAGING_TRANSACTIONS",
        schema_name="STAGING",
        column_name="UNITS",
        data_type="NUMBER",
        role="measure",
    )
    agent._schema_mapping_agent.run = AsyncMock(
        return_value=SchemaMappingOutput(
            result=SchemaMappingResult(
                tables=["STAGING_TRANSACTIONS"], columns=[column], joins=[], unmapped_terms=[]
            ),
            confidence=1.0,
            lineage_events=_lineage("understanding.schema_mapping"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._data_source_discovery_agent.run = AsyncMock(
        return_value=DataSourceDiscoveryOutput(
            result=DataSourceDiscoveryResult(
                resolved=[
                    ResolvedDataSource(
                        table_name="STAGING_TRANSACTIONS",
                        data_source_id="ds-1",
                        source_type="snowflake",
                        reachable=True,
                    )
                ],
                is_multi_source=False,
                unresolved_tables=[],
            ),
            confidence=1.0,
            lineage_events=_lineage("query.data_source_discovery"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._sql_generation_agent.run = AsyncMock(
        return_value=SqlGenerationOutput(
            result=SqlGenerationResult(
                statements=[
                    GeneratedSql(
                        data_source_id="ds-1",
                        sql="SELECT MARKETID, SUM(UNITS) AS UNITS_TOTAL FROM STAGING.STAGING_TRANSACTIONS GROUP BY MARKETID",
                        params={},
                        referenced_tables=["STAGING_TRANSACTIONS"],
                        referenced_columns=["STAGING_TRANSACTIONS.UNITS"],
                    )
                ],
                predicate_resolutions=[],
                unresolved_predicates=[],
            ),
            confidence=1.0,
            lineage_events=_lineage("query.sql_generation"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._schema_constraint_validator_agent.run = AsyncMock(
        return_value=SchemaConstraintValidatorOutput(
            result=SchemaConstraintValidatorResult(
                validated=[ConstraintGeneratedSql(**agent._sql_generation_agent.run.return_value.result.statements[0].model_dump())],
                rejected=[],
            ),
            confidence=1.0,
            lineage_events=_lineage("guardrail.schema_constraint_validator"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._pii_exposure_checker_agent.run = AsyncMock(
        return_value=PiiExposureCheckerOutput(
            result=PiiExposureCheckerResult(
                cleared=[PiiGeneratedSql(**agent._sql_generation_agent.run.return_value.result.statements[0].model_dump())],
                rejected=[],
            ),
            confidence=1.0,
            lineage_events=_lineage("guardrail.pii_exposure_checker"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._policy_authorization_agent.run = AsyncMock(
        return_value=PolicyAuthorizationOutput(
            result=PolicyAuthorizationResult(
                authorized=[PolicyGeneratedSql(**agent._sql_generation_agent.run.return_value.result.statements[0].model_dump())],
                rejected=[],
            ),
            confidence=1.0,
            lineage_events=_lineage("guardrail.policy_authorization"),
            errors=[],
            metadata=_METADATA,
        )
    )
    real_generated_sql = agent._sql_generation_agent.run.return_value.result.statements[0]
    agent._sql_optimization_agent.run = AsyncMock(
        return_value=SqlOptimizationOutput(
            result=SqlOptimizationResult(
                statements=[
                    OptimizedSql(
                        data_source_id="ds-1",
                        sql=real_generated_sql.sql,
                        params={},
                        applied_rules=[],
                        estimated_row_count=None,
                    )
                ],
                warnings=[],
            ),
            confidence=1.0,
            lineage_events=_lineage("query.sql_optimization"),
            errors=[],
            metadata=_METADATA,
        )
    )
    optimized = agent._sql_optimization_agent.run.return_value.result.statements[0]
    agent._query_cost_estimator_agent.run = AsyncMock(
        return_value=QueryCostEstimatorOutput(
            result=QueryCostEstimatorResult(
                approved=[CostOptimizedSql(**optimized.model_dump())],
                estimates=[],
                rejected=[],
            ),
            confidence=1.0,
            lineage_events=_lineage("guardrail.query_cost_estimator"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._execution_planning_agent.run = AsyncMock(
        return_value=ExecutionPlanningOutput(
            result=ExecutionPlanningResult(
                plans=[
                    ExecutionPlan(
                        data_source_id="ds-1",
                        route="direct_connector",
                        sql=optimized.sql,
                        params={},
                        timeout_seconds=30,
                        max_rows=10_000,
                        read_only_verified=True,
                    )
                ],
                requires_cross_source_join=False,
                rejected=[],
            ),
            confidence=1.0,
            lineage_events=_lineage("query.execution_planning"),
            errors=[],
            metadata=_METADATA,
        )
    )
    # Default: a real cache miss, so the happy path still exercises real
    # Data Federation -- see test_cache_hit_skips_data_federation below for
    # the hit path.
    agent._caching_agent.run = AsyncMock(
        return_value=CachingOutput(
            result=CachingResult(cache_key="navigraph:v1:test:query_cache:policy=none:x"),
            confidence=1.0,
            lineage_events=_lineage("query.caching"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._data_federation_agent.run = AsyncMock(
        return_value=DataFederationOutput(
            result=DataFederationResult(
                per_source_results=[],
                final_columns=["MARKETID", "UNITS_TOTAL"],
                final_rows=[{"MARKETID": "EBB", "UNITS_TOTAL": 100}],
                final_row_count=1,
                federated=False,
            ),
            confidence=1.0,
            lineage_events=_lineage("query.data_federation"),
            errors=[],
            metadata=_METADATA,
        )
    )
    real_chart = ChartSpec(
        chart_type="bar", x_column="MARKETID", y_column="UNITS_TOTAL", rationale="test"
    )
    agent._chart_selection_agent.run = AsyncMock(
        return_value=ChartSelectionOutput(
            result=ChartSelectionResult(chart=real_chart, unmatched_columns=[]),
            confidence=1.0,
            lineage_events=_lineage("insight.chart_selection"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._anomaly_agent.run = AsyncMock(
        return_value=AnomalyDetectionOutput(
            result=AnomalyDetectionResult(anomalies=[], skipped_reason="too few groups"),
            confidence=1.0,
            lineage_events=_lineage("insight.anomaly_outlier_highlighter"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._narrative_agent.run = AsyncMock(
        return_value=NarrativeGenerationOutput(
            result=NarrativeGenerationResult(
                narrative="Market EBB recorded 100 units.", citations=[], unverifiable_numbers=[]
            ),
            confidence=1.0,
            lineage_events=_lineage("insight.grounded_narrative_generation"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._follow_up_agent.run = AsyncMock(
        return_value=FollowUpSuggestionOutput(
            result=FollowUpSuggestionResult(
                suggestions=[FollowUpQuestion(question="What about last quarter?")]
            ),
            confidence=1.0,
            lineage_events=_lineage("insight.follow_up_suggestion"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._lineage_recorder_agent.run = AsyncMock()


async def test_happy_path_returns_answered_with_full_result() -> None:
    agent = _make_agent()
    _wire_happy_path(agent)

    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[MagicMock(id="ds-1")]):
        output = await agent.run(_make_input(data_source_id="ds-1"))

    assert output.result.outcome == "answered"
    assert output.result.narrative == "Market EBB recorded 100 units."
    assert output.result.chart == {
        "chart_type": "bar",
        "x_column": "MARKETID",
        "y_column": "UNITS_TOTAL",
        "rationale": "test",
    }
    assert output.result.follow_up_suggestions == ["What about last quarter?"]
    assert output.result.session_id.startswith("sess_")
    assert output.confidence == 1.0
    # Lineage recorded for every stage that ran.
    assert agent._lineage_recorder_agent.run.await_count >= 15


async def test_empty_schema_mapping_triggers_clarification_not_failure() -> None:
    agent = _make_agent()
    _wire_happy_path(agent)
    agent._schema_mapping_agent.run = AsyncMock(
        return_value=SchemaMappingOutput(
            result=SchemaMappingResult(tables=[], columns=[], joins=[], unmapped_terms=["market"]),
            confidence=0.5,
            lineage_events=_lineage("understanding.schema_mapping"),
            errors=[],
            metadata=_METADATA,
        )
    )
    agent._clarification_coordinator_agent.run = AsyncMock(
        return_value=ClarificationCoordinatorOutput(
            result=ClarificationCoordinatorResult(
                needs_clarification=True,
                clarifying_question="Which specific metric do you mean by 'market'?",
            ),
            confidence=1.0,
            lineage_events=_lineage("orchestrator.clarification_coordinator"),
            errors=[],
            metadata=_METADATA,
        )
    )

    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[MagicMock(id="ds-1")]):
        output = await agent.run(_make_input())

    assert output.result.outcome == "needs_clarification"
    assert output.result.clarifying_question == "Which specific metric do you mean by 'market'?"
    agent._clarification_coordinator_agent.run.assert_awaited_once()
    # Downstream Query/Guardrail/Insight stages must never have been called.
    agent._data_source_discovery_agent.run.assert_not_called()
    agent._sql_generation_agent.run.assert_not_called()
    agent._data_federation_agent.run.assert_not_called()


async def test_intent_understanding_non_recoverable_error_returns_failed() -> None:
    agent = _make_agent()
    _wire_happy_path(agent)
    agent._intent_agent.run = AsyncMock(
        return_value=IntentUnderstandingOutput(
            result=IntentUnderstandingResult(
                intent="unknown",
                entities=[],
                raw_question="What is the total transaction volume by market?",
            ),
            confidence=0.0,
            lineage_events=_lineage("understanding.intent_understanding"),
            errors=[AgentError(code="llm_call_failed", message="boom", recoverable=False)],
            metadata=_METADATA,
        )
    )

    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[MagicMock(id="ds-1")]):
        output = await agent.run(_make_input())

    assert output.result.outcome == "failed"
    assert output.result.failure_stage == "understanding.intent_understanding"
    agent._metadata_discovery_agent.run.assert_not_called()


async def test_guardrail_rejection_short_circuits_before_optimization() -> None:
    agent = _make_agent()
    _wire_happy_path(agent)
    real_statement = agent._sql_generation_agent.run.return_value.result.statements[0]
    agent._pii_exposure_checker_agent.run = AsyncMock(
        return_value=PiiExposureCheckerOutput(
            result=PiiExposureCheckerResult(
                cleared=[],
                rejected=[AgentError(code="pii_column_access_denied", message="no", recoverable=False)],
            ),
            confidence=0.0,
            lineage_events=_lineage("guardrail.pii_exposure_checker"),
            errors=[],
            metadata=_METADATA,
        )
    )

    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[MagicMock(id="ds-1")]):
        output = await agent.run(_make_input())

    assert output.result.outcome == "failed"
    assert output.result.failure_stage == "guardrail.pii_exposure_checker"
    agent._policy_authorization_agent.run.assert_not_called()
    agent._sql_optimization_agent.run.assert_not_called()
    del real_statement  # unused, kept for readability of the setup above


async def test_data_source_id_ambiguous_returns_failed_without_calling_any_agent() -> None:
    agent = _make_agent()
    _wire_happy_path(agent)

    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[]):
        output = await agent.run(_make_input())

    assert output.result.outcome == "failed"
    assert output.result.failure_stage == "orchestrator.data_source_resolution"
    agent._conversation_agent.run.assert_not_called()


async def test_data_source_id_ambiguous_when_no_default_is_marked() -> None:
    """Two registered data sources, neither marked `is_default` -- still
    ambiguous, same failure as zero registered. Only an EXACT single
    default resolves it (LIMITATIONS.md items 26/42)."""

    agent = _make_agent()
    _wire_happy_path(agent)

    data_sources = [
        MagicMock(id="ds-1", is_default=False),
        MagicMock(id="ds-2", is_default=False),
    ]
    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=data_sources):
        output = await agent.run(_make_input())

    assert output.result.outcome == "failed"
    assert output.result.failure_stage == "orchestrator.data_source_resolution"
    agent._conversation_agent.run.assert_not_called()


async def test_data_source_id_resolves_to_the_tenant_default_when_omitted() -> None:
    """Two registered data sources, exactly one marked `is_default` -- the
    caller omitting `data_source_id` now resolves to that default instead
    of failing (LIMITATIONS.md items 26/42, resolved 2026-08-09)."""

    agent = _make_agent()
    _wire_happy_path(agent)

    data_sources = [
        MagicMock(id="ds-1", is_default=False),
        MagicMock(id="ds-2", is_default=True),
    ]
    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=data_sources):
        output = await agent.run(_make_input())

    assert output.result.outcome == "answered"
    agent._data_source_discovery_agent.run.assert_awaited_once()


async def test_data_source_id_supplied_by_caller_skips_resolution() -> None:
    agent = _make_agent()
    _wire_happy_path(agent)

    with patch(f"{_AGENT_MODULE}.list_data_sources") as mock_list:
        output = await agent.run(_make_input(data_source_id="explicit-ds"))

    mock_list.assert_not_called()
    assert output.result.outcome == "answered"


async def test_session_id_is_minted_when_caller_omits_one() -> None:
    agent = _make_agent()
    _wire_happy_path(agent)

    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[MagicMock(id="ds-1")]):
        output = await agent.run(_make_input(session_id=None))

    assert output.result.session_id.startswith("sess_")
    assert len(output.result.session_id) > len("sess_")


async def test_session_id_supplied_by_caller_is_echoed_back() -> None:
    agent = _make_agent()
    _wire_happy_path(agent)

    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[MagicMock(id="ds-1")]):
        output = await agent.run(_make_input(session_id="sess_existing", data_source_id="ds-1"))

    assert output.result.session_id == "sess_existing"


async def test_lineage_recording_failure_never_aborts_the_request() -> None:
    agent = _make_agent()
    _wire_happy_path(agent)
    agent._lineage_recorder_agent.run = AsyncMock(side_effect=RuntimeError("lineage store down"))

    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[MagicMock(id="ds-1")]):
        output = await agent.run(_make_input(data_source_id="ds-1"))

    assert output.result.outcome == "answered"


async def test_cache_hit_skips_data_federation() -> None:
    """Resolves LIMITATIONS.md item 59: a real cache hit must be served
    without calling Data Federation at all, using the cached
    DataFederationResult's own final_columns/final_rows/final_row_count."""

    agent = _make_agent()
    _wire_happy_path(agent)
    agent._caching_agent.run = AsyncMock(
        return_value=CachingOutput(
            result=CachingResult(
                cache_key="navigraph:v1:test:query_cache:policy=none:x",
                hit=True,
                cached_value={
                    "per_source_results": [],
                    "final_columns": ["MARKETID", "UNITS_TOTAL"],
                    "final_rows": [{"MARKETID": "CACHED", "UNITS_TOTAL": 999}],
                    "final_row_count": 1,
                    "federated": False,
                },
            ),
            confidence=1.0,
            lineage_events=_lineage("query.caching"),
            errors=[],
            metadata=_METADATA,
        )
    )

    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[MagicMock(id="ds-1")]):
        output = await agent.run(_make_input(data_source_id="ds-1"))

    assert output.result.outcome == "answered"
    assert output.result.final_rows == [{"MARKETID": "CACHED", "UNITS_TOTAL": 999}]
    agent._data_federation_agent.run.assert_not_called()
    # Only one real caching call (the lookup) -- no store call on a hit,
    # since nothing new was executed.
    assert agent._caching_agent.run.await_count == 1


async def test_cache_miss_calls_data_federation_then_stores_the_result() -> None:
    """The default `_wire_happy_path` case: a real cache miss must still
    call Data Federation for real, then store its result for next time."""

    agent = _make_agent()
    _wire_happy_path(agent)

    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[MagicMock(id="ds-1")]):
        output = await agent.run(_make_input(data_source_id="ds-1"))

    assert output.result.outcome == "answered"
    agent._data_federation_agent.run.assert_awaited_once()
    # Two real caching calls: the lookup (miss) and the store afterward.
    assert agent._caching_agent.run.await_count == 2
    lookup_call, store_call = agent._caching_agent.run.await_args_list
    assert lookup_call.args[0].payload.operation == "lookup"
    assert store_call.args[0].payload.operation == "store"
    assert store_call.args[0].payload.value == {
        "per_source_results": [],
        "final_columns": ["MARKETID", "UNITS_TOTAL"],
        "final_rows": [{"MARKETID": "EBB", "UNITS_TOTAL": 100}],
        "final_row_count": 1,
        "federated": False,
    }


async def test_cache_backend_error_on_lookup_is_recorded_but_never_blocks_execution() -> None:
    """A recoverable cache error must behave exactly like a real miss --
    Data Federation still runs, the request still answers."""

    agent = _make_agent()
    _wire_happy_path(agent)
    agent._caching_agent.run = AsyncMock(
        return_value=CachingOutput(
            result=CachingResult(cache_key="navigraph:v1:test:query_cache:policy=none:x"),
            confidence=0.5,
            lineage_events=_lineage("query.caching"),
            errors=[
                AgentError(
                    code="cache_backend_unavailable", message="redis down", recoverable=True
                )
            ],
            metadata=_METADATA,
        )
    )

    with patch(f"{_AGENT_MODULE}.list_data_sources", return_value=[MagicMock(id="ds-1")]):
        output = await agent.run(_make_input(data_source_id="ds-1"))

    assert output.result.outcome == "answered"
    agent._data_federation_agent.run.assert_awaited_once()
    assert any(e.code == "cache_backend_unavailable" for e in output.errors)
