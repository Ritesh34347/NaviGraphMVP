#!/usr/bin/env python3
"""Human-run backfill: tag real, confirmed PII-shaped columns in the
metadata catalog as `is_pii = true`.

This is a deliberate, manual/scripted data-curation step -- NOT a naming
heuristic run automatically at crawl time (see
`navigraph_catalog.models.CatalogColumn.is_pii`'s docstring). Run the
read-only discovery query documented in that same docstring / this
phase's plan first, confirm the real column names against your actual
data, THEN pass that confirmed list here.

Usage:
    python tools/scripts/tag_pii_columns.py \\
        --tenant-id navikenz-poc \\
        --data-source-name fidelity_poc_snowflake_v2 \\
        --table CUSTOMER_INFORMATION \\
        --columns FIRSTNAME LASTNAME EMAIL PHONE

Idempotent -- safe to re-run for the same table/columns; already-tagged
rows are simply matched again, not duplicated or errored on.
"""

from __future__ import annotations

import argparse
import sys

from navigraph_catalog.api import list_data_sources, mark_columns_pii
from navigraph_catalog.db import get_engine, get_session_factory, session_scope
from navigraph_catalog.settings import MetadataCatalogSettings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--data-source-name", required=True)
    parser.add_argument("--table", required=True, help="Table name (case-insensitive)")
    parser.add_argument(
        "--columns",
        required=True,
        nargs="+",
        help="One or more column names to tag as PII (case-insensitive)",
    )
    args = parser.parse_args()

    settings = MetadataCatalogSettings()
    engine = get_engine(settings)
    session_factory = get_session_factory(engine)

    with session_scope(session_factory) as session:
        data_sources = list_data_sources(session, tenant_id=args.tenant_id)
        matching = [ds for ds in data_sources if ds.name == args.data_source_name]
        if not matching:
            print(
                f"No data source named {args.data_source_name!r} for tenant "
                f"{args.tenant_id!r}.",
                file=sys.stderr,
            )
            return 1
        data_source_id = matching[0].id

        matched_count = mark_columns_pii(
            session,
            data_source_id=data_source_id,
            table_name=args.table,
            column_names=args.columns,
        )

    print(
        f"Tagged is_pii=true on {matched_count} column(s) in "
        f"{args.data_source_name}.{args.table} (requested {len(args.columns)}: "
        f"{args.columns})."
    )
    if matched_count < len(args.columns):
        print(
            f"WARNING: only {matched_count} of {len(args.columns)} requested column "
            "names were found in the catalog -- check spelling/casing against a real "
            "catalog query before assuming the rest don't exist.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
