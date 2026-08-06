"""Real `Connector` implementation backed by `databricks-sql-connector`.

Uses Unity Catalog's `{catalog}.information_schema.tables`/`.columns` --
requires the target workspace to have Unity Catalog enabled (a real,
honest assumption logged in LIMITATIONS.md, not silently assumed to work
against a legacy `hive_metastore`-only workspace).

PARAMSTYLE GOTCHA, found live while designing this (confirmed against the
installed `databricks-sql-connector` package's own `Cursor.execute`
docstring): this driver's default (NATIVE parameter mode) expects PEP-249
`named` paramstyle (`:param_name`), NOT the `%(name)s` pyformat style
Snowflake/psycopg both use and this codebase's SQL Generation agent
targets universally (dialect-neutral SQL, one shared placeholder
convention across every connector). Rather than requiring SQL Generation
to know which connector will eventually execute a statement (breaking the
whole point of dialect-neutral SQL), `execute_query` here transforms
`%(name)s` -> `:name` itself before calling the driver -- the one place
this real cross-driver difference is handled, not leaked upstream.
"""

from __future__ import annotations

import re
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
from navigraph_connectors.databricks.settings import DatabricksSettings

# Matches this codebase's universal `%(name)s` pyformat placeholder
# (see `sql_generation.agent`'s module docstring) so it can be rewritten
# to the `:name` named-paramstyle this driver's default NATIVE parameter
# mode actually expects -- see this module's docstring.
_PYFORMAT_PARAM_RE = re.compile(r"%\((\w+)\)s")


def _to_named_paramstyle(sql: str) -> str:
    return _PYFORMAT_PARAM_RE.sub(r":\1", sql)


class DatabricksConnector(Connector):
    """`Connector` implementation for Databricks (Unity Catalog).

    Reads connection settings from `DatabricksSettings` (env-var driven,
    see that module). The `databricks.sql` driver is imported lazily
    inside `_connect()`, mirroring `SnowflakeConnector`/`PostgresConnector`'s
    exact pattern.
    """

    def __init__(self, settings: DatabricksSettings | None = None) -> None:
        self._settings = settings or DatabricksSettings()

    def _connect(self) -> Any:
        # Imported lazily so importing this module (and the package-level
        # registration side effect in `navigraph_connectors/databricks/__init__.py`)
        # never requires the `databricks-sql-connector` package to succeed
        # at import time in every code path -- e.g. unit tests that mock
        # the connection entirely.
        from databricks import sql

        settings = self._settings
        return sql.connect(
            server_hostname=settings.databricks_server_hostname,
            http_path=settings.databricks_http_path,
            access_token=settings.databricks_access_token,
        )

    def test_connection(self) -> ConnectionTestResult:
        # Per the `Connector` interface contract, this method must never
        # raise -- any failure (bad credentials, unreachable workspace,
        # missing driver, etc.) is reported as data via
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

    def _information_schema_table(self, name: str) -> str:
        catalog = self._settings.databricks_catalog
        if not catalog:
            raise ValueError(
                "databricks_catalog is required to introspect a Unity Catalog "
                "workspace -- information_schema is catalog-scoped, there is "
                "no schema-less default to fall back to"
            )
        return f"{catalog}.information_schema.{name}"

    def introspect_schema(self) -> list[SchemaDescriptor]:
        schema_filter = self._settings.databricks_schema

        tables_query = f"""
            SELECT table_schema, table_name
            FROM {self._information_schema_table("tables")}
            WHERE table_schema NOT IN ('information_schema')
        """
        columns_query = f"""
            SELECT
                table_schema,
                table_name,
                column_name,
                data_type,
                is_nullable,
                ordinal_position
            FROM {self._information_schema_table("columns")}
            WHERE table_schema NOT IN ('information_schema')
        """
        if schema_filter:
            tables_query += " AND table_schema = :schema_filter"
            columns_query += " AND table_schema = :schema_filter"
        tables_query += " ORDER BY table_schema, table_name"
        columns_query += " ORDER BY table_schema, table_name, ordinal_position"

        params = {"schema_filter": schema_filter} if schema_filter else None

        conn = self._connect()
        try:
            cursor = conn.cursor()
            try:
                cursor.execute(tables_query, params)
                table_rows = cursor.fetchall()

                cursor.execute(columns_query, params)
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
                cursor.execute(_to_named_paramstyle(sql), params)
                fetched_rows = cursor.fetchall()
                columns = [col[0] for col in cursor.description] if cursor.description else []
            finally:
                cursor.close()
        finally:
            conn.close()

        rows = [dict(zip(columns, row, strict=True)) for row in fetched_rows]
        return QueryResult(columns=columns, rows=rows, row_count=len(rows))

    def capabilities(self) -> ConnectorCapabilities:
        # Real, accurate values for Unity Catalog: real row filters and
        # real column masks (both genuine Unity Catalog features -- a real
        # differentiator from Postgres, captured precisely rather than
        # copying Snowflake's/Postgres's capability flags blindly), and
        # query pushdown (the query runs entirely inside the SQL warehouse).
        return ConnectorCapabilities(
            supports_row_level_security=True,
            supports_column_masking=True,
            supports_query_pushdown=True,
        )
