"""Data Source Discovery agent: resolves the bare table names produced by
Schema Mapping to the concrete, registered `DataSource` rows that own them,
and probes each distinct data source's real connectivity. Fully
deterministic -- no LLM call -- see agent.py."""
