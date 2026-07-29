"""Core connector plugin interface and the data shapes it speaks in.

This module is intentionally source-agnostic: nothing here is shaped around
Snowflake (or any other specific source). A future Postgres or REST
connector implements the exact same `Connector` interface with no changes
required here. This is a named architecture decision -- keeping the
abstraction boundary clean means this package never imports SQLAlchemy and
never has any notion of a catalog database; it only knows how to talk to an
external data source and describe its schema in plain Pydantic models. Any
mapping from a source's native types to a normalized/canonical type system
is deliberately left to a later phase, not this SDK.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class ColumnDescriptor(BaseModel):
    """One column of a table, as reported by the source itself.

    `data_type` is the source's native type name verbatim (e.g. `"VARCHAR"`,
    `"NUMBER"`) -- deliberately not normalized or mapped to a canonical type
    system yet, since that mapping is a later phase's responsibility, not
    this SDK's.
    """

    name: str
    data_type: str
    nullable: bool
    ordinal_position: int
    description: str | None = None


class TableDescriptor(BaseModel):
    """One table (or view) within a schema, and its columns."""

    name: str
    columns: list[ColumnDescriptor]
    row_count_estimate: int | None = None


class SchemaDescriptor(BaseModel):
    """One schema (namespace) within a data source, and its tables."""

    name: str
    tables: list[TableDescriptor]


class ConnectionTestResult(BaseModel):
    """Outcome of `Connector.test_connection()`.

    Always a normal return value, never an exception -- see `Connector`'s
    docstring for why.
    """

    success: bool
    message: str
    latency_ms: float | None = None


class QueryResult(BaseModel):
    """Result of `Connector.execute_query()`.

    `rows` is a list of `{column_name: value}` dicts rather than positional
    tuples, since that shape is friendlier for downstream consumers (no need
    to zip against `columns` to make sense of a row).
    """

    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int


class ConnectorCapabilities(BaseModel):
    """What a connector's underlying data source can actually do.

    These are real, source-specific capability flags -- not placeholders --
    so callers (e.g. a future policy-enforcement layer) can make decisions
    like "can I rely on this source's own row-level security instead of
    enforcing it myself" per connector.
    """

    supports_row_level_security: bool
    supports_column_masking: bool
    supports_query_pushdown: bool


class Connector(ABC):
    """The plugin interface every data-source connector implements.

    This is the sole boundary between NaviGraph and an external data source.
    Every concrete connector (Snowflake today; Postgres, REST, etc. in the
    future) implements these four methods and nothing more is assumed about
    it elsewhere in the codebase -- callers only ever code against this
    interface, never against a concrete connector's own extra methods.

    Contract for implementations:

    - `test_connection` MUST NOT let source-specific exceptions escape. Catch
      broad failures internally and report them via
      `ConnectionTestResult(success=False, message=...)` so callers can
      handle "source unreachable" as ordinary data, not as an exception to
      catch. This is what lets a caller probe reachability without a
      try/except around every connector implementation's own exception
      types.
    - `introspect_schema` and `execute_query` MAY raise a real exception on
      failure. They are expected to be called only after a successful
      `test_connection`, so a raised exception there is a genuine, unusual
      failure worth propagating rather than data to inspect.
    """

    @abstractmethod
    def test_connection(self) -> ConnectionTestResult:
        """Probe whether the source is reachable or with the configured credentials.

        Must never raise -- see the class docstring's contract.
        """
        raise NotImplementedError

    @abstractmethod
    def introspect_schema(self) -> list[SchemaDescriptor]:
        """Discover the schemas, tables, and columns available in the source.

        May raise on failure; see the class docstring's contract.
        """
        raise NotImplementedError

    @abstractmethod
    def execute_query(self, sql: str, params: dict[str, Any] | None = None) -> QueryResult:
        """Run a query against the source and return its results.

        May raise on failure; see the class docstring's contract.
        """
        raise NotImplementedError

    @abstractmethod
    def capabilities(self) -> ConnectorCapabilities:
        """Report what this connector's underlying source can do."""
        raise NotImplementedError
