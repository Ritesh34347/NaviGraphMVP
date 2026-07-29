"""Real, shared pipeline-chaining helper for the LLM-as-judge evaluation
harness (`eval/run_harness.py`).

Threads one question through the entire real, already-proven pipeline --
Understanding (Conversation -> Intent Understanding -> Metadata Discovery ->
Ontology -> Semantic Retrieval -> Schema Mapping) -> Query (Data Source
Discovery -> SQL Generation) -> all four real Guardrail gates (Schema
Constraint Validator, PII Exposure Checker, Policy Authorization) -> SQL
Optimization -> Query Cost Estimator -> Execution Planning -> real Data
Federation (against live Snowflake) -> Insight (Chart Selection ->
Anomaly/Outlier Highlighter -> Grounded Narrative Generation -> Follow-up
Suggestion) -- using ONE real, caller-supplied `LLMClient` for every
LLM-backed step. This is what a real Orchestrator would eventually do; no
such Orchestrator exists yet (see `LIMITATIONS.md` items on the
`ChartColumnRef.result_alias` manual-threading gap this function still has
to replicate by hand, same as every pipeline integration test).

DELIBERATELY NOT reused by
`tests/integration/insight_pipeline/test_pipeline_chain.py` -- a documented
deviation from the original Phase 8 plan, which proposed refactoring that
test to call this same helper. That test needs fully deterministic,
per-step CANNED LLM responses (a fixed intent, a fixed semantic-retrieval
match, a hand-crafted fabricated citation) to reliably exercise specific
mechanics -- citation-validation rejection, z-score correctness -- against
a known-in-advance resolved schema. Routing it through this helper's
single real `LLMClient` would either make it flaky (real model variability
changing which entities/columns resolve) or require a complex per-step
response-injection mechanism here for no real benefit over the ~150 lines
of duplication it would save. See `DECISIONS.md`'s entry on this exact
tradeoff for the full reasoning.

Every stage checks for a real failure (an `AgentError`, a Guardrail
rejection, an unresolved schema) and short-circuits to a
`PipelineResult(succeeded=False, failure_stage=..., failure_reason=...)`
rather than raising or assuming success -- unlike the worked example in
the integration tests (hand-verified to resolve cleanly), golden-set
questions are real, arbitrary questions run against a REAL (non-canned)
LLM at every step, and are not guaranteed to resolve or pass every gate.
A partial resolution (some `unmapped_terms` but at least one usable table)
is a soft, logged warning, not a hard failure -- the pipeline still
attempts to answer with whatever real fields DID resolve.
"""

from __future__ import annotations

from typing import Any

