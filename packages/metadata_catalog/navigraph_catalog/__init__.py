"""NaviGraph metadata catalog package.

A Postgres-backed catalog of RAW schema structure -- data sources, schemas,
tables, and columns -- crawled from external data sources via
`navigraph_connectors.Connector` implementations (see `navigraph-connector-
sdk`). This package deliberately does NOT model a business glossary or
ontology mapping (no semantic/embedding fields, no term tables); that is a
later phase's responsibility.
"""

__version__ = "0.1.0"
