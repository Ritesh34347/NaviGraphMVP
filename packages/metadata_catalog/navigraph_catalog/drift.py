"""Schema drift detection: a stable structural hash per crawled table, so
a re-crawl can report what actually changed since the last one instead of
silently upserting over it.

The gap this closes: `upsert_schema_tree` (see `api.py`) has always been
a correct, idempotent upsert -- re-running it never duplicates rows or
corrupts state -- but it never told a caller WHAT changed. A re-crawl
scheduler (or a human re-running an onboarding CLI step) had no signal
distinguishing "nothing changed" from "this table gained/lost/retyped a
column" short of manually diffing the whole catalog. `CrawlResult
.drift_events` is that signal now.
"""

from __future__ import annotations

import hashlib
import json

from navigraph_connectors.base import TableDescriptor
from pydantic import BaseModel


def compute_table_schema_hash(table: TableDescriptor) -> str:
    """A stable SHA-256 hash of `table`'s structural shape: name plus, for
    every column, its name/data_type/nullable/ordinal_position.

    Deliberately excludes `row_count_estimate` and `description` -- both
    change constantly (row counts grow every day; descriptions get edited)
    without the table's actual STRUCTURE changing, and including them
    would make this hash change on every single crawl regardless of real
    drift, defeating its purpose. Columns are sorted by `ordinal_position`
    before hashing so this is deterministic regardless of the order
    `table.columns` happens to arrive in.
    """

    canonical = {
        "name": table.name,
        "columns": [
            {
                "name": column.name,
                "data_type": column.data_type,
                "nullable": column.nullable,
                "ordinal_position": column.ordinal_position,
            }
            for column in sorted(table.columns, key=lambda c: c.ordinal_position)
        ],
    }
    payload = json.dumps(canonical, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class SchemaDriftEvent(BaseModel):
    """One table's real drift status from a single `upsert_schema_tree` call.

    `is_new=True` means this table had no prior `schema_hash` at all (a
    genuinely new table, or the very first crawl of an existing one crawled
    before this drift-tracking mechanism existed) -- `changed` is always
    `False` for a new table, since "changed" specifically means "differs
    from what THIS same table looked like last crawl."
    """

    table_name: str
    is_new: bool
    changed: bool
    old_hash: str | None
    new_hash: str


class CrawlResult(BaseModel):
    """The real result of `navigraph_catalog.ingestion.snowflake_crawler
    .crawl_and_store` (or any future connector-specific crawler following
    the same pattern) -- table count plus real, per-table drift detail."""

    tables_synced: int
    drift_events: list[SchemaDriftEvent]

    @property
    def new_table_names(self) -> list[str]:
        return [event.table_name for event in self.drift_events if event.is_new]

    @property
    def changed_table_names(self) -> list[str]:
        """Tables that existed before AND whose structure genuinely
        differs from their last crawl -- the real "something drifted"
        signal, deliberately excluding brand-new tables (see
        `SchemaDriftEvent.is_new`)."""

        return [
            event.table_name
            for event in self.drift_events
            if event.changed and not event.is_new
        ]