import navigraph_connectors.snowflake  # noqa: F401 -- registers "snowflake" for real connector resolution
from navigraph_agents.guardrail.pii_exposure_checker.agent import (
    PiiExposureCheckerAgent,
)
from navigraph_agents.guardrail.pii_exposure_checker.contracts import (
    GeneratedSql as PiiGeneratedSql,
)
from navigraph_agents.guardrail.pii_exposure_checker.contracts import (
    PiiExposureCheckerInput,
    PiiExposureCheckerPayload,
)
from navigraph_agents.guardrail.policy_authorization.agent import (
    PolicyAuthorizationAgent,
)
from navigraph_agents.guardrail.policy_authorization.contracts import (
    GeneratedSql as PolicyGeneratedSql,
)
from navigraph_agents.guardrail.policy_authorization.contracts import (
    PolicyAuthorizationInput,
    PolicyAuthorizationPayload,
)
from navigraph_agents.guardrail.query_cost_estimator.agent import (
    QueryCostEstimatorAgent,
)
from navigraph_agents.guardrail.query_cost_estimator.contracts import (
    OptimizedSql as CostOptimizedSql,
)
from navigraph_agents.guardrail.query_cost_estimator.contracts import (
    QueryCostEstimatorInput,
    QueryCostEstimatorPayload,
)
from navigraph_agents.guardrail.schema_constraint_validator.agent import (
    SchemaConstraintValidatorAgent,
)
from navigraph_agents.guardrail.schema_constraint_validator.contracts import (
    GeneratedSql as ConstraintGeneratedSql,
)
from navigraph_agents.guardrail.schema_constraint_validator.contracts import (
    SchemaConstraintValidatorInput,
    SchemaConstraintValidatorPayload,
)
from navigraph_agents.insight.anomaly_outlier_highlighter.agent import (
    AnomalyOutlierHighlighterAgent,
)
from navigraph_agents.insight.anomaly_outlier_highlighter.contracts import (
    AnomalyDetectionInput,
    AnomalyDetectionPayload,
)
from navigraph_agents.insight.anomaly_outlier_highlighter.contracts import (
    ChartSpec as AnomalyChartSpec,
)
from navigraph_agents.insight.chart_selection.agent import ChartSelectionAgent
from navigraph_agents.insight.chart_selection.contracts import (
    ChartColumnRef,
    ChartSelectionInput,
    ChartSelectionPayload,
)
from navigraph_agents.insight.follow_up_suggestion.agent import FollowUpSuggestionAgent
from navigraph_agents.insight.follow_up_suggestion.contracts import (
    AnomalyFinding as FollowUpAnomalyFinding,
)
from navigraph_agents.insight.follow_up_suggestion.contracts import (
    ChartSpec as FollowUpChartSpec,
)
from navigraph_agents.insight.follow_up_suggestion.contracts import (
    FollowUpSuggestionInput,
    FollowUpSuggestionPayload,
)
from navigraph_agents.insight.grounded_narrative_generation.agent import (
    GroundedNarrativeGenerationAgent,
)
from navigraph_agents.insight.grounded_narrative_generation.contracts import (
    AnomalyFinding as NarrativeAnomalyFinding,
)
from navigraph_agents.insight.grounded_narrative_generation.contracts import (
    ChartSpec as NarrativeChartSpec,
)
from navigraph_agents.insight.grounded_narrative_generation.contracts import (
    NarrativeGenerationInput,
    NarrativeGenerationPayload,
)
from navigraph_agents.query.data_federation.agent import DataFederationAgent
from navigraph_agents.query.data_federation.contracts import (
    DataFederationInput,
    DataFederationPayload,
)
from navigraph_agents.query.data_federation.contracts import (
    ExecutionPlan as FederationExecutionPlan,
)
from navigraph_agents.query.data_source_discovery.agent import DataSourceDiscoveryAgent
from navigraph_agents.query.data_source_discovery.contracts import (
    DataSourceDiscoveryInput,
    DataSourceDiscoveryPayload,
)
from navigraph_agents.query.execution_planning.agent import ExecutionPlanningAgent
from navigraph_agents.query.execution_planning.contracts import (
    ExecutionPlanningInput,
    ExecutionPlanningPayload,
)
from navigraph_agents.query.execution_planning.contracts import (
    OptimizedSql as PlanningOptimizedSql,
)
from navigraph_agents.query.sql_generation.agent import SqlGenerationAgent
from navigraph_agents.query.sql_generation.contracts import (
    JoinSpec as GenerationJoinSpec,
)
from navigraph_agents.query.sql_generation.contracts import (
    ResolvedColumnRef as GenerationResolvedColumnRef,
)
from navigraph_agents.query.sql_generation.contracts import (
    ResolvedDataSource as GenerationResolvedDataSource,
)
from navigraph_agents.query.sql_generation.contracts import (
    SchemaMappingResult as GenerationSchemaMappingResult,
)
from navigraph_agents.query.sql_generation.contracts import (
    SqlGenerationInput,
    SqlGenerationPayload,
)
from navigraph_agents.query.sql_optimization.agent import SqlOptimizationAgent
from navigraph_agents.query.sql_optimization.contracts import (
    GeneratedSql as OptimizationGeneratedSql,
)
from navigraph_agents.query.sql_optimization.contracts import (
    SqlOptimizationInput,
    SqlOptimizationPayload,
)
from navigraph_agents.understanding.conversation.agent import ConversationAgent
from navigraph_agents.understanding.conversation.contracts import (
    ConversationInput,
    ConversationPayload,
)
from navigraph_agents.understanding.intent_understanding.agent import (
    IntentUnderstandingAgent,
)
from navigraph_agents.understanding.intent_understanding.contracts import (
    IntentLabel,
    IntentUnderstandingInput,
    IntentUnderstandingPayload,
)
from navigraph_agents.understanding.metadata_discovery.agent import (
    MetadataDiscoveryAgent,
)
from navigraph_agents.understanding.metadata_discovery.contracts import (
    MetadataDiscoveryInput,
    MetadataDiscoveryPayload,
)
from navigraph_agents.understanding.ontology.agent import OntologyAgent
from navigraph_agents.understanding.ontology.contracts import (
    OntologyInput,
    OntologyPayload,
)
from navigraph_agents.understanding.schema_mapping.agent import SchemaMappingAgent
from navigraph_agents.understanding.schema_mapping.contracts import (
    CatalogInventoryEntry,
    ConceptResolution,
    RelationshipResolution,
    SchemaMappingInput,
    SchemaMappingPayload,
    TermMatch,
)
from navigraph_agents.understanding.semantic_retrieval.agent import (
    SemanticRetrievalAgent,
)
from navigraph_agents.understanding.semantic_retrieval.contracts import (
    RetrievalCandidate,
    SemanticRetrievalInput,
    SemanticRetrievalPayload,
)
from navigraph_catalog.db import session_scope
from navigraph_kg.client import Neo4jClient
from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import LLMClient
from navigraph_shared.opa import OpaClient
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker


