"""Real `Connector` implementation backed by `psycopg2`.

Verified against a real, live local Postgres 16 instance while building this
connector (not just cross-checked against docs) -- a sample `sales` schema
with `customers`/`orders` tables, including a column comment and a nullable
column, was created and both `introspect_schema()` and `execute_query()`
(with real bind parameters) were run against it for real. This is the same
discipline `navigraph_connectors.snowflake.connector` was originally built
and verified under (see that module's own docstring).

REAL CROSS-SOURCE COMPATIBILITY FINDING (this is exactly the "pressure-test
the abstraction against a second, differently-shaped source" LIMITATIONS.md
item 1 called for): `query.sql_generation` hardcodes `%(name)s` (pyformat)
bind-parameter placeholders specifically because that is
`SnowflakeConnector`'s driver's paramstyle (see that agent's own module
docstring). `psycopg2` ALSO accepts `%(name)s`-style placeholders with a
dict of params natively -- confirmed live, not assumed -- so SQL Generation's
output is directly executable against this connector with zero dialect
translation. This does NOT generalize to every future connector (a
paramstyle mismatch would be a real, structural gap for e.g. a driver using
`?`/positional-only placeholders), but for Postgres specifically, no
adapter layer was needed.
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

# Excludes Postgres's own system schemas (`pg_catalog`, `information_schema`,
# `pg_toast`) -- confirmed live against a real instance: without this
# exclusion, a crawl returns Postgres's own system catalog relations mixed
# in with real business tables, the exact same real gotcha
# `SnowflakeConnector` found for `INFORMATION_SCHEMA` during Phase 2.
# `relkind IN ('r', 'v')` includes ordinary tables and views (`SnowflakeConnector`
# also does not filter by table_type, having found live during Phase 2 that
# views are real, queryable business objects too).
_TABLES_QUERY = """
    SELECT n.nspname AS table_schema, c.relname AS table_name,
           c.reltuples::bigint AS row_count_estimate
    FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE c.relkind IN ('r', 'v')
      AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    ORDER BY n.nspname, c.relname
"""

# Queries `pg_catalog` directly (not `information_schema.columns`) so a
# real column comment (`col_description`) and the exact native type
# (`format_type`, e.g. "character varying(200)", "numeric(12,2)") come back
# in the same single query, rather than needing a second round-trip per
# column the way `information_schema` alone would require.
_COLUMNS_QUERY = """
    SELECT
        n.nspname AS table_schema,
        c.relname AS table_name,
        a.attname AS column_name,
        format_type(a.atttypid, a.atttypmod) AS data_type,
        NOT a.attnotnull AS is_nullable,
        a.attnum AS ordinal_position,
        col_description(a.attrelid, a.attnum) AS description
    FROM pg_catalog.pg_attribute a
    JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE a.attnum > 0
      AND NOT a.attisdropped
      AND c.relkind IN ('r', 'v')
      AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
    ORDER BY n.nspname, c.relname, a.attnum
"""


class PostgresConnector(Connector):
    """`Connector` implementation for Postgres.

    Reads connection settings from `PostgresSettings` (env-var driven, see
    that module). The `psycopg2` driver is imported lazily inside
    `_connect()`, mirroring `SnowflakeConnector._connect()`'s identical
    pattern -- importing `navigraph_connectors.postgres` (and thus
    registering this class) or exercising unit tests that mock the
    connection entirely should never require the real driver to be
    importable.
    """

    def __init__(self, settings: PostgresSettings | None = None) -> None:
        self._settings = settings or PostgresSettings()

    def _connect(self) -> Any:
        # Imported lazily so importing this module (and the package-level
        # registration side effect in
        # `navigraph_connectors/postgres/__init__.py`) never requires
        # `psycopg2` to succeed at import time in every code path -- e.g.
        # unit tests that mock the connection entirely.
        import psycopg2

        return psycopg2.connect(
            host=self._settings.customer_postgres_host,
            port=self._settings.customer_postgres_port,
            dbname=self._settings.customer_postgres_database,
            user=self._settings.customer_postgres_user,
            password=self._settings.customer_postgres_password,
            sslmode=self._settings.customer_postgres_sslmode,
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
        for table_schema, table_name, row_count in table_rows:
            schemas.setdefault(table_schema, {})[table_name] = TableDescriptor(
                name=table_name,
                columns=[],
                row_count_estimate=row_count,
            )

        for (
            table_schema,
            table_name,
            column_name,
            data_type,
            is_nullable,
            ordinal_position,
            description,
        ) in column_rows:
            table = schemas.setdefault(table_schema, {}).setdefault(
                table_name, TableDescriptor(name=table_name, columns=[])
            )
            table.columns.append(
                ColumnDescriptor(
                    name=column_name,
                    data_type=data_type,
                    nullable=bool(is_nullable),
                    ordinal_position=ordinal_position,
                    description=description,
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
        # Real, accurate values for what a stock Postgres instance supports
        # natively: row-level security policies (`CREATE POLICY`, since
        # Postgres 9.5) and query pushdown (the query runs entirely inside
        # Postgres's own engine). Column-level masking has no first-class,
        # built-in Postgres feature equivalent to Snowflake's dynamic data
        # masking policies (third-party extensions exist but are not a core
        # server capability), so this is reported as unsupported rather than
        # assumed true by analogy with Snowflake.
        return ConnectorCapabilities(
            supports_row_level_security=True,
            supports_column_masking=False,
            supports_query_pushdown=True,
        )
