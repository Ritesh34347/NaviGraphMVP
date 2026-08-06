"""Settings for the Postgres connector.

Every field has a safe default (empty string) so that importing this
module and constructing `PostgresSettings()` never crashes, even with a
completely empty environment -- matching the convention established by
`navigraph_shared.config.NaviGraphSettings` and `SnowflakeSettings`.

REAL COLLISION AVOIDED, found while designing this: NaviGraph's OWN
internal metadata catalog already has a `MetadataCatalogSettings` reading
BARE `postgres_host`/`postgres_user`/`postgres_password`/`postgres_db` env
vars (`POSTGRES_HOST`/`POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB`,
see `navigraph_catalog.settings`) for NaviGraph's own catalog database --
a COMPLETELY different Postgres instance than the one a tenant's
"postgres" data SOURCE connector would point at. Using the same field/env
var names here would either silently point this connector at NaviGraph's
own internal catalog DB, or have the two settings classes fight over the
same env vars in whichever process constructs both. Every field here is
prefixed `source_postgres_*` (`SOURCE_POSTGRES_*` env vars) specifically
to keep these two Postgres connections unambiguous -- "the external data
source this connector talks to," never NaviGraph's own catalog storage.
"""

from __future__ import annotations

from navigraph_shared.config import NaviGraphSettings


class PostgresSettings(NaviGraphSettings):
    """Connection settings for `PostgresConnector` (a tenant's external
    Postgres data source, not NaviGraph's own internal catalog database)."""

    source_postgres_host: str = ""
    source_postgres_port: int = 5432
    source_postgres_database: str = ""
    source_postgres_user: str = ""
    source_postgres_password: str = ""
    source_postgres_sslmode: str = "prefer"