class PipelineResult(BaseModel):
    """Everything the harness needs to score one question, plus enough of
    the intermediate resolution to explain a real failure honestly rather
    than silently."""

    model_config = ConfigDict(extra="forbid")

    succeeded: bool
    failure_stage: str | None = None
    failure_reason: str | None = None

    resolved_question: str | None = None
    actual_intent: IntentLabel | None = None
    unmapped_terms: list[str] = Field(default_factory=list)

    final_columns: list[str] = Field(default_factory=list)
    final_rows: list[dict[str, Any]] = Field(default_factory=list)
    final_row_count: int = 0

    chart: dict[str, Any] | None = None
    anomalies: list[dict[str, Any]] = Field(default_factory=list)

    narrative: str | None = None
    narrative_errors: list[str] = Field(default_factory=list)

    follow_up_suggestions: list[str] = Field(default_factory=list)


def _alias_for(column: Any) -> str:
    """Replicate SQL Generation's own real aggregation-alias rule (see
    `sql_generation.agent._aggregation_function`/`_generate_statements`):
    a `role="measure"` column becomes `{column_name}_TOTAL` in the real
    SELECT list; a `role="dimension"`/`"filter"` column keeps its bare
    name. See `LIMITATIONS.md` item 28 for the structural gap this
    manual replication documents."""

    return f"{column.column_name}_TOTAL" if column.role == "measure" else column.column_name


