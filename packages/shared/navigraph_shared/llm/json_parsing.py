"""Shared helper for parsing structured JSON out of a real LLM response.

REAL BUG FOUND AND FIXED live, during Phase 8's evaluation harness's first
real Anthropic call (every prior LLM-backed agent in this project had only
ever been exercised against `FakeLLMClient` in the unit-test tier, or
skipped in the optional `llm_integration` tier for lack of a real
`ANTHROPIC_API_KEY` -- this was the first real end-to-end run against a
real model): every LLM-backed agent's `_parse_llm_response` calls
`json.loads(llm_response.text)` directly, assuming the model returns raw
JSON with nothing else. The real Claude model (`claude-sonnet-5`) does not
do that even when a system prompt explicitly asks for "strict JSON" --
observed real response for Intent Understanding's exact prompt:

    '```json\\n{\\n  "intent": "comparison", ...\\n}\\n```'

`json.loads()` on that raw text fails immediately (`Expecting value: line 1
column 1`), which every agent's existing malformed-response handling then
correctly treated as a parse failure -- but the "failure" was really this
parsing gap, not a genuinely malformed model response. Every one of the 7
LLM-backed agents in this codebase (Conversation, Intent Understanding,
Semantic Retrieval, SQL Generation, Grounded Narrative Generation,
Follow-up Suggestion, Evaluation Judge) had the identical gap, since they
all share the same `json.loads(llm_response.text)` pattern -- fixed once,
here, rather than seven times inconsistently.
"""

from __future__ import annotations

import re

_JSON_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)


def strip_json_code_fence(text: str) -> str:
    """Strip a leading/trailing markdown code fence (```` ```json ... ``` ````
    or plain ```` ``` ... ``` ````) from `text`, if the ENTIRE (stripped)
    text is wrapped in one. Returns `text` unchanged (just whitespace-
    trimmed) if no such fence wraps the whole response, so this is always
    safe to call before `json.loads()` regardless of whether the model
    fenced its output this time or not.

    Deliberately scoped to the exact real pattern observed (the whole
    response wrapped in one fence) rather than attempting to strip
    arbitrary leading/trailing prose around embedded JSON -- a response
    that doesn't match this exact shape is passed through unchanged and
    still goes through each agent's existing, real `json.JSONDecodeError`
    handling, which was already documented and tested; this helper only
    removes the one real, comprehensively-observed obstacle, not every
    conceivable malformed shape.
    """

    stripped = text.strip()
    match = _JSON_CODE_FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped
