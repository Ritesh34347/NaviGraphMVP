# Runbook: NaviGraph Slack Bot Setup

`packages/slack_bot` (`navigraph-slack-bot`) is Phase 14.3's Slack
integration: a real Slack Events API service that answers `@NaviGraph`
mentions by calling the gateway's `POST /ask` and posting the result back
to the same channel/thread.

## What it does

- **`GET /healthz`** — liveness probe.
- **`POST /slack/events`** — Slack's Events API subscription URL.
  Verifies every request's real HMAC signature
  (`navigraph_slack_bot.signature.verify_slack_signature`, following
  Slack's documented `v0=<hmac-sha256>` algorithm exactly) before trusting
  anything about it. Handles Slack's one-time `url_verification`
  handshake, and real `app_mention` events: strips the `<@BOTID>` mention
  token, asks NaviGraph the remaining text as a question, and posts the
  answer back in the same thread. Every other real Slack event type is
  acknowledged (Slack expects a 200 either way) but intentionally a
  no-op — this bot only answers direct mentions.

## Setting up a real Slack app

1. Create an app at <https://api.slack.com/apps> ("From scratch").
2. **OAuth & Permissions** → add the `app_mentions:read` and
   `chat:write` bot token scopes → install the app to your workspace →
   copy the **Bot User OAuth Token** (`xoxb-...`).
3. **Basic Information** → copy the **Signing Secret**.
4. **Event Subscriptions** → enable events → set the Request URL to
   `https://<where-this-service-is-reachable>/slack/events` (Slack will
   immediately POST a `url_verification` challenge to it and requires a
   200 with the challenge echoed back before it accepts the URL — this
   service's `/slack/events` route handles that automatically once it's
   actually reachable and its signing secret is configured correctly).
   Subscribe to the `app_mention` bot event.
5. Set `SLACK_SIGNING_SECRET` and `SLACK_BOT_TOKEN` (this package's
   `SlackBotSettings` field names, uppercased) in this service's
   environment, plus `GATEWAY_BASE_URL` if the gateway isn't at the
   docker-compose default (`http://gateway:8000`).
6. Run it: `pip install -e packages/shared && pip install -e packages/slack_bot`,
   then `uvicorn navigraph_slack_bot.main:app --host 0.0.0.0 --port 8002`
   (no Dockerfile/compose entry exists for this yet — see "Still open"
   below).

In Slack, `@NaviGraph what was our revenue last quarter?` should trigger
a real gateway round-trip and post the answer back in the same channel.

## One tenant per Slack app, today

There is no real mapping from a Slack workspace/team to a NaviGraph
`tenant_id` — every question this bot forwards uses
`SlackBotSettings.default_tenant_id` (defaults to the same `navikenz-poc`
demo tenant this repo's other fixtures use), fixed per deployment. A
workspace that needs to serve more than one tenant needs more than one
deployment of this service today, each with its own Slack app and
`DEFAULT_TENANT_ID`.

## What has and hasn't been verified

- **Verified for real, in this sandbox**: 25 unit tests — real HMAC
  signature computation/verification (a genuinely valid signature
  accepted; a forged one, a tampered body, and a replayed stale
  timestamp all rejected), the full FastAPI routing layer via
  `fastapi.testclient.TestClient` (the URL-verification handshake, a
  rejected-unsigned-request path, a real `app_mention` event driving an
  actual `handle_app_mention` call), and `SlackBot`'s own logic (mention
  stripping, per-thread session continuity across two calls, and error
  handling when the gateway is unreachable or returns a 4xx) — all
  against `httpx.MockTransport`-faked gateway/Slack Web API boundaries,
  never a stub of the logic under test itself.
- **NOT verified**: this bot has never sent a real request to Slack's
  actual API, or received a real signed request FROM Slack (no live
  Slack app/workspace in this sandbox). The signing algorithm follows
  Slack's publicly documented specification from training knowledge, not
  a value cross-checked against Slack's own published worked example —
  this sandbox's network egress to `api.slack.com` is blocked, so that
  cross-check could not be performed here. Whoever first wires up a real
  Slack app should treat that first successful `url_verification`
  handshake as the genuine first live proof this algorithm is
  byte-for-byte correct, not assume it from this runbook.
- **Not built**: no Dockerfile/docker-compose service entry (mirrors this
  repo's other services' convention, just not done yet for this one), no
  retry/idempotency handling for Slack's own event-retry behavior (a
  slow/failed ack causes Slack to redeliver the same event; this service
  has no deduplication store, so a redelivered event would be answered
  twice), and the per-thread session map is in-memory only (does not
  survive a restart, does not work across more than one replica).
