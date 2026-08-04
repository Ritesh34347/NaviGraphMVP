"""Real SQL run against Snowflake for the reference-data ingestion stage.

Module-level SQL constants, mirroring
`navigraph_connectors.snowflake.connector`'s `_TABLES_QUERY`/`_COLUMNS_QUERY`
style -- kept in their own module (rather than inlined in
`navigraph_kg.ingestion.pipeline`) since `pipeline.py` already has plenty
going on across its four stages, and these five queries are exactly the
real, live-verified shape of the `FIDELITY_POC` `FAR_TRANS` schema described
in this phase's domain context: `SELECT DISTINCT` against
`ASSET_INFORMATION`, `MARKETS`, `TRANSACTIONS`, and `CUSTOMER_INFORMATION`.

Column names are written in lowercase here (Snowflake is case-insensitive
for unquoted identifiers), but `navigraph_kg.ingestion.pipeline` reads
`QueryResult.rows` dict keys in Snowflake's own uppercased-by-default form
(`"ISIN"`, `"ASSETNAME"`, ...) -- matching
`navigraph_connectors.snowflake.connector.SnowflakeConnector.execute_query`'s
behavior, which reports column names verbatim from `cursor.description`.
"""

from __future__ import annotations

ASSET_INFORMATION_QUERY = """
    SELECT DISTINCT
        isin,
        assetname,
        assetshortname,
        assetcategory,
        assetsubcategory,
        marketid,
        sector,
        industry
    FROM far_trans.asset_information
"""

MARKETS_QUERY = """
    SELECT DISTINCT
        exchangeid,
        marketid,
        name,
        description,
        country,
        tradingdays,
        tradinghours,
        marketclass
    FROM far_trans.markets
"""

# `channel`/`customertype`/`risklevel`/`investmentcapacity` are simple,
# independent Tier-1 lookup values (see `ontology.py`'s module docstring) --
# each of these four queries feeds exactly one node label, with no
# relationship of its own beyond existing as a resolvable reference value.
DISTINCT_CHANNELS_QUERY = """
    SELECT DISTINCT channel
    FROM far_trans.transactions
    WHERE channel IS NOT NULL
"""

DISTINCT_CUSTOMER_TYPES_QUERY = """
    SELECT DISTINCT customertype
    FROM far_trans.customer_information
    WHERE customertype IS NOT NULL
"""

DISTINCT_RISK_LEVELS_QUERY = """
    SELECT DISTINCT risklevel
    FROM far_trans.customer_information
    WHERE risklevel IS NOT NULL
"""

DISTINCT_INVESTMENT_CAPACITY_QUERY = """
    SELECT DISTINCT investmentcapacity
    FROM far_trans.customer_information
    WHERE investmentcapacity IS NOT NULL
"""

# ECOMMERCE_POC (tenant_id="ecommerce-poc") -- a second, separate real data
# source (see BUILD_LOG.md's 2026-08-04 e-commerce entry). `Channel` is
# already a generic, tenant-scoped Tier-1 label (see `ontology.py`), so its
# real e-commerce values (Website, Mobile App, Marketplace, In-Store) are
# crawled into the SAME node label as the brokerage dataset's channel
# values -- `tenant_id` scoping keeps the two datasets' values from ever
# colliding or cross-matching. This is what lets a question naming a
# specific channel by name (e.g. "orders via the Mobile App") resolve via
# `entity_matches_reference_node`, not just the literal word "channel" --
# the same real gap `entity_matches_reference_node` was built to close for
# the brokerage dataset's named markets (see its own docstring, item 86).
# Queried against the connection's default database (ECOMMERCE_POC, set by
# the connector this ingestion run is constructed with -- see
# `ingestion.pipeline.run_ecommerce_ingestion`'s docstring), so only the
# schema/table need qualifying here, not the database.
DISTINCT_ECOMMERCE_CHANNELS_QUERY = """
    SELECT DISTINCT channel_name AS channel
    FROM core.dim_channel
    WHERE channel_name IS NOT NULL
"""
