"""Adversarial test: the PII Exposure Checker agent's own enforcement
against real, live-tagged catalog data.

This goes beyond `tests/security/README.md`'s three OPA-specific minimums
-- per the user's standing rule ("never mark a security-relevant component
done without an adversarial test"), the PII Exposure Checker is its own
distinct enforcement layer (see DECISIONS.md) and needs its own real
adversarial coverage, not just the OPA-focused tests above.

Runs against the real, live docker-compose Postgres catalog, using the
real column Phase 6's `tools/scripts/tag_pii_columns.py` backfill actually
tagged (`CUSTOMER_INFORMATION.CUSTOMERID`, `is_pii=true`) -- not a
synthetic/fabricated column reference.

Point this at the real Postgres via `POSTGRES_HOST`/`POSTGRES_PORT` (see
`tests/integration/query_pipeline/`'s identical convention).
"""

from __future__ import annotations

import navigraph_connectors.snowflake  # noqa: F401 -- unused directly, but registers "snowflake"
import pytest
from navigraph_agents.guardrail.pii_exposure_checker.agent import (
    PiiExposureCheckerAgent,
)
from navigraph_agents.guardrail.pii_exposure_checker.contracts import (
    GeneratedSql,
    PiiExposureCheckerInput,
    PiiExposureCheckerPayload,
)
from navigraph_catalog.api import list_data_sources
from navigraph_catalog.db import get_engine, get_session_factory, session_scope
from navigraph_catalog.settings import MetadataCatalogSettings
from navigraph_shared.contracts import RequestContext

pytestmark = pytest.mark.postgres_integration

_TENANT_ID = "navikenz-poc"


def _real_pii_statement(data_source_id: str) -> GeneratedSql:
    return GeneratedSql(
        data_source_id=data_source_id,
        sql="SELECT CUSTOMER_INFORMATION.CUSTOMERID FROM CUSTOMER_INFORMATION",
        params={},
        referenced_tables=["CUSTOMER_INFORMATION"],
        referenced_columns=["CUSTOMER_INFORMATION.CUSTOMERID"],
    )


def _request_context(roles: list[str]) -> RequestContext:
    return RequestContext(
        tenant_id=_TENANT_ID,
        user_id="adversarial-pii-test-user",
        trace_id="adversarial-pii-exposure",
        roles=roles,
        claims={"tenant_id": _TENANT_ID},
    )


@pytest.mark.asyncio
async def test_unauthorized_role_is_denied_access_to_a_real_tagged_pii_column() -> None:
    catalog_settings = MetadataCatalogSettings()
    session_factory = get_session_factory(get_engine(catalog_settings))

    with session_scope(session_factory) as session:
        data_sources = list_data_sources(session, tenant_id=_TENANT_ID)
        assert data_sources, f"no data sources registered for tenant {_TENANT_ID!r}"
        # Either registered data source has the real Phase 6 PII backfill
        # applied (see BUILD_LOG.md) -- use whichever this tenant has.
        data_source_id = str(data_sources[0].id)

    agent = PiiExposureCheckerAgent(session_factory=session_factory)

    analyst_output = await agent.run(
        PiiExposureCheckerInput(
            request_context=_request_context(roles=["analyst"]),
            payload=PiiExposureCheckerPayload(
                statements=[_real_pii_statement(data_source_id)]
            ),
        )
    )

    assert analyst_output.result.cleared == [], (
        "an 'analyst' role must never be cleared to see a real, catalog-tagged "
        "PII column"
    )
    assert len(analyst_output.result.rejected) == 1
    assert analyst_output.result.rejected[0].code == "pii_column_access_denied"


@pytest.mark.asyncio
async def test_authorized_role_is_cleared_for_the_same_real_statement() -> None:
    """Control case, same real catalog data: a `pii_viewer` role must be
    cleared for the identical statement the previous test denied to
    `analyst` -- proves the denial is role-based, not a blanket block."""

    catalog_settings = MetadataCatalogSettings()
    session_factory = get_session_factory(get_engine(catalog_settings))

    with session_scope(session_factory) as session:
        data_sources = list_data_sources(session, tenant_id=_TENANT_ID)
        data_source_id = str(data_sources[0].id)

    agent = PiiExposureCheckerAgent(session_factory=session_factory)

    pii_viewer_output = await agent.run(
        PiiExposureCheckerInput(
            request_context=_request_context(roles=["pii_viewer"]),
            payload=PiiExposureCheckerPayload(
                statements=[_real_pii_statement(data_source_id)]
            ),
        )
    )

    assert pii_viewer_output.result.rejected == []
    assert len(pii_viewer_output.result.cleared) == 1
