"""Real SQL run against Snowflake for the reference-data ingestion stage's
Asset/Market crawl.

Module-level SQL constants, mirroring
`navigraph_connectors.snowflake.connector`'s `_TABLES_QUERY`/`_COLUMNS_QUERY`
style -- kept in their own module (rather than inlined in
`navigraph_kg.ingestion.pipeline`) since `pipeline.py` already has plenty
going on across its four stages. These two queries are exactly the real,
live-verified shape of the `FIDELITY_POC` `FAR_TRANS` schema described in
this phase's domain context: `SELECT DISTINCT` against `ASSET_INFORMATION`
and `MARKETS`.

The four simple, uniform "distinct lookup values" queries this module used
to also hardcode here (`channel`/`customertype`/`risklevel`/
`investmentcapacity`) are retired -- `navigraph_kg.ingestion.pipeline
._distinct_values_query` now builds that exact SQL shape dynamically from a
per-tenant `navigraph_semantic_model.SemanticModel`'s `reference_lookups`
instead (LIMITATIONS.md item 38's structural fix; see that module's
docstring for why Asset/Market's richer, edge-producing crawl logic was
deliberately NOT generalized in the same pass).

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
