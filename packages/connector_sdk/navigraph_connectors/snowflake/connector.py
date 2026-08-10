"""Real `Connector` implementation backed by `snowflake.connector`.

Verified against a real Snowflake account during Phase 2 (not just
cross-checked against docs) -- see `tests/snowflake/test_connector_integration.py`
for the automated version of that check, which requires real `SNOWFLAKE_*`
credentials to run. The real-account run is also what caught the need to
exclude `INFORMATION_SCHEMA` from both queries below (see the comment on
`_TABLES_QUERY`) and to not restrict `table_type` (views are real,
queryable business objects too -- the initial `WHERE table_type = 'BASE
TABLE'` filter silently dropped them).
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
    RequiredSetting,
    SchemaDescriptor,
    TableDescriptor,
)
from navigraph_connectors.snowflake.auth import build_connect_kwargs
from navigraph_connectors.snowflake.settings import SnowflakeSettings

# Excludes INFORMATION_SCHEMA itself: every Snowflake database has this
# schema, and without the exclusion a crawl returns Snowflake's own ~60
# system metadata views (COLUMNS, TABLES, FUNCTIONS, ...) mixed in with the
# real business schema -- confirmed against a real account during Phase 2
# verification, not a hypothetical.
_TABLES_QUERY = """
    SELECT table_schema, table_name, row_count
    FROM information_schema.tables
    WHERE table_schema != 'INFORMATION_SCHEMA'
    ORDER BY table_schema, table_name
"""

_COLUMNS_QUERY = """
    SELECT
        table_schema,
        table_name,
        column_name,
        data_type,
        is_nullable,
        ordinal_position,
        comment
    FROM information_schema.columns
    WHERE table_schema != 'INFORMATION_SCHEMA'
    ORDER BY table_schema, table_name, ordinal_position
"""


class SnowflakeConnector(Connector):
    """`Connector` implementation for Snowflake.

    Reads connection settings from `SnowflakeSettings` (env-var driven, see
    that module). The `snowflake.connector` driver is imported lazily inside
    `_connect()` rather than at module top, mirroring
    `navigraph_shared.llm.client.AnthropicLLMClient`'s pattern: importing
    `navigraph_connectors.snowflake` (and thus registering this class) or
    exercising unit tests that mock the connection entirely should never
    require the real driver to be importable.
    """

    def __init__(self, settings: SnowflakeSettings | None = None) -> None:
        self._settings = settings or SnowflakeSettings()

    def _connect(self) -> Any:
        # Imported lazily so importing this module (and the package-level
        # registration side effect in `navigraph_connectors/snowflake/__init__.py`)
        # never requires the `snowflake-connector-python` package to succeed
        # at import time in every code path -- e.g. unit tests that mock the
        # connection entirely.
        import snowflake.connector

        kwargs = build_connect_kwargs(self._settings)
        return snowflake.connector.connect(**kwargs)

    def test_connection(self) -> ConnectionTestResult:
        # Per the `Connector` interface contract, this method must never
        # raise -- any failure (bad credentials, unreachable account,
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
            comment,
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
                    description=comment,
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
        # Real, accurate values for what Snowflake supports natively: row
        # access policies, column masking policies, and query pushdown (the
        # query runs entirely inside Snowflake's own compute).
        return ConnectorCapabilities(
            supports_row_level_security=True,
            supports_column_masking=True,
            supports_query_pushdown=True,
        )

    @classmethod
    def required_settings(cls) -> list[RequiredSetting]:
        # `field` here MUST match the short names `settings_factory.py`'s
        # `build_snowflake_settings` actually reads via `secrets.get(scope=
        # scope, field=name)` ("account", not "snowflake_account") -- see
        # `postgres/connector.py`'s identical `required_settings` fix for
        # the full story: this manifest previously declared the
        # `SnowflakeSettings` FIELD names instead of the `SecretsProvider`
        # FIELD names, so a caller's posted credentials were silently
        # ignored.
        return [
            RequiredSetting(field="account", description="Snowflake account identifier"),
            RequiredSetting(field="user", description="Snowflake username"),
            RequiredSetting(
                field="warehouse", description="Snowflake warehouse to run queries against"
            ),
            RequiredSetting(field="database", description="Snowflake database to introspect/query"),
            RequiredSetting(field="role", description="Snowflake role to assume", required=False),
            RequiredSetting(
                field="auth_method",
                description="'password' (default) or 'key_pair'",
                required=False,
            ),
            RequiredSetting(
                field="password",
                description="Snowflake password",
                condition="required when auth_method == 'password' (the default)",
            ),
            RequiredSetting(
                field="private_key_path",
                description="Path to a private key file for key-pair auth",
                required=False,
                condition="required when auth_method == 'key_pair'",
            ),
            RequiredSetting(
                field="private_key_passphrase",
                description="Passphrase for an encrypted private key",
                required=False,
                condition="only if the key_pair private key is encrypted",
            ),
        ]
