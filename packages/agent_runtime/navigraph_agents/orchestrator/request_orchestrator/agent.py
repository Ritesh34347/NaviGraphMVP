"""Request Orchestrator agent implementation.

THE real Orchestrator: formalizes and REPLACES
`eval/pipeline_chain.py::run_full_pipeline` (deleted in this same phase --
confirmed via grep that nothing else imported it), which had already
proven this exact 19-call sequence correct end-to-end against a real
Snowflake account and a real Anthropic model. Two parallel copies of a
19-agent sequence would be a real, demonstrated drift risk in this
codebase (see the documentation-staleness findings logged in earlier
phases) -- every future contract change to any of the 19 agents would
otherwise need applying twice. This agent is the single, real, canonical
implementation; `eval/run_harness.py` now calls it directly instead of the
retired standalone helper.

NO LANGGRAPH (a deliberate, user-confirmed reversal of Phase 1's original
DECISIONS.md entry -- see that file's Phase 9 entry for the full
reasoning): 8 phases and ~22 real agents were built with zero real need
for graph-checkpointing/resumability ever emerging, and this exact
direct-async-call sequence was already proven correct by
`run_full_pipeline`. This agent is a plain Python class, not a graph.

This agent closes three real gaps `run_full_pipeline` deliberately left
open (documented in its own module docstring and in LIMITATIONS.md):

1. **Real lineage recording, threaded through every stage.** Constructs
   its own `LineageRecorderAgent` and calls it once, immediately after
   every real upstream agent's own output, passing that agent's own
   `lineage_events` -- the exact incremental-append design
   `ops.lineage_recorder` was built around. A lineage-recording failure
   is logged (via a recoverable `AgentError`) but never aborts the
   request -- lineage is an audit side-channel, not a correctness gate.
2. **Real `data_source_id` auto-resolution.** Every existing
   integration test/harness hardcodes a known data source name
   (`"fidelity_poc_snowflake_v2"`); this agent instead resolves it for
   real from `request_context.tenant_id` via
   `navigraph_catalog.api.list_data_sources` when the caller omits one --
   exactly one match is used; zero or more than one is a real, structured
   `outcome="failed"` (never a silent guess).
3. **Real Multi-turn Clarification, invoked on schema-resolution failure.**
   When Schema Mapping resolves ZERO tables (the exact real failure mode
   Phase 8's evaluation harness hit twice, `gq_007`/`gq_010` --
   LIMITATIONS.md item 38), this agent calls
   `ClarificationCoordinatorAgent` and returns a real
   `outcome="needs_clarification"` with a real clarifying question,
   instead of a bare pipeline failure.

Also threads real session/conversation persistence through
`SessionContextManagerAgent` (Redis-backed): reads any existing
conversation history before Conversation Agent runs, and appends the new
real turn after Intent Understanding classifies it -- regardless of which
`outcome` the request ends in, so even a failed/clarification-needed turn
is recorded for the next real request in the same session.

Every stage's real failure-detection logic (which `.errors`/`.rejected`
fields to check, in which order) is ported UNCHANGED from
`run_full_pipeline` -- this is a direct, low-risk port of already-proven
logic, not a redesign. The `result_alias` manual-threading step
(`_alias_for`) is likewise ported verbatim from `run_full_pipeline`,
centralizing it in exactly one real, canonical place instead of every
caller replicating it by hand (this PARTIALLY resolves LIMITATIONS.md item
28 -- no contract field structurally carries the alias yet, but there is
now only one real place that manually threads it, not N).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from navigraph_catalog.api import list_data_sources
from navigraph_catalog.db import session_scope as catalog_session_scope
from navigraph_federation.trino_client import TrinoClient
from navigraph_kg.client import Neo4jClient
from navigraph_shared.contracts import (
    AgentError,
    AgentMetadata,
    LineageEvent,
    RequestContext,
)
from navigraph_shared.llm import LLMClient
from navigraph_shared.opa import OpaClient
from navigraph_shared.telemetry import (
    configure_logging,
    get_tracer,
    record_agent_error,
    record_agent_invocation,
)
from opentelemetry.trace import Span, Tracer
from sqlalchemy.orm import Session, sessionmaker

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
from navigraph_agents.ops.lineage_recorder.agent import LineageRecorderAgent
from navigraph_agents.ops.lineage_recorder.contracts import (
    LineageRecorderInput,
    LineageRecorderPayload,
)
from navigraph_agents.orchestrator.clarification_coordinator.agent import (
    ClarificationCoordinatorAgent,
)
from navigraph_agents.orchestrator.clarification_coordinator.contracts import (
    ClarificationCoordinatorInput,
    ClarificationCoordinatorPayload,
)
from navigraph_agents.orchestrator.request_orchestrator.contracts import (
    RequestOrchestratorInput,
    RequestOrchestratorOutput,
    RequestOrchestratorResult,
)
from navigraph_agents.orchestrator.session_context_manager.agent import (
    CacheClientProtocol,
    SessionContextManagerAgent,
)
from navigraph_agents.orchestrator.session_context_manager.contracts import (
    ConversationTurn as SessionConversationTurn,
)
from navigraph_agents.orchestrator.session_context_manager.contracts import (
    SessionContextManagerInput,
    SessionContextManagerPayload,
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
from navigraph_agents.understanding.conversation.contracts import (
    ConversationTurn as UnderstandingConversationTurn,
)
from navigraph_agents.understanding.intent_understanding.agent import (
    IntentUnderstandingAgent,
)
from navigraph_agents.understanding.intent_understanding.contracts import (
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

AGENT_NAME = "orchestrator.request_orchestrator"

logger = configure_logging("navigraph-agent-runtime")


def _alias_for(column: Any) -> str:
    """Replicate SQL Generation's own real aggregation-alias rule (ported
    verbatim from `eval/pipeline_chain.py`'s identical helper -- see
    `sql_generation.agent._aggregation_function`/`_generate_statements`):
    a `role="measure"` column becomes `{column_name}_TOTAL` in the real
    SELECT list; a `role="dimension"`/`"filter"` column keeps its bare
    name. See LIMITATIONS.md item 28 for the structural gap this manual
    replication documents -- now centralized here, the one real caller,
    instead of duplicated per test/harness."""

    return f"{column.column_name}_TOTAL" if column.role == "measure" else column.column_name


class RequestOrchestratorAgent:
    """The real Request Orchestrator: constructs every real sub-agent once,
    then threads one question through the entire real pipeline for real."""

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        catalog_session_factory: sessionmaker[Session],
        lineage_session_factory: sessionmaker[Session],
        neo4j_client: Neo4jClient,
        opa_client: OpaClient,
        cache_client: CacheClientProtocol,
        trino_client: TrinoClient | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._catalog_session_factory = catalog_session_factory
        self._tracer = tracer or get_tracer("navigraph-agent-runtime")

        # Understanding domain
        self._conversation_agent = ConversationAgent(llm_client=llm_client, tracer=tracer)
        self._intent_agent = IntentUnderstandingAgent(llm_client=llm_client, tracer=tracer)
        self._metadata_discovery_agent = MetadataDiscoveryAgent(
            session_factory=catalog_session_factory, tracer=tracer
        )
        self._ontology_agent = OntologyAgent(client=neo4j_client, tracer=tracer)
        self._semantic_retrieval_agent = SemanticRetrievalAgent(
            llm_client=llm_client, tracer=tracer
        )
        self._schema_mapping_agent = SchemaMappingAgent(tracer=tracer)

        # Query domain
        self._data_source_discovery_agent = DataSourceDiscoveryAgent(
            session_factory=catalog_session_factory, tracer=tracer
        )
        self._sql_generation_agent = SqlGenerationAgent(llm_client=llm_client, tracer=tracer)
        self._sql_optimization_agent = SqlOptimizationAgent(tracer=tracer)
        self._execution_planning_agent = ExecutionPlanningAgent(tracer=tracer)
        self._data_federation_agent = DataFederationAgent(
            catalog_session_factory=catalog_session_factory,
            trino_client=trino_client,
            tracer=tracer,
        )

        # Guardrail domain
        self._schema_constraint_validator_agent = SchemaConstraintValidatorAgent(
            session_factory=catalog_session_factory, tracer=tracer
        )
        self._pii_exposure_checker_agent = PiiExposureCheckerAgent(
            session_factory=catalog_session_factory, tracer=tracer
        )
        self._policy_authorization_agent = PolicyAuthorizationAgent(
            opa_client=opa_client, tracer=tracer
        )
        self._query_cost_estimator_agent = QueryCostEstimatorAgent(tracer=tracer)

        # Insight domain
        self._chart_selection_agent = ChartSelectionAgent(tracer=tracer)
        self._anomaly_agent = AnomalyOutlierHighlighterAgent(tracer=tracer)
        self._narrative_agent = GroundedNarrativeGenerationAgent(
            llm_client=llm_client, tracer=tracer
        )
        self._follow_up_agent = FollowUpSuggestionAgent(llm_client=llm_client, tracer=tracer)

        # Ops domain
        self._lineage_recorder_agent = LineageRecorderAgent(
            session_factory=lineage_session_factory, tracer=tracer
        )

        # Orchestrator domain
        self._session_context_manager_agent = SessionContextManagerAgent(
            cache_client=cache_client, tracer=tracer
        )
        self._clarification_coordinator_agent = ClarificationCoordinatorAgent(
            llm_client=llm_client, tracer=tracer
        )

    async def _record_lineage(
        self, request_context: RequestContext, lineage_events: list[LineageEvent]
    ) -> None:
        """Persist one upstream agent's real `lineage_events`. Never lets a
        lineage-recording failure abort the request -- lineage is an audit
        side-channel, not a correctness gate (a genuine, logged behavioral
        choice, see this module's docstring)."""

        if not lineage_events:
            return
        try:
            await self._lineage_recorder_agent.run(
                LineageRecorderInput(
                    request_context=request_context,
                    payload=LineageRecorderPayload(events=lineage_events),
                )
            )
        except Exception:
            logger.warning(
                "Lineage recording failed for trace_id=%r -- request continues, audit "
                "trail for this stage is incomplete",
                request_context.trace_id,
                exc_info=True,
            )

    def _resolve_data_source_id(self, *, tenant_id: str, requested: str | None) -> str | None:
        """Real `data_source_id` resolution: use the caller-supplied one if
        given; otherwise resolve from `tenant_id` via
        `navigraph_catalog.api.list_data_sources` -- exactly one match is
        used, zero or more than one returns `None` (the caller turns this
        into a structured failure). Never guesses."""

        if requested is not None:
            return requested

        with catalog_session_scope(self._catalog_session_factory) as session:
            data_sources = list_data_sources(session, tenant_id=tenant_id)

        if len(data_sources) == 1:
            return str(data_sources[0].id)
        return None

    async def run(self, input: RequestOrchestratorInput) -> RequestOrchestratorOutput:
        start = time.perf_counter()
        request_context = input.request_context
        payload = input.payload
        errors: list[AgentError] = []

        session_id = payload.session_id or f"sess_{uuid.uuid4().hex}"

        with self._tracer.start_as_current_span("agent.request_orchestrator.run") as span:
            span.set_attribute("navigraph.tenant_id", request_context.tenant_id)
            span.set_attribute("navigraph.trace_id", request_context.trace_id)
            span.set_attribute("navigraph.agent_name", AGENT_NAME)
            span.set_attribute("navigraph.session_id", session_id)

            # ================== Session read ==================

            session_get_output = await self._session_context_manager_agent.run(
                SessionContextManagerInput(
                    request_context=request_context,
                    payload=SessionContextManagerPayload(session_id=session_id, operation="get"),
                )
            )
            await self._record_lineage(request_context, session_get_output.lineage_events)
            errors.extend(session_get_output.errors)
            conversation_history = [
                UnderstandingConversationTurn(**turn.model_dump())
                for turn in session_get_output.result.conversation_history
            ]

            # ================== data_source_id resolution ==================

            data_source_id = self._resolve_data_source_id(
                tenant_id=request_context.tenant_id, requested=payload.data_source_id
            )
            if data_source_id is None:
                return self._finish(
                    start=start,
                    request_context=request_context,
                    errors=errors,
                    result=RequestOrchestratorResult(
                        outcome="failed",
                        session_id=session_id,
                        failure_stage="orchestrator.data_source_resolution",
                        failure_reason=(
                            f"tenant {request_context.tenant_id!r} has zero or more than "
                            "one registered data source -- a specific data_source_id must "
                            "be supplied"
                        ),
                    ),
                    span=span,
                )

            # ================== Understanding domain ==================

            conversation_output = await self._conversation_agent.run(
                ConversationInput(
                    request_context=request_context,
                    payload=ConversationPayload(
                        question=payload.question, conversation_history=conversation_history
                    ),
                )
            )
            await self._record_lineage(request_context, conversation_output.lineage_events)
            resolved_question = conversation_output.result.resolved_question

            intent_output = await self._intent_agent.run(
                IntentUnderstandingInput(
                    request_context=request_context,
                    payload=IntentUnderstandingPayload(question=resolved_question),
                )
            )
            await self._record_lineage(request_context, intent_output.lineage_events)
            if intent_output.errors and any(not e.recoverable for e in intent_output.errors):
                return self._finish(
                    start=start,
                    request_context=request_context,
                    errors=errors + intent_output.errors,
                    result=RequestOrchestratorResult(
                        outcome="failed",
                        session_id=session_id,
                        resolved_question=resolved_question,
                        failure_stage="understanding.intent_understanding",
                        failure_reason=str(intent_output.errors),
                    ),
                    span=span,
                )
            actual_intent = intent_output.result.intent

            metadata_output = await self._metadata_discovery_agent.run(
                MetadataDiscoveryInput(
                    request_context=request_context,
                    payload=MetadataDiscoveryPayload(data_source_id=data_source_id),
                )
            )
            await self._record_lineage(request_context, metadata_output.lineage_events)
            if metadata_output.errors:
                return self._finish(
                    start=start,
                    request_context=request_context,
                    errors=errors + metadata_output.errors,
                    result=RequestOrchestratorResult(
                        outcome="failed",
                        session_id=session_id,
                        resolved_question=resolved_question,
                        actual_intent=actual_intent,
                        failure_stage="understanding.metadata_discovery",
                        failure_reason=str(metadata_output.errors),
                    ),
                    span=span,
                )
            catalog_columns = metadata_output.result.columns

            ontology_output = await self._ontology_agent.run(
                OntologyInput(
                    request_context=request_context,
                    payload=OntologyPayload(
                        entities=intent_output.result.entities, intent=actual_intent
                    ),
                )
            )
            await self._record_lineage(request_context, ontology_output.lineage_events)
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
            retrieval_output = await self._semantic_retrieval_agent.run(
                SemanticRetrievalInput(
                    request_context=request_context,
                    payload=SemanticRetrievalPayload(
                        question=resolved_question,
                        unresolved_terms=ontology_result.unresolved_terms,
                        candidates=retrieval_candidates,
                    ),
                )
            )
            await self._record_lineage(request_context, retrieval_output.lineage_events)

            schema_mapping_output = await self._schema_mapping_agent.run(
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
            await self._record_lineage(request_context, schema_mapping_output.lineage_events)
            schema_mapping_result = schema_mapping_output.result

            new_turn = SessionConversationTurn(
                turn_id=request_context.trace_id,
                raw_question=payload.question,
                resolved_question=resolved_question,
                intent=actual_intent,
                entities=intent_output.result.entities,
            )

            if not schema_mapping_result.tables:
                # Real Multi-turn Clarification trigger -- the exact real
                # failure mode Phase 8's harness hit twice (gq_007, gq_010).
                clarification_output = await self._clarification_coordinator_agent.run(
                    ClarificationCoordinatorInput(
                        request_context=request_context,
                        payload=ClarificationCoordinatorPayload(
                            original_question=resolved_question,
                            failed_stage="understanding.schema_mapping",
                            failure_reason=(
                                f"no tables resolved at all; "
                                f"unmapped_terms={schema_mapping_result.unmapped_terms}"
                            ),
                            unmapped_terms=schema_mapping_result.unmapped_terms,
                        ),
                    )
                )
                await self._record_lineage(
                    request_context, clarification_output.lineage_events
                )
                await self._append_turn(request_context, session_id, new_turn)
                return self._finish(
                    start=start,
                    request_context=request_context,
                    errors=errors + clarification_output.errors,
                    result=RequestOrchestratorResult(
                        outcome="needs_clarification",
                        session_id=session_id,
                        resolved_question=resolved_question,
                        actual_intent=actual_intent,
                        unmapped_terms=schema_mapping_result.unmapped_terms,
                        clarifying_question=clarification_output.result.clarifying_question,
                    ),
                    span=span,
                )

            # ================== Query domain ==================

            discovery_output = await self._data_source_discovery_agent.run(
                DataSourceDiscoveryInput(
                    request_context=request_context,
                    payload=DataSourceDiscoveryPayload(tables=schema_mapping_result.tables),
                )
            )
            await self._record_lineage(request_context, discovery_output.lineage_events)
            if discovery_output.errors:
                await self._append_turn(request_context, session_id, new_turn)
                return self._finish(
                    start=start,
                    request_context=request_context,
                    errors=errors + discovery_output.errors,
                    result=RequestOrchestratorResult(
                        outcome="failed",
                        session_id=session_id,
                        resolved_question=resolved_question,
                        actual_intent=actual_intent,
                        unmapped_terms=schema_mapping_result.unmapped_terms,
                        failure_stage="query.data_source_discovery",
                        failure_reason=str(discovery_output.errors),
                    ),
                    span=span,
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
                    GenerationResolvedColumnRef(**c.model_dump())
                    for c in schema_mapping_result.columns
                ],
                joins=[
                    GenerationJoinSpec(**j.model_dump()) for j in schema_mapping_result.joins
                ],
                unmapped_terms=schema_mapping_result.unmapped_terms,
            )
            generation_output = await self._sql_generation_agent.run(
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
            await self._record_lineage(request_context, generation_output.lineage_events)
            if generation_output.errors or not generation_output.result.statements:
                await self._append_turn(request_context, session_id, new_turn)
                return self._finish(
                    start=start,
                    request_context=request_context,
                    errors=errors + generation_output.errors,
                    result=RequestOrchestratorResult(
                        outcome="failed",
                        session_id=session_id,
                        resolved_question=resolved_question,
                        actual_intent=actual_intent,
                        unmapped_terms=schema_mapping_result.unmapped_terms,
                        failure_stage="query.sql_generation",
                        failure_reason=(
                            str(generation_output.errors) or "no statements generated"
                        ),
                    ),
                    span=span,
                )
            real_statement = generation_output.result.statements[0]

            # ================== Guardrail domain ==================

            constraint_output = await self._schema_constraint_validator_agent.run(
                SchemaConstraintValidatorInput(
                    request_context=request_context,
                    payload=SchemaConstraintValidatorPayload(
                        statements=[ConstraintGeneratedSql(**real_statement.model_dump())]
                    ),
                )
            )
            await self._record_lineage(request_context, constraint_output.lineage_events)
            if constraint_output.result.rejected:
                await self._append_turn(request_context, session_id, new_turn)
                return self._finish(
                    start=start,
                    request_context=request_context,
                    errors=errors,
                    result=RequestOrchestratorResult(
                        outcome="failed",
                        session_id=session_id,
                        resolved_question=resolved_question,
                        actual_intent=actual_intent,
                        failure_stage="guardrail.schema_constraint_validator",
                        failure_reason=str(constraint_output.result.rejected),
                    ),
                    span=span,
                )

            pii_output = await self._pii_exposure_checker_agent.run(
                PiiExposureCheckerInput(
                    request_context=request_context,
                    payload=PiiExposureCheckerPayload(
                        statements=[PiiGeneratedSql(**real_statement.model_dump())]
                    ),
                )
            )
            await self._record_lineage(request_context, pii_output.lineage_events)
            if pii_output.result.rejected:
                await self._append_turn(request_context, session_id, new_turn)
                return self._finish(
                    start=start,
                    request_context=request_context,
                    errors=errors,
                    result=RequestOrchestratorResult(
                        outcome="failed",
                        session_id=session_id,
                        resolved_question=resolved_question,
                        actual_intent=actual_intent,
                        failure_stage="guardrail.pii_exposure_checker",
                        failure_reason=str(pii_output.result.rejected),
                    ),
                    span=span,
                )

            authorization_output = await self._policy_authorization_agent.run(
                PolicyAuthorizationInput(
                    request_context=request_context,
                    payload=PolicyAuthorizationPayload(
                        statements=[PolicyGeneratedSql(**real_statement.model_dump())],
                        intent=actual_intent,
                    ),
                )
            )
            await self._record_lineage(request_context, authorization_output.lineage_events)
            if authorization_output.result.rejected:
                await self._append_turn(request_context, session_id, new_turn)
                return self._finish(
                    start=start,
                    request_context=request_context,
                    errors=errors,
                    result=RequestOrchestratorResult(
                        outcome="failed",
                        session_id=session_id,
                        resolved_question=resolved_question,
                        actual_intent=actual_intent,
                        failure_stage="guardrail.policy_authorization",
                        failure_reason=str(authorization_output.result.rejected),
                    ),
                    span=span,
                )

            optimization_output = await self._sql_optimization_agent.run(
                SqlOptimizationInput(
                    request_context=request_context,
                    payload=SqlOptimizationPayload(
                        statements=[OptimizationGeneratedSql(**real_statement.model_dump())],
                        tenant_id=request_context.tenant_id,
                        trace_id=request_context.trace_id,
                    ),
                )
            )
            await self._record_lineage(request_context, optimization_output.lineage_events)
            optimized_statement = optimization_output.result.statements[0]

            cost_output = await self._query_cost_estimator_agent.run(
                QueryCostEstimatorInput(
                    request_context=request_context,
                    payload=QueryCostEstimatorPayload(
                        statements=[CostOptimizedSql(**optimized_statement.model_dump())]
                    ),
                )
            )
            await self._record_lineage(request_context, cost_output.lineage_events)
            if cost_output.result.rejected or not cost_output.result.approved:
                await self._append_turn(request_context, session_id, new_turn)
                return self._finish(
                    start=start,
                    request_context=request_context,
                    errors=errors,
                    result=RequestOrchestratorResult(
                        outcome="failed",
                        session_id=session_id,
                        resolved_question=resolved_question,
                        actual_intent=actual_intent,
                        failure_stage="guardrail.query_cost_estimator",
                        failure_reason=str(cost_output.result.rejected),
                    ),
                    span=span,
                )

            planning_output = await self._execution_planning_agent.run(
                ExecutionPlanningInput(
                    request_context=request_context,
                    payload=ExecutionPlanningPayload(
                        statements=[
                            PlanningOptimizedSql(**cost_output.result.approved[0].model_dump())
                        ]
                    ),
                )
            )
            await self._record_lineage(request_context, planning_output.lineage_events)
            if planning_output.result.rejected or not planning_output.result.plans:
                await self._append_turn(request_context, session_id, new_turn)
                return self._finish(
                    start=start,
                    request_context=request_context,
                    errors=errors,
                    result=RequestOrchestratorResult(
                        outcome="failed",
                        session_id=session_id,
                        resolved_question=resolved_question,
                        actual_intent=actual_intent,
                        failure_stage="query.execution_planning",
                        failure_reason=str(planning_output.result.rejected),
                    ),
                    span=span,
                )
            real_plan = planning_output.result.plans[0]

            # ================== Data Federation ==================

            federation_output = await self._data_federation_agent.run(
                DataFederationInput(
                    request_context=request_context,
                    payload=DataFederationPayload(
                        plans=[FederationExecutionPlan(**real_plan.model_dump())]
                    ),
                )
            )
            await self._record_lineage(request_context, federation_output.lineage_events)
            if federation_output.errors:
                await self._append_turn(request_context, session_id, new_turn)
                return self._finish(
                    start=start,
                    request_context=request_context,
                    errors=errors + federation_output.errors,
                    result=RequestOrchestratorResult(
                        outcome="failed",
                        session_id=session_id,
                        resolved_question=resolved_question,
                        actual_intent=actual_intent,
                        failure_stage="query.data_federation",
                        failure_reason=str(federation_output.errors),
                    ),
                    span=span,
                )
            federation_result = federation_output.result

            # ================== result_alias threading ==================

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

            chart_output = await self._chart_selection_agent.run(
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
            await self._record_lineage(request_context, chart_output.lineage_events)
            chart = chart_output.result.chart

            anomaly_output = await self._anomaly_agent.run(
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
            await self._record_lineage(request_context, anomaly_output.lineage_events)
            anomaly_result = anomaly_output.result

            narrative_output = await self._narrative_agent.run(
                NarrativeGenerationInput(
                    request_context=request_context,
                    payload=NarrativeGenerationPayload(
                        original_question=resolved_question,
                        final_columns=federation_result.final_columns,
                        final_rows=federation_result.final_rows,
                        final_row_count=federation_result.final_row_count,
                        chart=NarrativeChartSpec(**chart.model_dump()),
                        anomalies=[
                            NarrativeAnomalyFinding(**a.model_dump())
                            for a in anomaly_result.anomalies
                        ],
                    ),
                )
            )
            await self._record_lineage(request_context, narrative_output.lineage_events)

            follow_up_output = await self._follow_up_agent.run(
                FollowUpSuggestionInput(
                    request_context=request_context,
                    payload=FollowUpSuggestionPayload(
                        original_question=resolved_question,
                        narrative=narrative_output.result.narrative,
                        final_columns=federation_result.final_columns,
                        final_row_count=federation_result.final_row_count,
                        chart=FollowUpChartSpec(**chart.model_dump()),
                        anomalies=[
                            FollowUpAnomalyFinding(**a.model_dump())
                            for a in anomaly_result.anomalies
                        ],
                    ),
                )
            )
            await self._record_lineage(request_context, follow_up_output.lineage_events)

            await self._append_turn(request_context, session_id, new_turn)

            return self._finish(
                start=start,
                request_context=request_context,
                errors=errors,
                result=RequestOrchestratorResult(
                    outcome="answered",
                    session_id=session_id,
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
                    follow_up_suggestions=[
                        s.question for s in follow_up_output.result.suggestions
                    ],
                ),
                span=span,
            )

    async def _append_turn(
        self, request_context: RequestContext, session_id: str, new_turn: SessionConversationTurn
    ) -> None:
        """Append the real turn just processed to the real session store --
        called regardless of the final `outcome`, so even a failed or
        clarification-needed turn is recorded for the next real request in
        the same session."""

        append_output = await self._session_context_manager_agent.run(
            SessionContextManagerInput(
                request_context=request_context,
                payload=SessionContextManagerPayload(
                    session_id=session_id, operation="append_turn", new_turn=new_turn
                ),
            )
        )
        await self._record_lineage(request_context, append_output.lineage_events)

    def _finish(
        self,
        *,
        start: float,
        request_context: RequestContext,
        errors: list[AgentError],
        result: RequestOrchestratorResult,
        span: Span,
    ) -> RequestOrchestratorOutput:
        confidence = {"answered": 1.0, "needs_clarification": 0.5, "failed": 0.0}[result.outcome]

        lineage_event = LineageEvent(
            agent_name=AGENT_NAME,
            input_summary=f"session_id={result.session_id!r}",
            output_summary=(
                f"outcome={result.outcome} failure_stage={result.failure_stage!r}"
            ),
            tenant_id=request_context.tenant_id,
            trace_id=request_context.trace_id,
        )

        latency_ms = (time.perf_counter() - start) * 1000.0
        metadata = AgentMetadata(latency_ms=latency_ms)

        span.set_attribute("navigraph.outcome", result.outcome)

        record_agent_invocation(AGENT_NAME, latency_ms=latency_ms, success=result.outcome != "failed")
        for error in errors:
            record_agent_error(AGENT_NAME, error_code=error.code, recoverable=error.recoverable)

        return RequestOrchestratorOutput(
            result=result,
            confidence=confidence,
            lineage_events=[lineage_event],
            errors=errors,
            metadata=metadata,
        )
