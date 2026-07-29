"""NaviGraph connector SDK.

A source-agnostic plugin interface for connecting to external data sources
(Snowflake today; Postgres/REST/etc. in the future) and describing their
schemas in plain Pydantic models. This package deliberately knows nothing
about SQLAlchemy or any catalog-database concepts -- it only knows how to
talk to a data source and describe what it finds there. See `base.Connector`
for the plugin interface every connector implements and `registry` for how
concrete connectors are registered and looked up by `source_type` string.
"""

__version__ = "0.1.0"
