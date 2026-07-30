"""Alembic environment script for the metadata catalog.

The DB URL is never hardcoded here or in `alembic.ini` -- it is built at
runtime from `MetadataCatalogSettings().sqlalchemy_url`, which reads
POSTGRES_HOST/POSTGRES_PORT/POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB from
the environment (or a `.env` file), matching every other NaviGraph
service's settings convention. This means `alembic upgrade head` run from
any shell with the right env vars set just works, with no manual URL
editing.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from navigraph_catalog.models import Base
from navigraph_catalog.settings import MetadataCatalogSettings
from sqlalchemy import engine_from_config, pool

# Alembic Config object, providing access to values within alembic.ini.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Metadata used for 'autogenerate' support.
target_metadata = Base.metadata

# Build the real DB URL from env-derived settings rather than whatever
# (blank) value is in alembic.ini. ConfigParser (which backs alembic's
# Config object) treats a bare "%" as the start of an interpolation
# token, so any URL whose password happens to contain "%" (a real,
# perfectly valid password character) raises "invalid interpolation
# syntax" unless literal percents are escaped as "%%" first.
config.set_main_option(
    "sqlalchemy.url", MetadataCatalogSettings().sqlalchemy_url.replace("%", "%%")
)


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
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
