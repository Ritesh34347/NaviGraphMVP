"""Settings for the knowledge graph's Neo4j connection.

Every field has a safe default so that importing this module and
constructing `KnowledgeGraphSettings()` never crashes, even with a
completely empty environment -- matching the convention established by
`navigraph_shared.config.NaviGraphSettings`. Real values are supplied via
env vars (or a `.env` file) in every real deployment.

Field names map to env vars by uppercasing, exactly like
`NaviGraphSettings.anthropic_api_key` maps to `ANTHROPIC_API_KEY`:
`neo4j_uri` -> `NEO4J_URI`, `neo4j_user` -> `NEO4J_USER`, `neo4j_password` ->
`NEO4J_PASSWORD`. That last one is deliberate, not incidental:
`infra/docker-compose.yml`'s `neo4j` service already reads
`NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}` from `infra/.env` to set the
container's own auth password, so `neo4j_password` reads that SAME
`NEO4J_PASSWORD` env var rather than inventing a new name -- this package
picks up the same `.env` with zero renaming, matching
`MetadataCatalogSettings`'s `postgres_*` fields' relationship to
`infra/docker-compose.yml`'s `postgres` service.
"""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings


class KnowledgeGraphSettings(NaviGraphSettings):
    """Connection settings for the Neo4j-backed knowledge graph."""

    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
