"""Integration test: chains the full real pipeline -- Understanding, SQL
Generation, all four real Guardrail gates, SQL Optimization, Query Cost
Estimator, Execution Planning, and REAL Data Federation -- into the 4 new
Insight-domain agents, against the live docker-compose Postgres, Neo4j,
OPA, and a real Snowflake account.

REQUIRES LIVE, REACHABLE POSTGRES, NEO4J, OPA, AND A REAL SNOWFLAKE
ACCOUNT -- mirrors `tests/integration/guardrail_pipeline/test_pipeline_chain.py`'s
stance exactly: this test does NOT skip gracefully if any of these are
unreachable.

Unlike `guardrail_pipeline`, which deliberately stops short of Data
Federation (real Snowflake execution was already proven end-to-end by
`tests/integration/query_pipeline/`), this test DOES call real Data
Federation -- the Insight agents need a real `DataFederationResult` to
operate on, not a synthetic one.

Point this at the real services via the same env-var convention every
other NaviGraph integration test uses: `POSTGRES_HOST`/`POSTGRES_PORT`,
`NEO4J_URI`/`NEO4J_PASSWORD`, `OPA_URL`, and the real `SNOWFLAKE_*`
credentials.

Worked example: "What is the total transaction volume by market?" -- the
real `STAGING_TRANSACTIONS.UNITS`/`MARKETID` result, exactly as proven
real in `tests/integration/query_pipeline/`.

THE MANUAL `result_alias` THREADING GAP (see LIMITATIONS.md item 28,
demonstrated concretely here, not glossed over): no contract between SQL
Generation and Data Federation carries column role/aliasing forward, so
this test manually builds `ChartColumnRef` entries from the real
`SchemaMappingResult.columns`, replicating SQL Generation's own real
aliasing rule (`sql_generation.agent._aggregation_function`/
`_generate_statements`: a `role="measure"` column becomes
`{column_name}_TOTAL` in the real SELECT list; a `role="dimension"`
column keeps its bare name) -- exactly what a real Coordinator would need
to do until this gap is closed structurally.
"""

from __future__ import annotations

import navigraph_connectors.snowflake  # noqa: F401 -- registers "snowflake" for real find_column/etc
import pytest
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
from navigraph_catalog.api import list_data_sources
from navigraph_catalog.db import get_engine, get_session_factory, session_scope
from navigraph_catalog.settings import MetadataCatalogSettings
from navigraph_kg.client import Neo4jClient
from navigraph_kg.settings import KnowledgeGraphSettings
from navigraph_shared.contracts import RequestContext
from navigraph_shared.llm import FakeLLMClient

pytestmark = pytest.mark.postgres_integration

_TENANT_ID = "navikenz-poc"
_DATA_SOURCE_NAME = "fidelity_poc_snowflake_v2"
_QUESTION = "What is the total transaction volume by market?"


def _request_context() -> RequestContext:
    return RequestContext(
        tenant_id=_TENANT_ID,
        user_id="integration-test-user",
        trace_id="insight-pipeline-chain-test",
        roles=["analyst"],
        claims={"tenant_id": _TENANT_ID},
    )


