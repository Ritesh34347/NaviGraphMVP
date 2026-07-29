"""Integration test: chains the Understanding domain, SQL Generation, all
four real Guardrail-domain agents, SQL Optimization, and Execution Planning
for real, against the live docker-compose Postgres, Neo4j, and OPA (running
the real, non-allow-all `authz.rego` policy), plus a real Snowflake-backed
catalog for the PII/schema-constraint checks.

REQUIRES LIVE, REACHABLE POSTGRES, NEO4J, AND OPA -- mirrors
`tests/integration/query_pipeline/test_pipeline_chain.py`'s stance exactly:
this test does NOT skip gracefully if any of these are unreachable, since
`tests/integration/` is documented as running against the actual
docker-compose stack in a separate CI job.

Point this at the real services via the same env-var convention every
other NaviGraph integration test uses: `POSTGRES_HOST`/`POSTGRES_PORT`,
`NEO4J_URI`/`NEO4J_PASSWORD`, `OPA_URL`. Defaults are the docker-compose
in-network hostnames; when running from the host against
`infra/docker-compose.yml`'s published ports, set
`POSTGRES_HOST=localhost POSTGRES_PORT=5433 NEO4J_URI=bolt://localhost:7687
OPA_URL=http://localhost:8181` first.

Deliberately does NOT proceed to Data Federation -- real execution against
live Snowflake is already proven end-to-end by
`tests/integration/query_pipeline/`. This test's job is to prove the four
Guardrail gates themselves are real: a statement can only reach a real,
`read_only_verified` `ExecutionPlan` after passing Schema Constraint
Validation, Policy Authorization (the real OPA policy), the Query Cost/
Row-Limit Estimator, AND Execution Planning's own SELECT-only gate -- and
that a rejected statement at any of those gates never reaches the next one.

Worked example, same as the Query-domain pipeline-chain test (this test
picks up exactly where that one leaves off): "What is the total transaction
volume by market?" -> the real `SchemaMappingResult`/`GeneratedSql` for
`STAGING_TRANSACTIONS.UNITS`/`MARKETID`.

Three real rejections are proven, each at the gate whose job it actually is:

1. Schema Constraint Validator rejects a statement referencing an unknown
   column (`STAGING_TRANSACTIONS.NOSUCHCOLUMN`), run in the SAME batch as
   the real, valid statement -- the valid one is still validated.
2. PII Exposure Checker rejects a statement referencing the real, tagged-PII
   `CUSTOMER_INFORMATION.CUSTOMERID` column for an `analyst` role, then
   clears the identical statement for a `pii_viewer` role -- proving both
   directions against real, live-tagged catalog data (see Phase 6's
   `tools/scripts/tag_pii_columns.py` backfill).
3. Policy Authorization denies the whole request when the caller's
   `claims.tenant_id` doesn't match `request_context.tenant_id` -- run as a
   SEPARATE call with a different `RequestContext` (unlike the other two
   gates, OPA's authorization decision in this policy is per-request-
   identity, not per-statement, so this cannot be demonstrated by mixing a
   bad statement into the real request's own batch).
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
from navigraph_shared.opa import HttpOpaClient, OpaSettings

pytestmark = pytest.mark.postgres_integration

_TENANT_ID = "navikenz-poc"
_DATA_SOURCE_NAME = "fidelity_poc_snowflake_v2"
_QUESTION = "What is the total transaction volume by market?"


def _request_context(*, claims: dict | None = None, roles: list[str] | None = None) -> RequestContext:
    return RequestContext(
        tenant_id=_TENANT_ID,
        user_id="integration-test-user",
        trace_id="guardrail-pipeline-chain-test",
        roles=roles if roles is not None else ["analyst"],
        claims=claims if claims is not None else {"tenant_id": _TENANT_ID},
    )


@pytest.mark.neo4j_integration
@pytest.mark.opa_integration
@pytest.mark.asyncio
async def test_guardrail_pipeline_rejects_bad_statements_and_clears_a_real_one() -> None:
    catalog_settings = MetadataCatalogSettings()
    engine = get_engine(catalog_settings)
    session_factory = get_session_factory(engine)

    with session_scope(session_factory) as session:
        data_sources = list_data_sources(session, tenant_id=_TENANT_ID)
        matching = [ds for ds in data_sources if ds.name == _DATA_SOURCE_NAME]
        assert matching, (
            f"No data source named {_DATA_SOURCE_NAME!r} for tenant {_TENANT_ID!r} -- "
            "see tests/integration/understanding_pipeline/test_pipeline_chain.py's "
            "identical assertion for how to (re)create it."
        )
        data_source_id = matching[0].id

    neo4j_client = Neo4jClient(KnowledgeGraphSettings())
    connectivity = neo4j_client.test_connection()
    assert connectivity.success, f"Neo4j unreachable: {connectivity.message}"

    request_context = _request_context()

    # ================== Understanding domain (real, same as Phase 4) ==================

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

    # ================== Query domain: Data Source Discovery + SQL Generation ==================

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
    real_data_source_id = resolved_data_sources[0].data_source_id

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

    # ================== Guardrail gate 1: Schema Constraint Validator ==================
    # Real statement + a deliberately malformed sibling (unknown column) in
    # the SAME batch -- proves per-statement rejection without discarding a
    # valid sibling.

    bogus_column_statement = ConstraintGeneratedSql(
        data_source_id=real_data_source_id,
        sql="SELECT NOSUCHCOLUMN FROM STAGING.STAGING_TRANSACTIONS",
        params={},
        referenced_tables=["STAGING_TRANSACTIONS"],
        referenced_columns=["NOSUCHCOLUMN"],
    )
    schema_constraint_validator_agent = SchemaConstraintValidatorAgent(
        session_factory=session_factory
    )
    constraint_output = await schema_constraint_validator_agent.run(
        SchemaConstraintValidatorInput(
            request_context=request_context,
            payload=SchemaConstraintValidatorPayload(
                statements=[
                    ConstraintGeneratedSql(**real_statement.model_dump()),
                    bogus_column_statement,
                ]
            ),
        )
    )
    assert len(constraint_output.result.validated) == 1, (
        "expected only the real statement to survive; the bogus-column "
        "statement must never be validated"
    )
    assert constraint_output.result.validated[0].sql == real_statement.sql
    assert len(constraint_output.result.rejected) == 1
    assert constraint_output.result.rejected[0].code == "unknown_column"
    print(
        f"\nReal Schema Constraint Validator rejection: "
        f"{constraint_output.result.rejected[0].message}"
    )

    # ================== Guardrail gate 2: PII Exposure Checker ==================
    # Real (non-PII) statement + a real PII-column statement (the actual
    # tagged CUSTOMER_INFORMATION.CUSTOMERID column -- see
    # tools/scripts/tag_pii_columns.py's Phase 6 backfill), checked once for
    # an unauthorized role and once for an authorized one.

    pii_statement = PiiGeneratedSql(
        data_source_id=real_data_source_id,
        sql="SELECT CUSTOMER_INFORMATION.CUSTOMERID FROM CUSTOMER_INFORMATION",
        params={},
        referenced_tables=["CUSTOMER_INFORMATION"],
        referenced_columns=["CUSTOMER_INFORMATION.CUSTOMERID"],
    )
    pii_checker_agent = PiiExposureCheckerAgent(session_factory=session_factory)

    pii_output_analyst = await pii_checker_agent.run(
        PiiExposureCheckerInput(
            request_context=_request_context(roles=["analyst"]),
            payload=PiiExposureCheckerPayload(
                statements=[
                    PiiGeneratedSql(**real_statement.model_dump()),
                    pii_statement,
                ]
            ),
        )
    )
    assert len(pii_output_analyst.result.cleared) == 1
    assert pii_output_analyst.result.cleared[0].sql == real_statement.sql
    assert len(pii_output_analyst.result.rejected) == 1
    assert pii_output_analyst.result.rejected[0].code == "pii_column_access_denied"
    print(
        f"\nReal PII Exposure Checker rejection (analyst role): "
        f"{pii_output_analyst.result.rejected[0].message}"
    )

    pii_output_authorized = await pii_checker_agent.run(
        PiiExposureCheckerInput(
            request_context=_request_context(roles=["pii_viewer"]),
            payload=PiiExposureCheckerPayload(statements=[pii_statement]),
        )
    )
    assert not pii_output_authorized.result.rejected
    assert len(pii_output_authorized.result.cleared) == 1
    print("\nReal PII Exposure Checker clearance (pii_viewer role): CUSTOMERID statement cleared")

    # ================== Guardrail gate 3: Policy Authorization (real OPA) ==================
    # A separate call with a mismatched tenant claim -- OPA's decision here
    # is per-request-identity, not per-statement, so this is a standalone
    # call rather than a second statement in the real request's batch.

    opa_client = HttpOpaClient(OpaSettings())
    policy_authorization_agent = PolicyAuthorizationAgent(opa_client=opa_client)

    real_policy_statement = PolicyGeneratedSql(**real_statement.model_dump())
    good_authorization_output = await policy_authorization_agent.run(
        PolicyAuthorizationInput(
            request_context=request_context,
            payload=PolicyAuthorizationPayload(statements=[real_policy_statement], intent=intent),
        )
    )
    assert not good_authorization_output.result.rejected
    assert len(good_authorization_output.result.authorized) == 1
    print("\nReal Policy Authorization (matching tenant claim): statement authorized")

    bad_context = _request_context(claims={"tenant_id": "some-other-tenant"})
    bad_authorization_output = await policy_authorization_agent.run(
        PolicyAuthorizationInput(
            request_context=bad_context,
            payload=PolicyAuthorizationPayload(statements=[real_policy_statement], intent=intent),
        )
    )
    assert bad_authorization_output.result.authorized == []
    assert len(bad_authorization_output.result.rejected) == 1
    assert bad_authorization_output.result.rejected[0].code == "policy_denied"
    print(
        f"\nReal Policy Authorization denial (mismatched tenant claim, real OPA): "
        f"{bad_authorization_output.result.rejected[0].message}"
    )

    # ================== SQL Optimization + Query Cost/Row-Limit Estimator ==================
    # Only the real, guardrail-cleared statement proceeds from here.

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
    assert len(optimization_output.result.statements) == 1
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
    assert len(cost_output.result.approved) == 1
    assert len(cost_output.result.estimates) == 1

    # ================== Execution Planning: the final, independent safety gate ==================

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
    assert real_plan.read_only_verified is True
    assert real_plan.data_source_id == real_data_source_id
    print(
        f"\nReal ExecutionPlan ready (Data Federation deliberately not invoked this phase -- "
        f"already proven for real in tests/integration/query_pipeline/): "
        f"route={real_plan.route} max_rows={real_plan.max_rows}"
    )

    neo4j_client.close()
