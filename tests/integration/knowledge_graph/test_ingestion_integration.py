"""Integration test: runs real ingestion against a real Neo4j AND real Snowflake.

REQUIRES A LIVE, REACHABLE NEO4J -- mirrors
`tests/integration/metadata_catalog/test_migrations.py`'s stance exactly:
this test does NOT skip gracefully if Neo4j is unreachable, since
`tests/integration/` is documented as running against the actual
docker-compose stack in a separate CI job (see that file and the top-level
`tests/integration/.gitkeep`). Point this at a real Neo4j via the same
env-var convention every other NaviGraph service uses (`NEO4J_URI`,
`NEO4J_USER`, `NEO4J_PASSWORD`) -- `KnowledgeGraphSettings()` picks these up
automatically. Defaults to `bolt://neo4j:7687` (the docker-compose service's
in-network hostname); when running this test from the host against
`infra/docker-compose.yml`'s published port, set
`NEO4J_URI=bolt://localhost:7687` first.

This test ALSO requires a live, reachable Postgres (same non-skipping
stance -- `run_ingestion`'s stage 1/2 read the real catalog via
`navigraph_catalog.api`) and a real Snowflake account for the
reference-data crawl stage. Snowflake is the one dependency here that DOES
skip gracefully: guarded by the same `snowflake_integration`-style
`@pytest.mark.skipif(not os.environ.get("SNOWFLAKE_ACCOUNT"), ...)` pattern
used by
`packages/connector_sdk/tests/snowflake/test_connector_integration.py`, so a
CI job without Snowflake credentials skips this file's assertions cleanly
rather than erroring.

This test:
  1. Registers a throwaway `DataSource` and crawls its schema into the
     catalog (reusing `navigraph_catalog`'s own crawl path), so stage 1/2
     have real rows to sync.
  2. Runs `run_ingestion(...)` for real.
  3. Asserts non-zero node counts via a real Cypher
     `MATCH (n {tenant_id: ...}) RETURN labels(n), count(*)` query.
  4. Re-runs `run_ingestion(...)` a SECOND time and asserts the counts are
     IDENTICAL -- proving idempotency for real, not just "no exception was
     raised".
"""

from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa
from navigraph_catalog.api import register_data_source
from navigraph_catalog.db import get_engine, get_session_factory, session_scope
from navigraph_catalog.ingestion.snowflake_crawler import crawl_and_store
from navigraph_catalog.settings import MetadataCatalogSettings
from navigraph_connectors.snowflake.connector import SnowflakeConnector
from navigraph_connectors.snowflake.settings import SnowflakeSettings
from navigraph_kg.client import Neo4jClient
from navigraph_kg.ingestion.pipeline import run_ingestion
from navigraph_kg.ontology import apply_constraints
from navigraph_kg.settings import KnowledgeGraphSettings

pytestmark = pytest.mark.neo4j_integration

_TENANT_ID = "kg-integration-test-tenant"


def _label_counts(client: Neo4jClient) -> dict[str, int]:
    records = client.run(
        "MATCH (n {tenant_id: $tenant_id}) RETURN labels(n) AS labels, count(*) AS count",
        tenant_id=_TENANT_ID,
    )
    counts: dict[str, int] = {}
    for record in records:
        labels = record["labels"] or ["Unknown"]
        for label in labels:
            counts[label] = counts.get(label, 0) + record["count"]
    return counts


@pytest.mark.skipif(
    not os.environ.get("SNOWFLAKE_ACCOUNT"),
    reason="requires real SNOWFLAKE_* env vars",
)
def test_ingestion_is_idempotent_against_real_neo4j_and_snowflake() -> None:
    neo4j_client = Neo4jClient(KnowledgeGraphSettings())

    # Fail loudly (not skip) if Neo4j isn't reachable -- see module docstring
    # for why that's the correct, expected behavior for this tier.
    connectivity = neo4j_client.test_connection()
    assert connectivity.success, f"Neo4j unreachable: {connectivity.message}"

    apply_constraints(neo4j_client)

    connector = SnowflakeConnector(SnowflakeSettings())
    snowflake_connectivity = connector.test_connection()
    assert snowflake_connectivity.success, (
        f"Snowflake unreachable: {snowflake_connectivity.message}"
    )

    catalog_settings = MetadataCatalogSettings()
    engine = get_engine(catalog_settings)

    # Fail loudly (not skip) if Postgres isn't reachable either.
    with engine.connect() as connection:
        connection.execute(sa.text("SELECT 1"))

    session_factory = get_session_factory(engine)

    with session_scope(session_factory) as catalog_session:
        data_source = register_data_source(
            catalog_session,
            tenant_id=_TENANT_ID,
            name=f"kg-integration-{uuid.uuid4().hex[:8]}",
            source_type="snowflake",
            connection_ref={"env_prefix": "SNOWFLAKE"},
        )
        crawl_and_store(catalog_session, data_source_id=data_source.id, connector=connector)
        data_source_id = data_source.id

    try:
        with session_scope(session_factory) as catalog_session:
            first_summary = run_ingestion(
                catalog_session,
                neo4j_client,
                connector,
                data_source_id=data_source_id,
                tenant_id=_TENANT_ID,
            )

        assert first_summary.tables_synced > 0
        assert first_summary.assets_synced > 0
        assert first_summary.markets_synced > 0

        first_counts = _label_counts(neo4j_client)
        assert sum(first_counts.values()) > 0

        with session_scope(session_factory) as catalog_session:
            second_summary = run_ingestion(
                catalog_session,
                neo4j_client,
                connector,
                data_source_id=data_source_id,
                tenant_id=_TENANT_ID,
            )

        second_counts = _label_counts(neo4j_client)

        assert second_counts == first_counts, (
            "re-running ingestion changed node counts -- MERGE is not behaving "
            "idempotently"
        )
        assert second_summary.model_dump() == first_summary.model_dump()
    finally:
        neo4j_client.run("MATCH (n {tenant_id: $tenant_id}) DETACH DELETE n", tenant_id=_TENANT_ID)
        neo4j_client.close()
