"""Integration test: runs the real Alembic migration against a real Postgres.

REQUIRES A LIVE, REACHABLE POSTGRES -- this test does NOT skip gracefully if
one isn't available. That is intentional: this repo's `tests/integration/`
tier is documented as running against the actual docker-compose stack in a
separate CI job (see the top-level `tests/integration/.gitkeep` and this
package's sibling unit tests, which are the DB-free tier). A hard failure
here when Postgres is unreachable is the correct, expected behavior for this
tier, matching that established convention -- not a bug to work around with
a skip.

Point this at a real Postgres via the same env vars every other NaviGraph
service uses (`POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_DB`) -- `MetadataCatalogSettings()` picks
these up automatically. Defaults to `postgres:5432` (the docker-compose
service's in-network hostname); when running this test from the host against
`infra/docker-compose.yml`'s published port, set `POSTGRES_HOST=localhost`
(and the matching `POSTGRES_USER`/`POSTGRES_PASSWORD`/`POSTGRES_DB` from
`infra/.env`) first.

This test:
  1. Runs `alembic upgrade head` programmatically against the real database.
  2. Connects with SQLAlchemy and asserts all four catalog tables exist with
     their expected columns.
  3. Runs `alembic downgrade base` to prove the migration's `downgrade()`
     path is real and correct, not just written for show -- then asserts
     the tables are gone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from navigraph_catalog.settings import MetadataCatalogSettings

_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "packages" / "metadata_catalog"

_EXPECTED_TABLES_AND_COLUMNS = {
    "data_sources": {
        "id",
        "tenant_id",
        "name",
        "source_type",
        "connection_ref",
        "created_at",
    },
    "catalog_schemas": {"id", "data_source_id", "name"},
    "catalog_tables": {"id", "schema_id", "name", "description", "row_count_estimate"},
    "catalog_columns": {
        "id",
        "table_id",
        "name",
        "data_type",
        "nullable",
        "ordinal_position",
        "description",
    },
}


def _alembic_config(settings: MetadataCatalogSettings) -> Config:
    config = Config(str(_PACKAGE_ROOT / "alembic.ini"))
    # Absolute path, not the ini's relative "migrations" -- this test's cwd
    # is not guaranteed to be packages/metadata_catalog.
    config.set_main_option("script_location", str(_PACKAGE_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", settings.sqlalchemy_url)
    return config


@pytest.mark.postgres_integration
def test_upgrade_head_creates_expected_tables_then_downgrade_removes_them() -> None:
    settings = MetadataCatalogSettings()
    engine = sa.create_engine(settings.sqlalchemy_url)

    # Fail loudly (not skip) if Postgres isn't reachable -- see module
    # docstring for why that's the correct behavior for this tier.
    with engine.connect() as connection:
        connection.execute(sa.text("SELECT 1"))

    config = _alembic_config(settings)

    try:
        command.upgrade(config, "head")

        inspector = sa.inspect(engine)
        actual_tables = set(inspector.get_table_names())
        for table_name in _EXPECTED_TABLES_AND_COLUMNS:
            assert table_name in actual_tables, f"{table_name} missing after upgrade head"

        for table_name, expected_columns in _EXPECTED_TABLES_AND_COLUMNS.items():
            actual_columns = {col["name"] for col in inspector.get_columns(table_name)}
            assert expected_columns <= actual_columns, (
                f"{table_name} missing columns: {expected_columns - actual_columns}"
            )

        # Foreign keys with ON DELETE CASCADE, spot-checked on the leaf table.
        fks = inspector.get_foreign_keys("catalog_columns")
        assert any(
            fk["referred_table"] == "catalog_tables" and fk.get("options", {}).get("ondelete")
            == "CASCADE"
            for fk in fks
        )
    finally:
        # Prove downgrade() is real, not just written for show -- and leave
        # the database clean regardless of whether the assertions above
        # passed.
        command.downgrade(config, "base")

    inspector = sa.inspect(engine)
    remaining = set(inspector.get_table_names())
    for table_name in _EXPECTED_TABLES_AND_COLUMNS:
        assert table_name not in remaining, f"{table_name} still present after downgrade base"
