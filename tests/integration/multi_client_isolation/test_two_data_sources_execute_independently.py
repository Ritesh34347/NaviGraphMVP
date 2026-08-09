"""Integration test: Phase 11's own stated exit criterion, proven for real.

Phase 11 ("multi-client trust foundation") was scoped with this exact exit
criterion: register a second, synthetic `DataSource` under different fake
credentials, in the same running service, and prove both resolve and
execute independently -- the real test that two actual clients could be
served concurrently. No live second Snowflake account exists to prove this
against (this repo has never had more than one), so this test proves it
against two real, independently-provisioned local Postgres databases
instead -- mirroring this project's established precedent of substituting
a real Postgres backend for Snowflake wherever a second live Snowflake
account isn't available (see `LIMITATIONS.md` item 1's Postgres connector).

REQUIRES real, reachable Postgres databases -- not skipped gracefully,
same stance as every other `postgres_integration`-marked test in this repo.
Provisioning used for this test (run once, by hand, against a real local
Postgres 16 instance):

    CREATE ROLE navigraph_catalog_test LOGIN PASSWORD 'navigraph_catalog_test_pw';
    CREATE ROLE client_a_role LOGIN PASSWORD 'client-a-secret-pw';
    CREATE ROLE client_b_role LOGIN PASSWORD 'client-b-secret-pw';
    CREATE DATABASE navigraph_catalog_test OWNER navigraph_catalog_test;
    CREATE DATABASE client_a_sample OWNER client_a_role;
    CREATE DATABASE client_b_sample OWNER client_b_role;
    -- in client_a_sample, as client_a_role:
    CREATE TABLE summary (label text); INSERT INTO summary VALUES ('CLIENT_A_MARKER');
    -- in client_b_sample, as client_b_role:
    CREATE TABLE summary (label text); INSERT INTO summary VALUES ('CLIENT_B_MARKER');

What this actually proves, real end to end, in ONE running
`DataFederationAgent` instance (the "same running service"):
  1. Two `DataSource` rows, same `source_type="postgres"`, same tenant,
     with DIFFERENT `connection_ref.secret_scope` values, are registered
     in a real metadata catalog.
  2. A single `EnvVarSecretsProvider` holds BOTH scopes' real (if
     locally-fake) credentials -- host/port/database/user/password -- with
     genuinely different values per scope, exactly as LIMITATIONS.md item
     21 required.
  3. `DataFederationAgent.run()`, given both plans in the SAME call, uses
     `navigraph_connectors.registry.build_connector` to resolve two
     DISTINCT `PostgresConnector` instances from two DISTINCT
     `PostgresSettings`, and executes a real query against EACH real
     database -- proven not by inspecting settings objects (Phase 11 part
     1's unit tests already did that) but by the actual returned ROWS: A's
     result contains only `CLIENT_A_MARKER`, B's contains only
     `CLIENT_B_MARKER`, never crossed or shared.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

# Import side effect only: registers "postgres" in
# `navigraph_connectors.registry`, including its settings_factory.
import navigraph_connectors.postgres  # noqa: F401
from navigraph_agents.query.data_federation.agent import DataFederationAgent
from navigraph_agents.query.data_federation.contracts import (
    DataFederationInput,
    DataFederationPayload,
    ExecutionPlan,
)
from navigraph_catalog.api import register_data_source
from navigraph_catalog.db import get_engine, get_session_factory, session_scope
from navigraph_catalog.settings import MetadataCatalogSettings
from navigraph_shared.contracts import RequestContext
from navigraph_shared.secrets import EnvVarSecretsProvider

pytestmark = pytest.mark.postgres_integration

_TENANT_ID = "multi-client-isolation-test-tenant"
_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "packages" / "metadata_catalog"


@pytest.fixture()
def catalog_session_factory(monkeypatch: pytest.MonkeyPatch) -> Iterator[sa.orm.sessionmaker]:
    """Runs the real Alembic migration up before the test, tears the
    schema back down after -- mirrors
    `tests/integration/metadata_catalog/test_migrations.py`'s pattern, so
    this test never depends on some OTHER test/session having already
    migrated this database, and never leaves rows behind for the next run.

    Sets `POSTGRES_*` env vars (rather than passing explicit
    `MetadataCatalogSettings` kwargs) because `migrations/env.py` always
    rebuilds its own `MetadataCatalogSettings()` from the environment,
    ignoring whatever URL a caller sets on the Alembic `Config` object --
    the same reason `test_migrations.py` points itself at a real database
    via env vars, not constructor kwargs."""

    monkeypatch.setenv("POSTGRES_HOST", "localhost")
    monkeypatch.setenv("POSTGRES_PORT", "5432")
    monkeypatch.setenv("POSTGRES_USER", "navigraph_catalog_test")
    monkeypatch.setenv("POSTGRES_PASSWORD", "navigraph_catalog_test_pw")
    monkeypatch.setenv("POSTGRES_DB", "navigraph_catalog_test")

    settings = MetadataCatalogSettings()
    engine = get_engine(settings)

    with engine.connect() as connection:
        connection.execute(sa.text("SELECT 1"))

    config = Config(str(_PACKAGE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_PACKAGE_ROOT / "migrations"))
    command.upgrade(config, "head")

    try:
        yield get_session_factory(engine)
    finally:
        command.downgrade(config, "base")


def _request_context() -> RequestContext:
    return RequestContext(
        tenant_id=_TENANT_ID,
        user_id="integration-test-user",
        trace_id=f"multi-client-isolation-{uuid.uuid4()}",
        roles=["analyst"],
    )


@pytest.mark.asyncio
async def test_two_postgres_data_sources_resolve_and_execute_independently(
    monkeypatch: pytest.MonkeyPatch, catalog_session_factory: sa.orm.sessionmaker
) -> None:
    # --- Real, genuinely distinct credentials for two scopes, held by ONE
    # secrets provider instance -- exactly what LIMITATIONS.md item 21
    # required two DataSource rows of the same source_type to have. ---
    monkeypatch.setenv("CLIENT_A_PG_HOST", "localhost")
    monkeypatch.setenv("CLIENT_A_PG_PORT", "5432")
    monkeypatch.setenv("CLIENT_A_PG_DATABASE", "client_a_sample")
    monkeypatch.setenv("CLIENT_A_PG_USER", "client_a_role")
    monkeypatch.setenv("CLIENT_A_PG_PASSWORD", "client-a-secret-pw")

    monkeypatch.setenv("CLIENT_B_PG_HOST", "localhost")
    monkeypatch.setenv("CLIENT_B_PG_PORT", "5432")
    monkeypatch.setenv("CLIENT_B_PG_DATABASE", "client_b_sample")
    monkeypatch.setenv("CLIENT_B_PG_USER", "client_b_role")
    monkeypatch.setenv("CLIENT_B_PG_PASSWORD", "client-b-secret-pw")

    secrets = EnvVarSecretsProvider()

    # --- Register both DataSource rows for the SAME tenant, same
    # source_type, in a real catalog database. ---
    session_factory = catalog_session_factory

    with session_scope(session_factory) as session:
        data_source_a = register_data_source(
            session,
            tenant_id=_TENANT_ID,
            name=f"client-a-{uuid.uuid4()}",
            source_type="postgres",
            connection_ref={"secret_scope": "client_a_pg"},
        )
        data_source_b = register_data_source(
            session,
            tenant_id=_TENANT_ID,
            name=f"client-b-{uuid.uuid4()}",
            source_type="postgres",
            connection_ref={"secret_scope": "client_b_pg"},
        )
        data_source_a_id = str(data_source_a.id)
        data_source_b_id = str(data_source_b.id)

    # --- ONE DataFederationAgent instance -- "the same running service" --
    # handed BOTH plans in a SINGLE run() call. ---
    agent = DataFederationAgent(catalog_session_factory=session_factory, secrets=secrets)

    plan_a = ExecutionPlan(
        data_source_id=data_source_a_id,
        route="direct_connector",
        sql="SELECT label FROM summary",
        params={},
        timeout_seconds=30,
        max_rows=100,
        read_only_verified=True,
    )
    plan_b = ExecutionPlan(
        data_source_id=data_source_b_id,
        route="direct_connector",
        sql="SELECT label FROM summary",
        params={},
        timeout_seconds=30,
        max_rows=100,
        read_only_verified=True,
    )

    output = await agent.run(
        DataFederationInput(
            request_context=_request_context(),
            payload=DataFederationPayload(plans=[plan_a, plan_b]),
        )
    )

    assert output.errors == [], f"unexpected errors: {output.errors}"
    assert len(output.result.per_source_results) == 2

    result_by_source = {r.data_source_id: r for r in output.result.per_source_results}

    result_a = result_by_source[data_source_a_id]
    result_b = result_by_source[data_source_b_id]

    # The real proof: each data source's real, independently-resolved
    # connector genuinely executed against ITS OWN real database -- never
    # the other one's, and never some shared/collapsed connection.
    assert result_a.rows == [{"label": "CLIENT_A_MARKER"}]
    assert result_b.rows == [{"label": "CLIENT_B_MARKER"}]
    assert result_a.route_used == "direct_connector"
    assert result_b.route_used == "direct_connector"


@pytest.mark.asyncio
async def test_swapped_credentials_would_prove_isolation_is_real_not_coincidental(
    monkeypatch: pytest.MonkeyPatch, catalog_session_factory: sa.orm.sessionmaker
) -> None:
    """Adversarial companion to the test above: deliberately register the
    two `DataSource` rows with their `secret_scope`s SWAPPED relative to
    which real database each name suggests, and confirm the result still
    tracks the real `connection_ref` (not the name, not registration
    order) -- proving resolution genuinely keys off `connection_ref
    .secret_scope`, not an incidental correlation with insertion order."""

    monkeypatch.setenv("CLIENT_A_PG_HOST", "localhost")
    monkeypatch.setenv("CLIENT_A_PG_PORT", "5432")
    monkeypatch.setenv("CLIENT_A_PG_DATABASE", "client_a_sample")
    monkeypatch.setenv("CLIENT_A_PG_USER", "client_a_role")
    monkeypatch.setenv("CLIENT_A_PG_PASSWORD", "client-a-secret-pw")

    monkeypatch.setenv("CLIENT_B_PG_HOST", "localhost")
    monkeypatch.setenv("CLIENT_B_PG_PORT", "5432")
    monkeypatch.setenv("CLIENT_B_PG_DATABASE", "client_b_sample")
    monkeypatch.setenv("CLIENT_B_PG_USER", "client_b_role")
    monkeypatch.setenv("CLIENT_B_PG_PASSWORD", "client-b-secret-pw")

    secrets = EnvVarSecretsProvider()
    session_factory = catalog_session_factory

    with session_scope(session_factory) as session:
        # Registered in reverse -- "first_registered" points at scope B,
        # "second_registered" points at scope A.
        first_registered = register_data_source(
            session,
            tenant_id=_TENANT_ID,
            name=f"first-registered-{uuid.uuid4()}",
            source_type="postgres",
            connection_ref={"secret_scope": "client_b_pg"},
        )
        second_registered = register_data_source(
            session,
            tenant_id=_TENANT_ID,
            name=f"second-registered-{uuid.uuid4()}",
            source_type="postgres",
            connection_ref={"secret_scope": "client_a_pg"},
        )
        first_registered_id = str(first_registered.id)
        second_registered_id = str(second_registered.id)

    agent = DataFederationAgent(catalog_session_factory=session_factory, secrets=secrets)

    output = await agent.run(
        DataFederationInput(
            request_context=_request_context(),
            payload=DataFederationPayload(
                plans=[
                    ExecutionPlan(
                        data_source_id=first_registered_id,
                        route="direct_connector",
                        sql="SELECT label FROM summary",
                        params={},
                        timeout_seconds=30,
                        max_rows=100,
                        read_only_verified=True,
                    ),
                    ExecutionPlan(
                        data_source_id=second_registered_id,
                        route="direct_connector",
                        sql="SELECT label FROM summary",
                        params={},
                        timeout_seconds=30,
                        max_rows=100,
                        read_only_verified=True,
                    ),
                ]
            ),
        )
    )

    assert output.errors == []
    result_by_source = {r.data_source_id: r for r in output.result.per_source_results}

    # "first_registered" (name suggests A, but its real connection_ref
    # points at scope B) must resolve to B's real data -- proving
    # resolution follows connection_ref, not name or insertion order.
    assert result_by_source[first_registered_id].rows == [{"label": "CLIENT_B_MARKER"}]
    assert result_by_source[second_registered_id].rows == [{"label": "CLIENT_A_MARKER"}]
