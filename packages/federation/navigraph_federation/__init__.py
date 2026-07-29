"""NaviGraph federation package.

A real Trino DB-API client (`trino_client.TrinoClient`) plus dialect-rewrite
helpers (`dialect`) that let SQL generated against NaviGraph's
dialect-neutral `SCHEMA.TABLE` convention be routed either straight at a
single tenant connector or, when a query genuinely spans more than one
registered data source, through the Trino coordinator instead -- see
`infra/trino/coordinator/` and `DECISIONS.md`'s 2026-07-28 entry ("Trino
stood up for real federation despite one registered source") for why this
cluster exists before a second real source is registered.

This package deliberately does not implement `navigraph_connectors.base
.Connector` -- Trino is a query-routing layer over other sources, not a
tenant-registered `DataSource` in its own right.
"""

__version__ = "0.1.0"
