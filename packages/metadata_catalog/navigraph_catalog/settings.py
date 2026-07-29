"""Settings for the metadata catalog's Postgres connection.

Every field has a safe default so that importing this module and
constructing `MetadataCatalogSettings()` never crashes, even with a
completely empty environment -- matching the convention established by
`navigraph_shared.config.NaviGraphSettings`. Real values are supplied via
env vars (or a `.env` file) in every real deployment.

Field names map to env vars by uppercasing, exactly like
`NaviGraphSettings.anthropic_api_key` maps to `ANTHROPIC_API_KEY`:
`postgres_user` -> `POSTGRES_USER`, `postgres_password` ->
`POSTGRES_PASSWORD`, `postgres_db` -> `POSTGRES_DB` -- the exact same names
used in `infra/.env.example` and `infra/docker-compose.yml`'s `postgres`
service, so this package picks up the same `.env` with zero renaming.
"""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings


class MetadataCatalogSettings(NaviGraphSettings):
    """Connection settings for the metadata catalog's Postgres database."""

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_user: str = ""
    postgres_password: str = ""
    postgres_db: str = ""

    @property
    def sqlalchemy_url(self) -> str:
        """The SQLAlchemy connection URL for this database, using psycopg 3."""

        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )
