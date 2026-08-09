"""Real tests for the Slack bot's FastAPI routing: signature verification
wired to real headers, the URL-verification handshake, and background-task
dispatch for a real `app_mention` event.

Uses `fastapi.testclient.TestClient` -- mirrors
`packages/gateway/tests/test_ask.py`'s convention. A real `SlackBot` (not
a mock of it) is injected with `httpx.MockTransport`-backed clients, so
these tests prove `main.py`'s routing/signature logic AND `SlackBot`'s
own logic compose correctly end to end, with only the two real network
boundaries (gateway, Slack Web API) faked.
"""

from __future__ import annotations

import json
import time

import httpx
from fastapi.testclient import TestClient

from navigraph_slack_bot.bot import SlackBot
from navigraph_slack_bot.main import create_app
from navigraph_slack_bot.settings import SlackBotSettings
from navigraph_slack_bot.signature import compute_signature

_SIGNING_SECRET = "test-signing-secret"
_SETTINGS = SlackBotSettings(
    slack_signing_secret=_SIGNING_SECRET, slack_bot_token="xoxb-test", default_tenant_id="tenant-acme"
)


def _signed_headers(body: bytes, *, timestamp: str | None = None) -> dict[str, str]:
    timestamp = timestamp or str(int(time.time()))
    return {
        "X-Slack-Request-Timestamp": timestamp,
        "X-Slack-Signature": compute_signature(signing_secret=_SIGNING_SECRET, timestamp=timestamp, body=body),
        "Content-Type": "application/json",
    }


def _make_app(*, gateway_handler=None, slack_handler=None) -> TestClient:
    gateway_client = httpx.AsyncClient(
        transport=httpx.MockTransport(gateway_handler or (lambda r: httpx.Response(200, json={"result": {}}))),
        base_url="http://gateway",
    )
    slack_client = httpx.AsyncClient(
        transport=httpx.MockTransport(slack_handler or (lambda r: httpx.Response(200, json={"ok": True}))),
        base_url="https://slack.com/api",
    )
    bot = SlackBot(settings=_SETTINGS, gateway_client=gateway_client, slack_client=slack_client)
    app = create_app(settings=_SETTINGS, bot=bot)
    return TestClient(app)


def test_healthz_returns_ok() -> None:
    with _make_app() as client:
        response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_a_request_with_no_signature_headers_is_rejected() -> None:
    with _make_app() as client:
        response = client.post("/slack/events", json={"type": "url_verification", "challenge": "abc"})
    assert response.status_code == 401


def test_a_request_with_a_forged_signature_is_rejected() -> None:
    body = json.dumps({"type": "url_verification", "challenge": "abc"}).encode()
    with _make_app() as client:
        response = client.post(
            "/slack/events",
            content=body,
            headers={
                "X-Slack-Request-Timestamp": str(int(time.time())),
                "X-Slack-Signature": "v0=" + "0" * 64,
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 401


def test_url_verification_challenge_is_echoed_back_when_correctly_signed() -> None:
    body = json.dumps({"type": "url_verification", "challenge": "real-challenge-value"}).encode()
    with _make_app() as client:
        response = client.post("/slack/events", content=body, headers=_signed_headers(body))
    assert response.status_code == 200
    assert response.json() == {"challenge": "real-challenge-value"}


def test_a_correctly_signed_app_mention_event_triggers_a_real_answer_flow() -> None:
    gateway_requests = []
    slack_requests = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        gateway_requests.append(request)
        return httpx.Response(200, json={"result": {"outcome": "answered", "narrative": "Revenue grew 12%."}})

    def slack_handler(request: httpx.Request) -> httpx.Response:
        slack_requests.append(request)
        return httpx.Response(200, json={"ok": True})

    body = json.dumps(
        {
            "type": "event_callback",
            "event": {
                "type": "app_mention",
                "channel": "C123",
                "ts": "111.111",
                "user": "U999",
                "text": "<@BOT1> what is our revenue?",
            },
        }
    ).encode()

    with _make_app(gateway_handler=gateway_handler, slack_handler=slack_handler) as client:
        response = client.post("/slack/events", content=body, headers=_signed_headers(body))

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    # The 200 above proves Slack's own 3-second-ack requirement is met
    # regardless of how long the background work takes; these assert the
    # background task actually ran (TestClient executes background tasks
    # synchronously within the same call, matching Starlette's documented
    # behavior for the in-process test transport).
    assert len(gateway_requests) == 1
    assert json.loads(gateway_requests[0].content)["question"] == "what is our revenue?"
    assert len(slack_requests) == 1
    assert json.loads(slack_requests[0].content)["text"] == "Revenue grew 12%."


def test_a_non_app_mention_event_is_acknowledged_without_calling_the_gateway() -> None:
    gateway_requests = []

    def gateway_handler(request: httpx.Request) -> httpx.Response:
        gateway_requests.append(request)
        return httpx.Response(200, json={"result": {}})

    body = json.dumps(
        {"type": "event_callback", "event": {"type": "reaction_added", "channel": "C123"}}
    ).encode()

    with _make_app(gateway_handler=gateway_handler) as client:
        response = client.post("/slack/events", content=body, headers=_signed_headers(body))

    assert response.status_code == 200
    assert gateway_requests == []


def test_a_non_json_body_is_rejected_as_a_bad_request_not_a_500() -> None:
    body = b"not json"
    with _make_app() as client:
        response = client.post("/slack/events", content=body, headers=_signed_headers(body))
    assert response.status_code == 400