async def run_full_pipeline(
    *,
    question: str,
    tenant_id: str,
    data_source_id: str,
    catalog_session_factory: sessionmaker[Session],
    neo4j_client: Neo4jClient,
    opa_client: OpaClient,
    llm_client: LLMClient,
    trace_id: str,
    user_id: str = "eval-harness",
) -> PipelineResult:
    """Run `question` through the entire real pipeline for real, using
    `llm_client` for every LLM-backed step. Never raises -- any real
    failure at any stage is reported via `PipelineResult.succeeded=False`.
    """

    request_context = RequestContext(
        tenant_id=tenant_id,
        user_id=user_id,
        trace_id=trace_id,
        roles=["analyst"],
        claims={"tenant_id": tenant_id},
    )

    # ================== Understanding domain ==================

    conversation_agent = ConversationAgent(llm_client=llm_client)
    conversation_output = await conversation_agent.run(
        ConversationInput(
            request_context=request_context,
            payload=ConversationPayload(question=question, conversation_history=[]),
        )
    )
    resolved_question = conversation_output.result.resolved_question

    intent_agent = IntentUnderstandingAgent(llm_client=llm_client)
    intent_output = await intent_agent.run(
        IntentUnderstandingInput(
            request_context=request_context,
            payload=IntentUnderstandingPayload(question=resolved_question),
        )
    )
    if intent_output.errors and any(not e.recoverable for e in intent_output.errors):
        return PipelineResult(
            succeeded=False,
            failure_stage="understanding.intent_understanding",
            failure_reason=str(intent_output.errors),
            resolved_question=resolved_question,
        )
    actual_intent = intent_output.result.intent

    metadata_discovery_agent = MetadataDiscoveryAgent(session_factory=catalog_session_factory)
    metadata_output = await metadata_discovery_agent.run(
        MetadataDiscoveryInput(
            request_context=request_context,
            payload=MetadataDiscoveryPayload(data_source_id=data_source_id),
        )
    )
    if metadata_output.errors:
        return PipelineResult(
            succeeded=False,
            failure_stage="understanding.metadata_discovery",
            failure_reason=str(metadata_output.errors),
            resolved_question=resolved_question,
            actual_intent=actual_intent,
        )
    catalog_columns = metadata_output.result.columns

    ontology_agent = OntologyAgent(client=neo4j_client)
    ontology_output = await ontology_agent.run(
        OntologyInput(
            request_context=request_context,
            payload=OntologyPayload(
                entities=intent_output.result.entities, intent=actual_intent
            ),
        )
    )
    ontology_result = ontology_output.result

    retrieval_candidates = [
        RetrievalCandidate(
            catalog_column_id=c.catalog_column_id,
            table_name=c.table_name,
            column_name=c.column_name,
            business_name=c.business_name,
            synonyms=c.synonyms,
            description=c.description,
        )
        for c in catalog_columns
    ]
    semantic_retrieval_agent = SemanticRetrievalAgent(llm_client=llm_client)
    retrieval_output = await semantic_retrieval_agent.run(
        SemanticRetrievalInput(
            request_context=request_context,
            payload=SemanticRetrievalPayload(
                question=resolved_question,
                unresolved_terms=ontology_result.unresolved_terms,
                candidates=retrieval_candidates,
            ),
        )
    )

    schema_mapping_agent = SchemaMappingAgent()
    schema_mapping_output = await schema_mapping_agent.run(
        SchemaMappingInput(
            request_context=request_context,
            payload=SchemaMappingPayload(
                intent=actual_intent,
                concept_resolutions=[
                    ConceptResolution(**r.model_dump())
                    for r in ontology_result.concept_resolutions
                ],
                relationship_resolutions=[
                    RelationshipResolution(**r.model_dump())
                    for r in ontology_result.relationship_resolutions
                ],
                semantic_matches=[
                    TermMatch(**m.model_dump()) for m in retrieval_output.result.matches
                ],
                catalog_inventory=[
                    CatalogInventoryEntry(**c.model_dump()) for c in catalog_columns
                ],
            ),
        )
    )
    schema_mapping_result = schema_mapping_output.result

    if not schema_mapping_result.tables:
        # A real, honest hard failure: nothing resolved at all, no SQL is
        # possible -- unlike a partial resolution (some unmapped_terms but
        # at least one usable table), there is nothing left to attempt.
        return PipelineResult(
            succeeded=False,
            failure_stage="understanding.schema_mapping",
            failure_reason=(
                f"no tables resolved at all; unmapped_terms={schema_mapping_result.unmapped_terms}"
            ),
            resolved_question=resolved_question,
            actual_intent=actual_intent,
            unmapped_terms=schema_mapping_result.unmapped_terms,
        )

    # ================== Query domain ==================

    data_source_discovery_agent = DataSourceDiscoveryAgent(
        session_factory=catalog_session_factory
    )
    discovery_output = await data_source_discovery_agent.run(
        DataSourceDiscoveryInput(
            request_context=request_context,
            payload=DataSourceDiscoveryPayload(tables=schema_mapping_result.tables),
        )
    )
    if discovery_output.errors:
        return PipelineResult(
            succeeded=False,
            failure_stage="query.data_source_discovery",
            failure_reason=str(discovery_output.errors),
            resolved_question=resolved_question,
            actual_intent=actual_intent,
            unmapped_terms=schema_mapping_result.unmapped_terms,
        )
    resolved_data_sources = [
        GenerationResolvedDataSource(
            table_name=r.table_name,
            data_source_id=r.data_source_id,
            source_type=r.source_type,
            reachable=r.reachable,
        )
        for r in discovery_output.result.resolved
    ]

    generation_schema_mapping = GenerationSchemaMappingResult(
        tables=schema_mapping_result.tables,
        columns=[
            GenerationResolvedColumnRef(**c.model_dump()) for c in schema_mapping_result.columns
        ],
        joins=[GenerationJoinSpec(**j.model_dump()) for j in schema_mapping_result.joins],
        unmapped_terms=schema_mapping_result.unmapped_terms,
    )
    sql_generation_agent = SqlGenerationAgent(llm_client=llm_client)
    generation_output = await sql_generation_agent.run(
        SqlGenerationInput(
            request_context=request_context,
            payload=SqlGenerationPayload(
                original_question=resolved_question,
                intent=actual_intent,
                schema_mapping=generation_schema_mapping,
                resolved_data_sources=resolved_data_sources,
            ),
        )
    )
    if generation_output.errors or not generation_output.result.statements:
        return PipelineResult(
            succeeded=False,
            failure_stage="query.sql_generation",
            failure_reason=str(generation_output.errors) or "no statements generated",
            resolved_question=resolved_question,
            actual_intent=actual_intent,
            unmapped_terms=schema_mapping_result.unmapped_terms,
        )
    real_statement = generation_output.result.statements[0]

    # ================== Guardrail domain (real gates) ==================

    schema_constraint_validator_agent = SchemaConstraintValidatorAgent(
        session_factory=catalog_session_factory
    )
    constraint_output = await schema_constraint_validator_agent.run(
        SchemaConstraintValidatorInput(
            request_context=request_context,
            payload=SchemaConstraintValidatorPayload(
                statements=[ConstraintGeneratedSql(**real_statement.model_dump())]
            ),
        )
    )
    if constraint_output.result.rejected:
        return PipelineResult(
            succeeded=False,
            failure_stage="guardrail.schema_constraint_validator",
            failure_reason=str(constraint_output.result.rejected),
            resolved_question=resolved_question,
            actual_intent=actual_intent,
        )

    pii_checker_agent = PiiExposureCheckerAgent(session_factory=catalog_session_factory)
    pii_output = await pii_checker_agent.run(
        PiiExposureCheckerInput(
            request_context=request_context,
            payload=PiiExposureCheckerPayload(
                statements=[PiiGeneratedSql(**real_statement.model_dump())]
            ),
        )
    )
    if pii_output.result.rejected:
        return PipelineResult(
            succeeded=False,
            failure_stage="guardrail.pii_exposure_checker",
            failure_reason=str(pii_output.result.rejected),
            resolved_question=resolved_question,
            actual_intent=actual_intent,
        )

    policy_authorization_agent = PolicyAuthorizationAgent(opa_client=opa_client)
    authorization_output = await policy_authorization_agent.run(
        PolicyAuthorizationInput(
            request_context=request_context,
            payload=PolicyAuthorizationPayload(
                statements=[PolicyGeneratedSql(**real_statement.model_dump())],
                intent=actual_intent,
            ),
        )
    )
    if authorization_output.result.rejected:
        return PipelineResult(
            succeeded=False,
            failure_stage="guardrail.policy_authorization",
            failure_reason=str(authorization_output.result.rejected),
            resolved_question=resolved_question,
            actual_intent=actual_intent,
        )

    sql_optimization_agent = SqlOptimizationAgent()
    optimization_output = await sql_optimization_agent.run(
        SqlOptimizationInput(
            request_context=request_context,
            payload=SqlOptimizationPayload(
                statements=[OptimizationGeneratedSql(**real_statement.model_dump())],
                tenant_id=request_context.tenant_id,
                trace_id=request_context.trace_id,
            ),
        )
    )
    optimized_statement = optimization_output.result.statements[0]

    query_cost_estimator_agent = QueryCostEstimatorAgent()
    cost_output = await query_cost_estimator_agent.run(
        QueryCostEstimatorInput(
            request_context=request_context,
            payload=QueryCostEstimatorPayload(
                statements=[CostOptimizedSql(**optimized_statement.model_dump())]
            ),
        )
    )
    if cost_output.result.rejected or not cost_output.result.approved:
        return PipelineResult(
            succeeded=False,
            failure_stage="guardrail.query_cost_estimator",
            failure_reason=str(cost_output.result.rejected),
            resolved_question=resolved_question,
            actual_intent=actual_intent,
        )

    execution_planning_agent = ExecutionPlanningAgent()
    planning_output = await execution_planning_agent.run(
        ExecutionPlanningInput(
            request_context=request_context,
            payload=ExecutionPlanningPayload(
                statements=[
                    PlanningOptimizedSql(**cost_output.result.approved[0].model_dump())
                ]
            ),
        )
    )
    if planning_output.result.rejected or not planning_output.result.plans:
        return PipelineResult(
            succeeded=False,
            failure_stage="query.execution_planning",
            failure_reason=str(planning_output.result.rejected),
            resolved_question=resolved_question,
            actual_intent=actual_intent,
        )
    real_plan = planning_output.result.plans[0]

    # ================== Data Federation: real Snowflake execution ==================

    data_federation_agent = DataFederationAgent(catalog_session_factory=catalog_session_factory)
    federation_output = await data_federation_agent.run(
        DataFederationInput(
            request_context=request_context,
            payload=DataFederationPayload(
                plans=[FederationExecutionPlan(**real_plan.model_dump())]
            ),
        )
    )
    if federation_output.errors:
        return PipelineResult(
            succeeded=False,
            failure_stage="query.data_federation",
            failure_reason=str(federation_output.errors),
            resolved_question=resolved_question,
            actual_intent=actual_intent,
        )
    federation_result = federation_output.result

    # ================== Manual result_alias threading (LIMITATIONS.md item 28) ==================

    chart_columns = [
        ChartColumnRef(
            term=c.term,
            catalog_column_id=c.catalog_column_id,
            table_name=c.table_name,
            column_name=c.column_name,
            data_type=c.data_type,
            role=c.role,
            result_alias=_alias_for(c),
        )
        for c in schema_mapping_result.columns
    ]

    # ================== Insight domain ==================

    chart_selection_agent = ChartSelectionAgent()
    chart_output = await chart_selection_agent.run(
        ChartSelectionInput(
            request_context=request_context,
            payload=ChartSelectionPayload(
                final_columns=federation_result.final_columns,
                final_rows=federation_result.final_rows,
                final_row_count=federation_result.final_row_count,
                columns=chart_columns,
            ),
        )
    )
    chart = chart_output.result.chart

    anomaly_agent = AnomalyOutlierHighlighterAgent()
    anomaly_output = await anomaly_agent.run(
        AnomalyDetectionInput(
            request_context=request_context,
            payload=AnomalyDetectionPayload(
                final_columns=federation_result.final_columns,
                final_rows=federation_result.final_rows,
                final_row_count=federation_result.final_row_count,
                chart=AnomalyChartSpec(**chart.model_dump()),
            ),
        )
    )
    anomaly_result = anomaly_output.result

    narrative_agent = GroundedNarrativeGenerationAgent(llm_client=llm_client)
    narrative_output = await narrative_agent.run(
        NarrativeGenerationInput(
            request_context=request_context,
            payload=NarrativeGenerationPayload(
                original_question=resolved_question,
                final_columns=federation_result.final_columns,
                final_rows=federation_result.final_rows,
                final_row_count=federation_result.final_row_count,
                chart=NarrativeChartSpec(**chart.model_dump()),
                anomalies=[
                    NarrativeAnomalyFinding(**a.model_dump()) for a in anomaly_result.anomalies
                ],
            ),
        )
    )

    follow_up_agent = FollowUpSuggestionAgent(llm_client=llm_client)
    follow_up_output = await follow_up_agent.run(
        FollowUpSuggestionInput(
            request_context=request_context,
            payload=FollowUpSuggestionPayload(
                original_question=resolved_question,
                narrative=narrative_output.result.narrative,
                final_columns=federation_result.final_columns,
                final_row_count=federation_result.final_row_count,
                chart=FollowUpChartSpec(**chart.model_dump()),
                anomalies=[
                    FollowUpAnomalyFinding(**a.model_dump()) for a in anomaly_result.anomalies
                ],
            ),
        )
    )

    return PipelineResult(
        succeeded=True,
        resolved_question=resolved_question,
        actual_intent=actual_intent,
        unmapped_terms=schema_mapping_result.unmapped_terms,
        final_columns=federation_result.final_columns,
        final_rows=federation_result.final_rows,
        final_row_count=federation_result.final_row_count,
        chart=chart.model_dump(),
        anomalies=[a.model_dump() for a in anomaly_result.anomalies],
        narrative=narrative_output.result.narrative,
        narrative_errors=[e.code for e in narrative_output.errors],
        follow_up_suggestions=[s.question for s in follow_up_output.result.suggestions],
    )


def real_catalog_session_scope(session_factory: sessionmaker[Session]):
    """Thin re-export of `navigraph_catalog.db.session_scope` for callers
    (e.g. `run_harness.py`) that only import from `eval.pipeline_chain`."""

    return session_scope(session_factory)
