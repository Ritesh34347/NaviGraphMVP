"""Integration test: real Cypher queries via `navigraph_kg.api`.

Runs against the graph populated by
`test_ingestion_integration.py`'s ingestion run for the same
`kg-integration-test-tenant` tenant -- depends on that file having been run
first in the same Neo4j instance (pytest collects and runs files in a
directory in name order, so `test_api_integration.py` naturally follows
`test_ingestion_integration.py`).

Depends on two real, live-verified facts about the `FIDELITY_POC` dataset:
  - `STAGING.SCHEMA_ENRICHMENT` (the glossary source `navigraph_catalog`
    ingests into `ColumnGlossary`) contains a `"trade value"` business term.
  - Exchange `ATHEX` genuinely groups exactly three real markets in
    `FAR_TRANS.MARKETS`: `EBB`, `XATH`, `ENAX`.

REQUIRES A LIVE, REACHABLE NEO4J already populated by
`test_ingestion_integration.py` -- not skipped gracefully, same stance as
that file and `tests/integration/metadata_catalog/test_migrations.py`.
"""

from __future__ import annotations

import pytest

from navigraph_kg.api import list_markets_for_exchange, resolve_business_term
from navigraph_kg.client import Neo4jClient
from navigraph_kg.settings import KnowledgeGraphSettings

pytestmark = pytest.mark.neo4j_integration

_TENANT_ID = "kg-integration-test-tenant"


def _connected_client() -> Neo4jClient:
    client = Neo4jClient(KnowledgeGraphSettings())
    connectivity = client.test_connection()
    assert connectivity.success, f"Neo4j unreachable: {connectivity.message}"
    return client


def test_resolve_business_term_finds_trade_value() -> None:
    client = _connected_client()
    try:
        matches = resolve_business_term(client, tenant_id=_TENANT_ID, term="trade value")
        assert len(matches) > 0
        assert all("catalog_column_id" in match for match in matches)
    finally:
        client.close()


def test_list_markets_for_exchange_returns_three_real_athex_markets() -> None:
    client = _connected_client()
    try:
        markets = list_markets_for_exchange(client, tenant_id=_TENANT_ID, exchange_id="ATHEX")
        market_ids = {market["market_id"] for market in markets}
        assert market_ids == {"EBB", "XATH", "ENAX"}
    finally:
        client.close()
