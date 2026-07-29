"""Settings for the lineage store's Postgres connection.

Field-for-field identical to `navigraph_catalog.settings.MetadataCatalogSettings`
-- this package deliberately reuses the SAME physical Postgres instance (same
`POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`
env vars), not a new database service. Tenant isolation in this codebase is
already row-level (`tenant_id` columns, enforced by OPA + application code),
never database-level, so standing up a second Postgres for one new table
would be pure operational overhead with zero isolation benefit.
"""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings


class LineageSettings(NaviGraphSettings):
    """Connection settings for the lineage store's Postgres database."""

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
