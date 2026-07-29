"""Pydantic models used internally by the ingestion pipeline.

Only the two reference-data record types with real structure worth
validating (`Asset`, `Market`) get a full model -- everything else that
flows through `navigraph_kg.ingestion.pipeline` (channels, customer types,
risk levels, investment capacity bands, glossary rows already modeled as
`navigraph_catalog.models.ColumnGlossary`) is a single scalar value or an
existing catalog model, and wrapping those in another Pydantic model would
add ceremony without adding validation value.
"""

from __future__ import annotations

from pydantic import BaseModel


class AssetRecord(BaseModel):
    """One row of `FAR_TRANS.ASSET_INFORMATION`, as needed by the ingestion pipeline.

    `sector`/`industry` are `None` for legitimately sector-less/industry-less
    instruments -- confirmed against the real `FIDELITY_POC` data: only
    448/836 assets have a `sector` and 397/836 have an `industry` (bonds and
    MTF-type instruments legitimately have neither). The ingestion pipeline
    relies on this `None`-ness directly: it creates an `IN_SECTOR`/
    `IN_INDUSTRY` edge only when the corresponding field here is not `None`,
    per the approved design decision against fabricating placeholder
    `Sector`/`Industry` nodes.
    """

    isin: str
    # Optional, not required: real FIDELITY_POC data has at least one asset
    # with a NULL asset_name (caught running the real ingestion pipeline
    # against live Snowflake data during Phase 3 verification) -- ISIN is
    # the only field guaranteed present, since it's the natural key used
    # for MERGE.
    asset_name: str | None = None
    asset_short_name: str | None = None
    asset_category: str | None = None
    asset_sub_category: str | None = None
    market_id: str | None = None
    sector: str | None = None
    industry: str | None = None


class MarketRecord(BaseModel):
    """One row of `FAR_TRANS.MARKETS`.

    `exchange_id` is genuinely 1-to-many with `market_id` in the real data
    (e.g. exchange `ATHEX` groups markets `EBB`, `XATH`, `ENAX`) -- this
    record's `exchange_id` field is exactly what the ingestion pipeline uses
    to `MERGE` the `(:Market)-[:PART_OF_EXCHANGE]->(:Exchange)` edge.
    """

    exchange_id: str
    market_id: str
    name: str | None = None
    description: str | None = None
    country: str | None = None
    trading_days: str | None = None
    trading_hours: str | None = None
    market_class: str | None = None


class IngestionSummary(BaseModel):
    """Per-stage counts produced by `navigraph_kg.ingestion.pipeline.run_ingestion`.

    Every field defaults to `0` so a summary can be constructed
    incrementally (or left partially zero in a stage-skipping test) without
    needing every count supplied up front.
    """

    tables_synced: int = 0
    columns_synced: int = 0
    business_concepts_synced: int = 0
    concept_mappings_synced: int = 0
    assets_synced: int = 0
    markets_synced: int = 0
    exchanges_synced: int = 0
    sectors_synced: int = 0
    industries_synced: int = 0
    channels_synced: int = 0
    customer_types_synced: int = 0
    risk_levels_synced: int = 0
    investment_capacity_bands_synced: int = 0
    relationship_concepts_synced: int = 0
