"""Real unit tests for `SlackBot.handle_app_mention`, no live gateway or
Slack needed: both `httpx.AsyncClient`s it holds are built with
`httpx.MockTransport`, so every request/response actually flows through
real httpx request construction, not a stub of `handle_app_mention`
itself.

`asyncio_mode = "auto"` is set in pyproject.toml, so these `async def
test_...` functions run without an explicit `@pytest.mark.asyncio`
decorator.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from navigraph_slack_bot.bot import SlackBot, strip_mention, summarize_result
from navigraph_slack_bot.settings import SlackBotSettings

_SETTINGS = SlackBotSettings(
    slack_signing_secret="test-secret",
    slack_bot_token="xoxb-test-token",
    default_tenant_id="tenant-acme",
)


def _client(
    handler: Callable[[httpx.Request], httpx.Response], base_url: str = "http://gateway"
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=base_url)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<@U012AB3CD> what is our revenue?", "what is our revenue?"),
        ("<@U012AB3CD>   what is our revenue?", "what is our revenue?"),
        ("<@U012AB3CD>", ""),
        ("no mention at all", "no mention at all"),
    ],
)
def test_strip_mention(raw: str, expected: str) -> None:
    assert strip_mention(raw) == expected


def test_summarize_result_for_each_of_the_three_real_outcomes() -> None:
    assert summarize_result({"outcome": "answered", "narrative": "Revenue grew 12%."}) == "Revenue grew 12%."
    assert (
        summarize_result({"outcome": "needs_clarification", "clarifying_question": "Which region?"})
        == "Which region?"
    )
    assert (
        summarize_result({"outcome": "failed", "failure_stage": "sql_generation", "failure_reason": "bad SQL"})
        == "bad SQL"
    )


def test_summarize_result_falls_back_to_a_generic_message_when_fields_are_missing() -> None:
    assert summarize_result({"outcome": "answered"}) == "No narrative was generated for this answer."
    assert summarize_result({"outcome": "needs_clarification"}) == "Could you clarify your question?"
    assert summarize_result({"outcome": "failed"}) == "Something went wrong (stage: unknown)."


async def test_handle_app_mention_calls_the_gateway_then_posts_the_narrative_back() -> None:
    gateway_requests = []
    slack_requests = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        gateway_requests.append(request)
        return httpx.Response(
            200,
            json={"result": {"outcome": "answered", "session_id": "session-1", "narrative": "Revenue grew 12%."}},
        )

    def slack_handler(request: httpx.Request) -> httpx.Response:
        slack_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    bot = SlackBot(
        settings=_SETTINGS,
        gateway_client=_client(gateway_handler),
        slack_client=_client(slack_handler, base_url="https://slack.com/api"),
    )

    await bot.handle_app_mention(
        {"channel": "C123", "ts": "111.222", "user": "U999", "text": "<@BOT1> what is our revenue?"}
    )

    assert len(gateway_requests) == 1
    sent = json.loads(gateway_requests[0].content)
    assert sent["question"] == "what is our revenue?"
    assert sent["tenant_id"] == "tenant-acme"
    assert sent["user_id"] == "slack:U999"
    assert sent["session_id"] is None

    assert len(slack_requests) == 1
    posted = json.loads(slack_requests[0].content)
    assert posted["channel"] == "C123"
    assert posted["thread_ts"] == "111.222"
    assert posted["text"] == "Revenue grew 12%."
    assert slack_requests[0].headers["Authorization"] == "Bearer xoxb-test-token"


async def test_handle_app_mention_replies_in_the_original_thread_when_one_exists() -> None:
    slack_requests = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": {"outcome": "answered", "narrative": "ok"}})

    def slack_handler(request: httpx.Request) -> httpx.Response:
        slack_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    bot = SlackBot(
        settings=_SETTINGS,
        gateway_client=_client(gateway_handler),
        slack_client=_client(slack_handler, base_url="https://slack.com/api"),
    )

    await bot.handle_app_mention(
        {
            "channel": "C123",
            "ts": "222.333",
            "thread_ts": "111.111",
            "user": "U999",
            "text": "<@BOT1> a follow-up question",
        }
    )

    posted = json.loads(slack_requests[0].content)
    assert posted["thread_ts"] == "111.111"


async def test_handle_app_mention_carries_the_session_id_across_two_calls_in_the_same_thread() -> None:
    gateway_requests = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        gateway_requests.append(request)
        return httpx.Response(200, json={"result": {"outcome": "answered", "session_id": "stable-session", "narrative": "ok"}})

    def slack_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    bot = SlackBot(
        settings=_SETTINGS,
        gateway_client=_client(gateway_handler),
        slack_client=_client(slack_handler, base_url="https://slack.com/api"),
    )

    event = {"channel": "C123", "ts": "111.111", "user": "U999", "text": "<@BOT1> first question"}
    await bot.handle_app_mention(event)
    await bot.handle_app_mention(
        {"channel": "C123", "ts": "222.222", "thread_ts": "111.111", "user": "U999", "text": "<@BOT1> second question"}
    )

    assert len(gateway_requests) == 2
    assert json.loads(gateway_requests[0].content)["session_id"] is None
    assert json.loads(gateway_requests[1].content)["session_id"] == "stable-session"


async def test_handle_app_mention_posts_an_apology_when_the_gateway_is_unreachable() -> None:
    slack_requests = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    def slack_handler(request: httpx.Request) -> httpx.Response:
        slack_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    bot = SlackBot(
        settings=_SETTINGS,
        gateway_client=_client(gateway_handler),
        slack_client=_client(slack_handler, base_url="https://slack.com/api"),
    )

    await bot.handle_app_mention({"channel": "C123", "ts": "111.111", "user": "U999", "text": "<@BOT1> anything"})

    assert len(slack_requests) == 1
    posted = json.loads(slack_requests[0].content)
    assert "couldn't reach NaviGraph" in posted["text"]


async def test_handle_app_mention_posts_an_error_message_for_a_gateway_4xx_response() -> None:
    slack_requests = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "bad request"})

    def slack_handler(request: httpx.Request) -> httpx.Response:
        slack_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    bot = SlackBot(
        settings=_SETTINGS,
        gateway_client=_client(gateway_handler),
        slack_client=_client(slack_handler, base_url="https://slack.com/api"),
    )

    await bot.handle_app_mention({"channel": "C123", "ts": "111.111", "user": "U999", "text": "<@BOT1> anything"})

    posted = json.loads(slack_requests[0].content)
    assert "status 422" in posted["text"]


async def test_handle_app_mention_asks_for_a_real_question_when_the_mention_has_none() -> None:
    slack_requests = []
    gateway_requests = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        gateway_requests.append(request)
        return httpx.Response(200, json={"result": {"outcome": "answered"}})

    def slack_handler(request: httpx.Request) -> httpx.Response:
        slack_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    bot = SlackBot(
        settings=_SETTINGS,
        gateway_client=_client(gateway_handler),
        slack_client=_client(slack_handler, base_url="https://slack.com/api"),
    )

    await bot.handle_app_mention({"channel": "C123", "ts": "111.111", "user": "U999", "text": "<@BOT1>"})

    assert gateway_requests == []
    assert len(slack_requests) == 1
    assert "didn't catch a question" in json.loads(slack_requests[0].content)["text"]
