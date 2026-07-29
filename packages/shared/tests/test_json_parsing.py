"""Real unit tests for `navigraph_shared.llm.strip_json_code_fence`.

Added in Phase 8 after a REAL bug was found live: the first-ever real
Anthropic call any LLM-backed agent in this project made (during the
evaluation harness's first live run) returned JSON wrapped in a markdown
code fence (` ```json\\n{...}\\n``` `), which every agent's
`json.loads(llm_response.text)` call failed on outright. See
`json_parsing.py`'s module docstring for the full story.
"""

from __future__ import annotations

import json

from navigraph_shared.llm import strip_json_code_fence


def test_strips_a_json_labeled_code_fence() -> None:
    text = '```json\n{\n  "intent": "comparison",\n  "entities": ["a", "b"]\n}\n```'

    result = strip_json_code_fence(text)

    assert json.loads(result) == {"intent": "comparison", "entities": ["a", "b"]}


def test_strips_a_plain_unlabeled_code_fence() -> None:
    text = '```\n{"a": 1}\n```'

    result = strip_json_code_fence(text)

    assert json.loads(result) == {"a": 1}


def test_passes_through_raw_json_unchanged() -> None:
    text = '{"a": 1}'

    result = strip_json_code_fence(text)

    assert json.loads(result) == {"a": 1}


def test_trims_surrounding_whitespace_even_without_a_fence() -> None:
    text = '\n\n  {"a": 1}  \n\n'

    result = strip_json_code_fence(text)

    assert result == '{"a": 1}'


def test_handles_a_single_line_fenced_response() -> None:
    text = '```json\n{"a": 1}\n```'

    result = strip_json_code_fence(text)

    assert json.loads(result) == {"a": 1}


def test_genuinely_malformed_json_still_fails_to_parse_after_stripping() -> None:
    """Confirms this helper does not mask a REAL malformed response -- it
    only removes the fence, it doesn't fix broken JSON inside one."""

    text = "```json\nthis is not json\n```"

    result = strip_json_code_fence(text)

    try:
        json.loads(result)
        raise AssertionError("expected a JSONDecodeError")
    except json.JSONDecodeError:
        pass
