"""Real `Connector` implementation backed by `psycopg` (v3).

Uses the ANSI-standard `information_schema.tables`/`.columns` views
(genuinely portable SQL, unlike Snowflake's proprietary metadata surface)
-- a deliberate, real test that `navigraph_connectors.base.Connector`'s
interface generalizes across sources, not just a second copy of the
Snowflake connector's own queries. `psycopg`'s native paramstyle for
`execute_query` is `%(name)s`, the same pyformat convention this
codebase's SQL Generation agent already targets (see that module's own
docstring on why that placeholder style was chosen) -- a real,
non-coincidental consistency, not something special-cased here.
"""

from __future__ import annotations

import time
from typing import Any

from navigraph_connectors.base import (
    ColumnDescriptor,
    ConnectionTestResult,
    Connector,
    ConnectorCapabilities,
    QueryResult,
    SchemaDescriptor,
    TableDescriptor,
)
from navigraph_connectors.postgres.settings import PostgresSettings

# Excludes Postgres's own system schemas: every real Postgres database has
# `pg_catalog` and `information_schema` themselves registered as schemas --
# without this exclusion a crawl returns Postgres's own system tables mixed
# in with the real business schema, mirroring the exact same real gap the
# Snowflake connector's `_TABLES_QUERY` already guards against (see that
# module's comment).
_TABLES_QUERY = """
    SELECT table_schema, table_name
    FROM information_schema.tables
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
    ORDER BY table_schema, table_name
"""

_COLUMNS_QUERY = """
    SELECT
        table_schema,
        table_name,
        column_name,
        data_type,
        is_nullable,
        ordinal_position
    FROM information_schema.columns
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
    ORDER BY table_schema, table_name, ordinal_position
"""

# `information_schema` carries no column-level comment/description and no
# row-count estimate (unlike Snowflake's own metadata views) -- a real,
# honest difference between sources, not something to fabricate. Row-count
# estimates are available from Postgres's own `pg_stat_user_tables`/
# `pg_class.reltuples`, but that is a separate, source-specific query this
# connector does not attempt yet (logged as a real limitation, not silently
# assumed to be `None` by omission).


class PostgresConnector(Connector):
    """`Connector` implementation for Postgres.

    Reads connection settings from `PostgresSettings` (env-var driven, see
    that module -- note the deliberate `SOURCE_POSTGRES_*` env var prefix,
    distinct from NaviGraph's own internal catalog database settings). The
    `psycopg` driver is imported lazily inside `_connect()`, mirroring
    `SnowflakeConnector`'s exact pattern: importing this module (and the
    package-level registration side effect) or exercising unit tests that
    mock the connection entirely should never require the real driver to
    be importable.
    """

    def __init__(self, settings: PostgresSettings | None = None) -> None:
        self._settings = settings or PostgresSettings()

    def _connect(self) -> Any:
        # Imported lazily so importing this module (and the package-level
        # registration side effect in `navigraph_connectors/postgres/__init__.py`)
        # never requires the `psycopg` package to succeed at import time in
        # every code path -- e.g. unit tests that mock the connection
        # entirely.
        import psycopg

        settings = self._settings
        return psycopg.connect(
            host=settings.source_postgres_host,
            port=settings.source_postgres_port,
            dbname=settings.source_postgres_database,
            user=settings.source_postgres_user,
            password=settings.source_postgres_password,
            sslmode=settings.source_postgres_sslmode,
        )

    def test_connection(self) -> ConnectionTestResult:
        # Per the `Connector` interface contract, this method must never
        # raise -- any failure (bad credentials, unreachable host, missing
        # driver, etc.) is reported as data via
        # `ConnectionTestResult(success=False, ...)`.
        start = time.monotonic()
        try:
            conn = self._connect()
            try:
                cursor = conn.cursor()
                try:
                    cursor.execute("SELECT 1")
                    cursor.fetchall()
                finally:
                    cursor.close()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - contract requires catching everything
            return ConnectionTestResult(success=False, message=str(exc))

        latency_ms = (time.monotonic() - start) * 1000
        return ConnectionTestResult(
            success=True,
            message="Connected successfully",
            latency_ms=latency_ms,
        )

    def introspect_schema(self) -> list[SchemaDescriptor]:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(_TABLES_QUERY)
                table_rows = cursor.fetchall()

                cursor.execute(_COLUMNS_QUERY)
                column_rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            conn.close()

        # schema_name -> table_name -> TableDescriptor
        schemas: dict[str, dict[str, TableDescriptor]] = {}
        for table_schema, table_name in table_rows:
            schemas.setdefault(table_schema, {})[table_name] = TableDescriptor(
                name=table_name,
                columns=[],
            )

        for (
            table_schema,
            table_name,
            column_name,
            data_type,
            is_nullable,
            ordinal_position,
        ) in column_rows:
            table = schemas.setdefault(table_schema, {}).setdefault(
                table_name, TableDescriptor(name=table_name, columns=[])
            )
            table.columns.append(
                ColumnDescriptor(
                    name=column_name,
                    data_type=data_type,
                    nullable=str(is_nullable).upper() == "YES",
                    ordinal_position=ordinal_position,
                    description=None,
                )
            )

        return [
            SchemaDescriptor(name=schema_name, tables=list(tables.values()))
            for schema_name, tables in schemas.items()
        ]

    def execute_query(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        conn = self._connect()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(sql, params)
                fetched_rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description] if cursor.description else []
            finally:
                cursor.close()
        finally:
            conn.close()

        rows = [dict(zip(columns, row, strict=True)) for row in fetched_rows]
        return QueryResult(columns=columns, rows=rows, row_count=len(rows))

    def capabilities(self) -> ConnectorCapabilities:
        # Real, accurate values for what Postgres supports natively: row
        # security policies (`CREATE POLICY` / row-level security), no
        # native column masking (unlike Snowflake/Databricks Unity
        # Catalog -- a real, honest difference, not copied blindly from
        # the Snowflake connector's capability flags), and query pushdown
        # (the query runs entirely inside Postgres).
        return ConnectorCapabilities(
            supports_row_level_security=True,
            supports_column_masking=False,
            supports_query_pushdown=True,
        )
