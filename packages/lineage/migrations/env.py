"""Alembic environment script for the lineage store.

The DB URL is never hardcoded here or in `alembic.ini` -- it is built at
runtime from `LineageSettings().sqlalchemy_url`, matching every other
NaviGraph service's settings convention.

IMPORTANT, real, concrete collision this file exists to avoid: this
package's migrations target the SAME physical Postgres database as
`navigraph_catalog`'s (see `navigraph_lineage.settings`'s module docstring
for why). Alembic tracks "which revision is applied" in a table named
`alembic_version` by default -- if this package used that same default
name, its independent revision chain (starting at its own `0001`) would
collide with `navigraph_catalog`'s already-applied revision chain in the
SAME table the moment both are migrated against the same database. Both
`context.configure(...)` calls below explicitly set a distinct
`version_table="alembic_version_lineage"` to avoid this.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from navigraph_lineage.models import Base
from navigraph_lineage.settings import LineageSettings
from sqlalchemy import engine_from_config, pool

# Alembic Config object, providing access to values within alembic.ini.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata used for 'autogenerate' support.
target_metadata = Base.metadata

# Build the real DB URL from env-derived settings rather than whatever
# (blank) value is in alembic.ini.
config.set_main_option("sqlalchemy.url", LineageSettings().sqlalchemy_url)

# See this module's docstring: a distinct version table, not the default
# `alembic_version`, since this package shares a physical database with
# `navigraph_catalog`'s own independent revision chain.
_VERSION_TABLE = "alembic_version_lineage"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    Configures the context with just a URL and not an Engine -- calls to
    `context.execute()` here emit the given string to the script output.
    """

    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=_VERSION_TABLE,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    Creates an Engine and associates a connection with the context.
    """

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=_VERSION_TABLE,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
