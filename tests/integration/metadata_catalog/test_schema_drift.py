"""Integration test: real schema-drift detection against a real Postgres
catalog database (Phase 13.1).

REQUIRES a real, reachable Postgres -- not skipped gracefully, same stance
as every other `postgres_integration`-marked test in this repo. Runs the
real Alembic migration up first (mirroring `test_migrations.py`'s own
pattern), then proves `crawl_and_store`'s drift detection end to end
against real catalog rows, not mocks:

  1. First crawl of a brand-new table -> `is_new=True`.
  2. An identical second crawl -> `changed=False` (nothing drifted).
  3. A third crawl where the connector's schema genuinely changed (a new
     column appears) -> `changed=True`, with the real old/new hashes
     differing.
  4. `DataSource.last_crawled_at` is real and advances across crawls.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# Import side effect only: registers "postgres" in
# `navigraph_connectors.registry`.
import navigraph_connectors.postgres  # noqa: F401
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from navigraph_catalog.api import register_data_source
from navigraph_catalog.db import get_engine, get_session_factory, session_scope
from navigraph_catalog.ingestion.snowflake_crawler import crawl_and_store
from navigraph_catalog.models import DataSource
from navigraph_catalog.settings import MetadataCatalogSettings
from navigraph_connectors.base import (
    ColumnDescriptor,
    ConnectionTestResult,
    Connector,
    ConnectorCapabilities,
    QueryResult,
    SchemaDescriptor,
    TableDescriptor,
)
from sqlalchemy import select

pytestmark = pytest.mark.postgres_integration

_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "packages" / "metadata_catalog"
_TENANT_ID = "phase13-drift-test-tenant"


class _FakeConnector(Connector):
    def __init__(self, schemas: list[SchemaDescriptor]) -> None:
        self._schemas = schemas

    def test_connection(self) -> ConnectionTestResult:
        return ConnectionTestResult(success=True, message="ok")

    def introspect_schema(self) -> list[SchemaDescriptor]:
        return self._schemas

    def execute_query(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        return QueryResult(columns=[], rows=[], row_count=0)

    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            supports_row_level_security=False,
            supports_column_masking=False,
            supports_query_pushdown=False,
        )


def _schema_v1() -> list[SchemaDescriptor]:
    return [
        SchemaDescriptor(
            name="public",
            tables=[
                TableDescriptor(
                    name="orders",
                    row_count_estimate=10,
                    columns=[
                        ColumnDescriptor(
                            name="id", data_type="INTEGER", nullable=False, ordinal_position=1
                        ),
                    ],
                )
            ],
        )
    ]


def _schema_v2_with_a_new_column() -> list[SchemaDescriptor]:
    return [
        SchemaDescriptor(
            name="public",
            tables=[
                TableDescriptor(
                    name="orders",
                    row_count_estimate=15,
                    columns=[
                        ColumnDescriptor(
                            name="id", data_type="INTEGER", nullable=False, ordinal_position=1
                        ),
                        ColumnDescriptor(
                            name="total", data_type="NUMBER", nullable=True, ordinal_position=2
                        ),
                    ],
                )
            ],
        )
    ]


@pytest.fixture()
def catalog_session_factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.orm.sessionmaker]:
    """Mirrors `tests/integration/multi_client_isolation`'s identical
    fixture -- see that file for why env vars, not constructor kwargs, are
    required here (`migrations/env.py` always rebuilds its own settings
    from the environment)."""

    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "navigraph_catalog_test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "navigraph_catalog_test_pw")
    monkeypatch.setenv("POSTGRES_DB", "navigraph_catalog_test")

    settings = MetadataCatalogSettings()
    engine = get_engine(settings)

    with engine.connect() as connection:
        connection.execute(sa.text("SELECT 1"))

    config = Config(str(_PACKAGE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_PACKAGE_ROOT / "migrations"))
    command.upgrade(config, "head")

    try:
        yield get_session_factory(engine)
    finally:
        command.downgrade(config, "base")


def test_crawl_and_store_detects_real_drift_against_a_live_catalog(
    catalog_session_factory: sa.orm.sessionmaker,
) -> None:
    session_factory = catalog_session_factory

    with session_scope(session_factory) as session:
        data_source = register_data_source(
            session,
            tenant_id=_TENANT_ID,
            name=f"drift-test-{uuid.uuid4()}",
            source_type="postgres",
            connection_ref={"secret_scope": "irrelevant-for-this-test"},
        )
        data_source_id = data_source.id
        assert data_source.last_crawled_at is None

    # --- Crawl 1: a brand-new table. ---
    with session_scope(session_factory) as session:
        result_1 = crawl_and_store(
            session, data_source_id=data_source_id, connector=_FakeConnector(_schema_v1())
        )

    assert result_1.tables_synced == 1
    assert result_1.new_table_names == ["orders"]
    assert result_1.changed_table_names == []

    with session_scope(session_factory) as session:
        first_crawl_time = session.execute(
            select(DataSource.last_crawled_at).where(DataSource.id == data_source_id)
        ).scalar_one()
    assert first_crawl_time is not None

    # Real wall-clock gap so the second crawl's timestamp is provably later.
    time.sleep(1.1)

    # --- Crawl 2: identical schema -- must report NO drift. ---
    with session_scope(session_factory) as session:
        result_2 = crawl_and_store(
            session, data_source_id=data_source_id, connector=_FakeConnector(_schema_v1())
        )

    assert result_2.new_table_names == []
    assert result_2.changed_table_names == []

    with session_scope(session_factory) as session:
        second_crawl_time = session.execute(
            select(DataSource.last_crawled_at).where(DataSource.id == data_source_id)
        ).scalar_one()
    assert second_crawl_time > first_crawl_time

    # --- Crawl 3: a real structural change (a new column). ---
    with session_scope(session_factory) as session:
        result_3 = crawl_and_store(
            session,
            data_source_id=data_source_id,
            connector=_FakeConnector(_schema_v2_with_a_new_column()),
        )

    assert result_3.new_table_names == []
    assert result_3.changed_table_names == ["orders"]
    drift_event = result_3.drift_events[0]
    assert drift_event.old_hash is not None
    assert drift_event.old_hash != drift_event.new_hash
