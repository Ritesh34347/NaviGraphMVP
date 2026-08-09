"""NaviGraph Slack bot FastAPI application (Phase 14.3).

Exposes:
  - GET  /healthz        -- liveness probe
  - POST /slack/events    -- Slack's Events API subscription URL

Every real request to `/slack/events` is signature-verified BEFORE its
body is trusted at all (`navigraph_slack_bot.signature
.verify_slack_signature`, over the exact raw bytes Slack signed -- reading
`request.body()` here, not a re-serialized/parsed form, is required for
the HMAC to match). Slack requires this endpoint to acknowledge within 3
seconds, so real work (`SlackBot.handle_app_mention`, which itself calls
the gateway's `/ask` and then Slack's own `chat.postMessage`) runs as a
`BackgroundTask` after the 200 is already on the wire, not before it.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from navigraph_slack_bot.bot import SlackBot
from navigraph_slack_bot.settings import SlackBotSettings, get_slack_bot_settings
from navigraph_slack_bot.signature import verify_slack_signature


def create_app(
    *,
    settings: SlackBotSettings | None = None,
    bot: SlackBot | None = None,
) -> FastAPI:
    """Build the FastAPI app. `settings`/`bot` are injectable so tests can
    supply a `SlackBot` wired to fake `httpx.AsyncClient`s and a known
    `slack_signing_secret`, instead of needing real Slack credentials.

    Mirrors `navigraph_gateway.main`'s `lifespan`-based real-client
    construction: when `bot` is not injected, its two real
    `httpx.AsyncClient`s (gateway, Slack Web API) are built at app
    STARTUP, not at import time, and closed at shutdown.
    """

    resolved_settings = settings or get_slack_bot_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if bot is not None:
            app.state.bot = bot
            yield
            return

        gateway_client = httpx.AsyncClient(
            base_url=resolved_settings.gateway_base_url, timeout=60.0
        )
        slack_client = httpx.AsyncClient(base_url="https://slack.com/api", timeout=10.0)
        app.state.bot = SlackBot(
            settings=resolved_settings, gateway_client=gateway_client, slack_client=slack_client
        )
        try:
            yield
        finally:
            await gateway_client.aclose()
            await slack_client.aclose()

    app = FastAPI(title="navigraph-slack-bot", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.post("/slack/events")
    async def slack_events(request: Request, background_tasks: BackgroundTasks) -> dict:
        raw_body = await request.body()
        timestamp = request.headers.get("X-Slack-Request-Timestamp")
        signature = request.headers.get("X-Slack-Signature")

        if (
            timestamp is None
            or signature is None
            or not verify_slack_signature(
                signing_secret=resolved_settings.slack_signing_secret,
                timestamp=timestamp,
                body=raw_body,
                signature=signature,
            )
        ):
            raise HTTPException(status_code=401, detail="invalid Slack request signature")

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="request body was not valid JSON") from exc

        # Slack's one-time subscription-URL handshake: echo the challenge
        # back verbatim, no event processing involved.
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}

        if payload.get("type") == "event_callback":
            event = payload.get("event", {})
            if event.get("type") == "app_mention":
                background_tasks.add_task(request.app.state.bot.handle_app_mention, event)
            # Every other real Slack event type (message edits, reactions,
            # channel joins, ...) is acknowledged but intentionally a
            # no-op today -- this bot only answers direct @-mentions.

        return {"ok": True}

    return app


app = create_app()
