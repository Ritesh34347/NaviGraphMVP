"""Demo-only fallback: real, previously-captured answers for the golden
question set, served when the real data source is genuinely unreachable
(e.g. Snowflake's free trial expiring and suspending its warehouses).

This exists for exactly one reason: demoing NaviGraph must keep working
even when the underlying trial data warehouse is temporarily down for a
reason outside this project's control. It is NOT a general mock-data
feature and must never activate outside an explicit, deliberate opt-in --
`NAVIGRAPH_DEMO_FALLBACK` must be set to "true" in the environment, and
even then this only ever returns content for the exact 8 golden-set
questions it has real, previously-captured answers for (see
`demo_fallback_data.json` -- each entry's `narrative`/
`follow_up_suggestions`/`final_row_count` are copied verbatim from a real
`eval/run_harness.py` run against the live stack, not fabricated). Any
other question still gets the real, honest `data_source_unreachable`
failure -- this fallback never pretends to answer something it doesn't
have a genuine prior real answer for.

Every fallback response's narrative is prefixed with an explicit
"[Cached demo replay ...]" marker so it is never mistaken for a live
answer, in the API response and therefore in the chat UI too.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

_DATA_PATH = Path(__file__).parent / "demo_fallback_data.json"


@lru_cache(maxsize=1)
def _load_cache() -> dict[str, dict[str, Any]]:
    return json.loads(_DATA_PATH.read_text(encoding="utf-8"))


def is_enabled() -> bool:
    return os.environ.get("NAVIGRAPH_DEMO_FALLBACK", "false").strip().lower() == "true"


def match_cached_result(question: str) -> dict[str, Any] | None:
    """Case-insensitive exact match against the real golden-set question
    text this fallback has a genuine prior real answer for. Deliberately
    a small, honest heuristic (matches this codebase's established
    `_is_count_question`/`_needs_predicate_resolution` style) -- no fuzzy
    matching, since a wrong fuzzy match here would mean showing a demo
    viewer a real answer to a DIFFERENT question than the one they typed.
    """

    return _load_cache().get(question.strip().lower())


def build_fallback_narrative(cached: dict[str, Any]) -> str:
    return (
        f"[Cached demo replay -- live data source unreachable; showing a "
        f"real result captured from run {cached['captured_run_id']}] "
        f"{cached['narrative']}"
    )