@pytest.mark.neo4j_integration
@pytest.mark.snowflake_integration
@pytest.mark.opa_integration
@pytest.mark.asyncio
async def test_insight_pipeline_produces_a_real_chart_narrative_and_suggestions() -> None:
    catalog_settings = MetadataCatalogSettings()
    engine = get_engine(catalog_settings)
    session_factory = get_session_factory(engine)

    with session_scope(session_factory) as session:
        data_sources = list_data_sources(session, tenant_id=_TENANT_ID)
        matching = [ds for ds in data_sources if ds.name == _DATA_SOURCE_NAME]
        assert matching, f"No data source named {_DATA_SOURCE_NAME!r} for tenant {_TENANT_ID!r}"
        data_source_id = matching[0].id

    neo4j_client = Neo4jClient(KnowledgeGraphSettings())
    connectivity = neo4j_client.test_connection()
    assert connectivity.success, f"Neo4j unreachable: {connectivity.message}"

    request_context = _request_context()

    # ================== Understanding domain (real) ==================

    conversation_agent = ConversationAgent(llm_client=FakeLLMClient())
    conversation_output = await conversation_agent.run(
        ConversationInput(
            request_context=request_context,
            payload=ConversationPayload(question=_QUESTION, conversation_history=[]),
        )
    )
    resolved_question = conversation_output.result.resolved_question

    intent_llm = FakeLLMClient(
        response='{"intent": "comparison", "entities": ["units traded", "market"]}'
    )
    intent_agent = IntentUnderstandingAgent(llm_client=intent_llm)
    intent_output = await intent_agent.run(
        IntentUnderstandingInput(
            request_context=request_context,
            payload=IntentUnderstandingPayload(question=resolved_question),
        )
    )
    intent = intent_output.result.intent

    metadata_discovery_agent = MetadataDiscoveryAgent(session_factory=session_factory)
    metadata_output = await metadata_discovery_agent.run(
        MetadataDiscoveryInput(
            request_context=request_context,
            payload=MetadataDiscoveryPayload(data_source_id=str(data_source_id)),
        )
    )
    catalog_columns = metadata_output.result.columns

    market_id_entry = next(
        (
            c
            for c in catalog_columns
            if c.table_name.upper() == "STAGING_TRANSACTIONS"
            and c.column_name.upper() == "MARKETID"
        ),
        None,
    )
    assert market_id_entry is not None

    ontology_agent = OntologyAgent(client=neo4j_client)
    ontology_output = await ontology_agent.run(
        OntologyInput(
            request_context=request_context,
            payload=OntologyPayload(entities=intent_output.result.entities, intent="comparison"),
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
    retrieval_llm = FakeLLMClient(
        response=(
            '{"matches": [{"term": "market", '
            f'"catalog_column_id": "{market_id_entry.catalog_column_id}", '
            '"rationale": "MARKETID is the transaction-level market dimension"}]}'
        )
    )
    semantic_retrieval_agent = SemanticRetrievalAgent(llm_client=retrieval_llm)
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
                intent="comparison",
                concept_resolutions=[
                    ConceptResolution(**r.model_dump()) for r in ontology_result.concept_resolutions
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
    assert schema_mapping_result.unmapped_terms == []

    # ================== Query + Guardrail domains (real, happy path) ==================

    data_source_discovery_agent = DataSourceDiscoveryAgent(session_factory=session_factory)
    discovery_output = await data_source_discovery_agent.run(
        DataSourceDiscoveryInput(
            request_context=request_context,
            payload=DataSourceDiscoveryPayload(tables=schema_mapping_result.tables),
        )
    )
    assert not discovery_output.errors
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
    sql_generation_agent = SqlGenerationAgent(llm_client=FakeLLMClient())
    generation_output = await sql_generation_agent.run(
        SqlGenerationInput(
            request_context=request_context,
            payload=SqlGenerationPayload(
                original_question=resolved_question,
                intent=intent,
                schema_mapping=generation_schema_mapping,
                resolved_data_sources=resolved_data_sources,
            ),
        )
    )
    assert not generation_output.errors
    assert len(generation_output.result.statements) == 1
    real_statement = generation_output.result.statements[0]

    schema_constraint_validator_agent = SchemaConstraintValidatorAgent(
        session_factory=session_factory
    )
    constraint_output = await schema_constraint_validator_agent.run(
        SchemaConstraintValidatorInput(
            request_context=request_context,
            payload=SchemaConstraintValidatorPayload(
                statements=[ConstraintGeneratedSql(**real_statement.model_dump())]
            ),
        )
    )
    assert not constraint_output.result.rejected
    assert len(constraint_output.result.validated) == 1

    pii_checker_agent = PiiExposureCheckerAgent(session_factory=session_factory)
    pii_output = await pii_checker_agent.run(
        PiiExposureCheckerInput(
            request_context=request_context,
            payload=PiiExposureCheckerPayload(
                statements=[PiiGeneratedSql(**real_statement.model_dump())]
            ),
        )
    )
    assert not pii_output.result.rejected
    assert len(pii_output.result.cleared) == 1

    policy_authorization_agent = PolicyAuthorizationAgent(
        opa_client=_real_opa_client_for_test()
    )
    authorization_output = await policy_authorization_agent.run(
        PolicyAuthorizationInput(
            request_context=request_context,
            payload=PolicyAuthorizationPayload(
                statements=[PolicyGeneratedSql(**real_statement.model_dump())], intent=intent
            ),
        )
    )
    assert not authorization_output.result.rejected
    assert len(authorization_output.result.authorized) == 1

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
    assert not cost_output.result.rejected

    execution_planning_agent = ExecutionPlanningAgent()
    planning_output = await execution_planning_agent.run(
        ExecutionPlanningInput(
            request_context=request_context,
            payload=ExecutionPlanningPayload(
                statements=[PlanningOptimizedSql(**cost_output.result.approved[0].model_dump())]
            ),
        )
    )
    assert not planning_output.result.rejected
    assert len(planning_output.result.plans) == 1
    real_plan = planning_output.result.plans[0]

    # ================== Data Federation: REAL execution against live Snowflake ==================

    data_federation_agent = DataFederationAgent(catalog_session_factory=session_factory)
    federation_output = await data_federation_agent.run(
        DataFederationInput(
            request_context=request_context,
            payload=DataFederationPayload(
                plans=[FederationExecutionPlan(**real_plan.model_dump())]
            ),
        )
    )
    assert not federation_output.errors
    federation_result = federation_output.result
    assert federation_result.final_row_count > 0
    assert "MARKETID" in federation_result.final_columns
    assert "UNITS_TOTAL" in federation_result.final_columns

    # ================== Manual result_alias threading (the real, logged gap) ==================
    # No contract between SQL Generation and Data Federation carries column
    # role/aliasing forward -- see LIMITATIONS.md item 28. Until a real
    # Coordinator exists, this is done by hand here, replicating SQL
    # Generation's own real aliasing rule: a "measure" column becomes
    # "{column_name}_TOTAL"; a "dimension" column keeps its bare name.

    chart_columns = [
        ChartColumnRef(
            term=c.term,
            catalog_column_id=c.catalog_column_id,
            table_name=c.table_name,
            column_name=c.column_name,
            data_type=c.data_type,
            role=c.role,
            result_alias=(
                f"{c.column_name}_TOTAL" if c.role == "measure" else c.column_name
            ),
        )
        for c in schema_mapping_result.columns
    ]

    # ================== Chart Selection (real) ==================

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
    assert chart_output.result.unmatched_columns == []
    chart = chart_output.result.chart
    assert chart.chart_type == "bar", "MARKETID is a non-temporal dimension"
    assert chart.x_column == "MARKETID"
    assert chart.y_column == "UNITS_TOTAL"
    print(f"\nReal Chart Selection: {chart.chart_type} ({chart.rationale})")

    # ================== Anomaly/Outlier Highlighter (real, independently re-derived) ==================

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

    # Independently recompute the same z-score statistic directly from the
    # real live data, using nothing from the agent's own internals --
    # proves the real math against real numbers regardless of whether
    # today's live data happens to contain a true outlier.
    import statistics as _statistics

    numeric_values = []
    for idx, row in enumerate(federation_result.final_rows):
        try:
            numeric_values.append((idx, float(row.get("UNITS_TOTAL"))))
        except (TypeError, ValueError):
            continue

    if len(numeric_values) < 3:
        assert anomaly_result.skipped_reason is not None
    else:
        nums = [v for _, v in numeric_values]
        expected_mean = _statistics.mean(nums)
        expected_stdev = _statistics.pstdev(nums)
        if expected_stdev == 0:
            assert anomaly_result.skipped_reason is not None
        else:
            expected_anomalies = [
                (idx, value, (value - expected_mean) / expected_stdev)
                for idx, value in numeric_values
                if abs((value - expected_mean) / expected_stdev) > anomaly_result.threshold
            ]
            assert len(anomaly_result.anomalies) == len(expected_anomalies)
            for finding, (idx, value, z) in zip(
                anomaly_result.anomalies, expected_anomalies, strict=True
            ):
                assert finding.row_index == idx
                assert abs(finding.measure_value - value) < 1e-6
                assert abs(finding.z_score - z) < 1e-6
                assert abs(finding.mean - expected_mean) < 1e-6
                assert abs(finding.stdev - expected_stdev) < 1e-6
    print(
        f"\nReal Anomaly/Outlier Highlighter: {len(anomaly_result.anomalies)} anomalies "
        f"found (skipped_reason={anomaly_result.skipped_reason!r}), independently "
        "re-derived and matched"
    )

    # ================== Grounded Narrative Generation: real citation validation ==================

    narrative_anomalies = [
        NarrativeAnomalyFinding(**a.model_dump()) for a in anomaly_result.anomalies
    ]

    # --- Positive case: a real citation drawn dynamically from the real,
    # live result set -- must validate cleanly. ---
    real_row_index = 0
    real_row = federation_result.final_rows[real_row_index]
    real_market = real_row["MARKETID"]
    real_units = real_row["UNITS_TOTAL"]
    good_narrative_llm = FakeLLMClient(
        response=(
            f'{{"narrative": "Market {real_market} [1] recorded a total volume of '
            f'{real_units} units [2].", "citations": ['
            f'{{"citation_id": 1, "row_index": {real_row_index}, "column": "MARKETID", '
            f'"cited_value": "{real_market}"}}, '
            f'{{"citation_id": 2, "row_index": {real_row_index}, "column": "UNITS_TOTAL", '
            f'"cited_value": "{real_units}"}}]}}'
        )
    )
    narrative_agent = GroundedNarrativeGenerationAgent(llm_client=good_narrative_llm)
    good_narrative_output = await narrative_agent.run(
        NarrativeGenerationInput(
            request_context=request_context,
            payload=NarrativeGenerationPayload(
                original_question=resolved_question,
                final_columns=federation_result.final_columns,
                final_rows=federation_result.final_rows,
                final_row_count=federation_result.final_row_count,
                chart=NarrativeChartSpec(**chart.model_dump()),
                anomalies=narrative_anomalies,
            ),
        )
    )
    assert not good_narrative_output.errors
    assert len(good_narrative_output.result.citations) == 2
    assert good_narrative_output.result.unverifiable_numbers == []
    print(f"\nReal Grounded Narrative (valid citations): {good_narrative_output.result.narrative}")

    # --- Negative case: one fabricated citation naming a value nowhere in
    # the real result set -- must be dropped and flagged. ---
    bad_narrative_llm = FakeLLMClient(
        response=(
            '{"narrative": "Market FABRICATED-MARKET [1] recorded 999999999 units.", '
            '"citations": [{"citation_id": 1, "row_index": 0, "column": "MARKETID", '
            '"cited_value": "FABRICATED-MARKET"}]}'
        )
    )
    bad_narrative_agent = GroundedNarrativeGenerationAgent(llm_client=bad_narrative_llm)
    bad_narrative_output = await bad_narrative_agent.run(
        NarrativeGenerationInput(
            request_context=request_context,
            payload=NarrativeGenerationPayload(
                original_question=resolved_question,
                final_columns=federation_result.final_columns,
                final_rows=federation_result.final_rows,
                final_row_count=federation_result.final_row_count,
                chart=NarrativeChartSpec(**chart.model_dump()),
                anomalies=narrative_anomalies,
            ),
        )
    )
    assert bad_narrative_output.result.citations == []
    assert any(e.code == "llm_cited_fabricated_value" for e in bad_narrative_output.errors)
    assert any(
        e.code == "narrative_contains_unverified_number" for e in bad_narrative_output.errors
    )
    print(
        f"\nReal Grounded Narrative rejection of fabricated citation: "
        f"{[e.message for e in bad_narrative_output.errors]}"
    )

    # ================== Follow-up Suggestion (real) ==================

    follow_up_llm = FakeLLMClient(
        response=(
            '{"suggestions": ['
            '{"question": "Which single account drove the highest-volume market?", '
            '"rationale": "drill-down into the top market"}, '
            '{"question": "How does this compare to last quarter?", '
            '"rationale": "trend context"}]}'
        )
    )
    follow_up_agent = FollowUpSuggestionAgent(llm_client=follow_up_llm)
    follow_up_output = await follow_up_agent.run(
        FollowUpSuggestionInput(
            request_context=request_context,
            payload=FollowUpSuggestionPayload(
                original_question=resolved_question,
                narrative=good_narrative_output.result.narrative,
                final_columns=federation_result.final_columns,
                final_row_count=federation_result.final_row_count,
                chart=FollowUpChartSpec(**chart.model_dump()),
                anomalies=[
                    FollowUpAnomalyFinding(**a.model_dump()) for a in anomaly_result.anomalies
                ],
            ),
        )
    )
    assert 1 <= len(follow_up_output.result.suggestions) <= 3
    for suggestion in follow_up_output.result.suggestions:
        assert suggestion.question.strip() != ""
    print(
        f"\nReal Follow-up Suggestions: "
        f"{[s.question for s in follow_up_output.result.suggestions]}"
    )

    neo4j_client.close()


def _real_opa_client_for_test():
    from navigraph_shared.opa import HttpOpaClient, OpaSettings

    return HttpOpaClient(OpaSettings())
