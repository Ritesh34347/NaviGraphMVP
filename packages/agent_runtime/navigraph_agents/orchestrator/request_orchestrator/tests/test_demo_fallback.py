"""Real unit tests for the demo-only Snowflake-unreachable fallback.

No network, no real infra -- pure logic against the real, committed
`demo_fallback_data.json` file.
"""

from __future__ import annotations

import os

import pytest

from navigraph_agents.orchestrator.request_orchestrator import demo_fallback


@pytest.fixture(autouse=True)
def _clear_env():
    os.environ.pop("NAVIGRAPH_DEMO_FALLBACK", None)
    demo_fallback._load_cache.cache_clear()
    yield
    os.environ.pop("NAVIGRAPH_DEMO_FALLBACK", None)


def test_disabled_by_default() -> None:
    assert demo_fallback.is_enabled() is False


def test_enabled_only_on_exact_true_string() -> None:
    os.environ["NAVIGRAPH_DEMO_FALLBACK"] = "true"
    assert demo_fallback.is_enabled() is True

    os.environ["NAVIGRAPH_DEMO_FALLBACK"] = "TRUE"
    assert demo_fallback.is_enabled() is True

    os.environ["NAVIGRAPH_DEMO_FALLBACK"] = "1"
    assert demo_fallback.is_enabled() is False


def test_matches_a_real_golden_set_question_case_insensitively() -> None:
    # gq_002's real captured narrative is genuinely empty (a real,
    # documented behavior for a 10,000-row result -- see data-flow.md) --
    # asserting question_id/row_count here, not narrative content.
    cached = demo_fallback.match_cached_result(
        "how many transactions has each customer made?"
    )
    assert cached is not None
    assert cached["question_id"] == "gq_002"
    assert cached["final_row_count"] == 10000
    assert isinstance(cached["follow_up_suggestions"], list)
    assert len(cached["follow_up_suggestions"]) > 0


def test_a_real_question_with_a_non_empty_cached_narrative() -> None:
    cached = demo_fallback.match_cached_result(
        "What is the total transaction volume by market?"
    )
    assert cached is not None
    assert cached["question_id"] == "gq_001"
    assert isinstance(cached["narrative"], str) and cached["narrative"]


def test_does_not_match_a_question_with_no_real_prior_answer() -> None:
    assert demo_fallback.match_cached_result("What is the airspeed of a swallow?") is None


def test_fallback_narrative_is_clearly_marked_as_a_cached_replay() -> None:
    cached = demo_fallback.match_cached_result(
        "How many transactions has each customer made?"
    )
    assert cached is not None
    narrative = demo_fallback.build_fallback_narrative(cached)
    assert narrative.startswith("[Cached demo replay")
    assert cached["narrative"] in narrative
