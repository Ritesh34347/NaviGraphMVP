"""Core Slack-bot logic: turn a real Slack `app_mention` event into a real
gateway `/ask` call, then post the answer back to the same thread.

Deliberately separated from `main.py`'s FastAPI routing so it can be unit
tested directly (construct a `SlackBot` with fake `httpx.AsyncClient`s,
call `handle_app_mention`, assert on what it posted) without going
through HTTP at all.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from navigraph_slack_bot.settings import SlackBotSettings

_MENTION_PREFIX = re.compile(r"^\s*<@[A-Za-z0-9]+>\s*")


def strip_mention(text: str) -> str:
    """Slack's `app_mention` event text includes the literal `<@BOTID>`
    mention token the user typed to trigger it (e.g. `<@U012AB3CD> what is
    our revenue?`) -- strip just that leading token, not any other `@`
    mention that might appear later in a real question."""

    return _MENTION_PREFIX.sub("", text, count=1).strip()


def summarize_result(result: dict[str, Any]) -> str:
    """Mirrors `web/src/app/chat/ChatClient.tsx`'s `summarizeAssistantText`
    exactly -- same three real outcomes, same fallback text per outcome,
    just the Python side of the same real contract
    (`RequestOrchestratorResult`)."""

    outcome = result.get("outcome")
    if outcome == "needs_clarification":
        return result.get("clarifying_question") or "Could you clarify your question?"
    if outcome == "failed":
        stage = result.get("failure_stage") or "unknown"
        return result.get("failure_reason") or f"Something went wrong (stage: {stage})."
    return result.get("narrative") or "No narrative was generated for this answer."


class SlackBot:
    """Holds the two real HTTP boundaries this service crosses (the
    gateway, Slack's own Web API) plus per-thread NaviGraph session
    continuity, and turns one real Slack event into one real answer."""

    def __init__(
        self,
        *,
        settings: SlackBotSettings,
        gateway_client: httpx.AsyncClient,
        slack_client: httpx.AsyncClient,
    ) -> None:
        self._settings = settings
        self._gateway_client = gateway_client
        self._slack_client = slack_client
        # Keyed by (channel, thread_key) -> NaviGraph session_id. Real, but
        # deliberately in-memory only -- see this package's README/
        # LIMITATIONS.md note: this does not survive a process restart and
        # does not work across more than one replica of this service.
        self._session_by_thread: dict[tuple[str, str], str] = {}

    async def handle_app_mention(self, event: dict[str, Any]) -> None:
        channel = event.get("channel")
        thread_key = event.get("thread_ts") or event.get("ts")
        if not channel or not thread_key:
            return

        question = strip_mention(event.get("text", ""))
        if not question:
            await self._post_message(
                channel=channel,
                thread_ts=thread_key,
                text="I didn't catch a question after the mention -- try `@NaviGraph <your question>`.",
            )
            return

        session_key = (channel, thread_key)
        session_id = self._session_by_thread.get(session_key)
        slack_user = event.get("user", "unknown")

        try:
            response = await self._gateway_client.post(
                "/ask",
                json={
                    "question": question,
                    "tenant_id": self._settings.default_tenant_id,
                    "user_id": f"slack:{slack_user}",
                    "session_id": session_id,
                    "roles": [],
                },
            )
        except httpx.HTTPError as exc:
            await self._post_message(
                channel=channel,
                thread_ts=thread_key,
                text=f"Sorry, I couldn't reach NaviGraph right now ({exc}).",
            )
            return

        if response.status_code >= 400:
            await self._post_message(
                channel=channel,
                thread_ts=thread_key,
                text=f"NaviGraph returned an error (status {response.status_code}).",
            )
            return

        result = response.json().get("result", {})
        new_session_id = result.get("session_id")
        if new_session_id:
            self._session_by_thread[session_key] = new_session_id

        await self._post_message(channel=channel, thread_ts=thread_key, text=summarize_result(result))

    async def _post_message(self, *, channel: str, thread_ts: str, text: str) -> None:
        await self._slack_client.post(
            "/chat.postMessage",
            json={"channel": channel, "thread_ts": thread_ts, "text": text},
            headers={"Authorization": f"Bearer {self._settings.slack_bot_token}"},
        )
